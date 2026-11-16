"""Three-state network truth for one node: ACTIVE, CONFIRMED_CLEAR or UNKNOWN.

Two sources of evidence exist:

* the live answer from Network Monitoring for the node (authoritative, but it can time out), and
* the read model fed by the NOC event feed (survives an API outage, but can go stale or fall
  behind, and only ever proves that an outage *exists*).

The asymmetry is deliberate. Either source, when fresh, may prove an outage. Only a fresh live
answer may prove the absence of one, because silence on the feed is not evidence. And even a
fresh live "operational" is not trusted while the node looks suspicious: the NOC declares an
outage minutes after equipment starts alarming and lines start failing, and those minutes are
exactly when customers call. Everything that is not proven is UNKNOWN, which keeps device
guidance and dispatch locked.

This module is pure: callers gather the evidence and pass ``now`` in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from skylink_care.domain.models import NetworkState, NodeMapping, OutageInfo

# A live answer stamped further ahead than this is treated as a clock fault, not as evidence.
MAX_CLOCK_SKEW = timedelta(seconds=5)


class DecisionReason(StrEnum):
    LIVE_OUTAGE = "LIVE_OUTAGE"
    FEED_OUTAGE = "FEED_OUTAGE"
    LIVE_CLEAR = "LIVE_CLEAR"
    MAPPING_UNRESOLVED = "MAPPING_UNRESOLVED"
    SUSPECTED_NODE_FAULT = "SUSPECTED_NODE_FAULT"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    CLEAR_NOT_CONFIRMED = "CLEAR_NOT_CONFIRMED"
    STALE_LAST_KNOWN_OUTAGE = "STALE_LAST_KNOWN_OUTAGE"
    NO_FRESH_EVIDENCE = "NO_FRESH_EVIDENCE"


class EvidenceSource(StrEnum):
    LIVE_API = "live_api"
    EVENT_FEED = "event_feed"
    NONE = "none"


@dataclass(frozen=True)
class LiveStatus:
    """What Network Monitoring said about the node just now, or that it could not be asked."""

    available: bool
    outage: OutageInfo | None = None
    observed_at: datetime | None = None
    critical_alarm: bool = False
    error: str | None = None

    @classmethod
    def in_outage(
        cls, outage: OutageInfo, *, observed_at: datetime, critical_alarm: bool = False
    ) -> LiveStatus:
        if not outage.is_open:
            raise ValueError("a live outage must be open (resolved_at unset)")
        return cls(
            available=True, outage=outage, observed_at=observed_at, critical_alarm=critical_alarm
        )

    @classmethod
    def operational(cls, *, observed_at: datetime, critical_alarm: bool = False) -> LiveStatus:
        return cls(available=True, observed_at=observed_at, critical_alarm=critical_alarm)

    @classmethod
    def unavailable(cls, error: str) -> LiveStatus:
        return cls(available=False, error=error)


@dataclass(frozen=True)
class FeedView:
    """What the event-fed read model holds for the node.

    ``caught_up`` is false while the consumer still has events to apply: a heartbeat only proves
    the feed answered, not that the read model reflects what it said.
    """

    heartbeat_at: datetime | None
    active_outage: OutageInfo | None
    critical_alarm: bool = False
    caught_up: bool = True


@dataclass(frozen=True)
class DecisionLimits:
    status_max_age: timedelta
    feed_max_age: timedelta
    # Lines failing on one node within the window are treated as a node fault when at least
    # this many customers have one, and that many failures among the lines tested there would
    # be a coincidence less often than the chance given, when a line fails for reasons of its
    # own at the rate given (``_lines_point_at_the_node``).
    line_test_cluster_threshold: int
    line_own_fault_rate: float
    line_test_cluster_chance: float


@dataclass(frozen=True)
class NetworkDecision:
    state: NetworkState
    reason: DecisionReason
    source: EvidenceSource
    node_id: str | None
    decided_at: datetime
    outage: OutageInfo | None = None
    last_known_outage: OutageInfo | None = None
    evidence_at: datetime | None = None

    @property
    def allows_device_flow(self) -> bool:
        """Device guidance and dispatch need positive proof of a healthy node."""
        return self.state is NetworkState.CONFIRMED_CLEAR


@dataclass(frozen=True)
class _Evidence:
    """Inputs reduced to what is usable right now."""

    node_id: str
    now: datetime
    live: LiveStatus
    live_fresh: bool
    feed_heartbeat_at: datetime | None
    feed_fresh: bool
    feed_outage: OutageInfo | None
    suspicious: bool

    def decision(
        self,
        state: NetworkState,
        reason: DecisionReason,
        source: EvidenceSource = EvidenceSource.NONE,
        **details: OutageInfo | datetime | None,
    ) -> NetworkDecision:
        return NetworkDecision(
            state=state,
            reason=reason,
            source=source,
            node_id=self.node_id,
            decided_at=self.now,
            **details,  # type: ignore[arg-type]
        )


def decide_network_state(
    *,
    mapping: NodeMapping,
    live: LiveStatus,
    feed: FeedView,
    now: datetime,
    limits: DecisionLimits,
    line_test_failures_on_node: int = 0,
    line_tests_passing_on_node: int = 0,
) -> NetworkDecision:
    """Combine mapping, live status, read model and suspicion into one evidenced decision."""
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone")
    if not mapping.is_resolved or mapping.node_id is None:
        return NetworkDecision(
            state=NetworkState.UNKNOWN,
            reason=DecisionReason.MAPPING_UNRESOLVED,
            source=EvidenceSource.NONE,
            node_id=None,
            decided_at=now,
        )

    live_fresh = live.available and _is_fresh(live.observed_at, now, limits.status_max_age)
    feed_fresh = feed.caught_up and _is_fresh(feed.heartbeat_at, now, limits.feed_max_age)
    open_feed_outage = (
        feed.active_outage if feed.active_outage and feed.active_outage.is_open else None
    )
    alarm = (live_fresh and live.critical_alarm) or (feed_fresh and feed.critical_alarm)
    evidence = _Evidence(
        node_id=mapping.node_id,
        now=now,
        live=live,
        live_fresh=live_fresh,
        feed_heartbeat_at=feed.heartbeat_at,
        feed_fresh=feed_fresh,
        feed_outage=open_feed_outage,
        suspicious=alarm
        or _lines_point_at_the_node(line_test_failures_on_node, line_tests_passing_on_node, limits),
    )
    return _declared_outage(evidence) or _suspected(evidence) or _no_declared_outage(evidence)


def _lines_point_at_the_node(failing: int, passing: int, limits: DecisionLimits) -> bool:
    """Enough customers have a failing line on the node, and more than coincidence explains.

    A count alone is evidence only while few lines are tested, which is how customers' own
    reports arrive: the ones who call are the ones affected. After an outage the platform
    tests every waiting line on the node within minutes, and among a hundred lines a few have
    faults of their own. So the count is set against the lines tested: three failing of three
    is the node, three of a hundred is not, and eight of a hundred is again.
    """
    if failing < limits.line_test_cluster_threshold:
        return False
    by_coincidence = _chance_of_at_least(failing, failing + passing, limits.line_own_fault_rate)
    return by_coincidence < limits.line_test_cluster_chance


def _chance_of_at_least(failing: int, tested: int, rate: float) -> float:
    """How likely it is that at least ``failing`` of ``tested`` lines fail when each fails by
    itself with probability ``rate``: the upper tail of a binomial distribution.

    Summed in logarithms from the lower tail, which holds for any number of lines; the terms
    of the upper tail would underflow long before a node runs out of lines.
    """
    log_fails, log_passes = math.log(rate), math.log1p(-rate)
    fewer = math.fsum(
        math.exp(
            math.lgamma(tested + 1)
            - math.lgamma(count + 1)
            - math.lgamma(tested - count + 1)
            + count * log_fails
            + (tested - count) * log_passes
        )
        for count in range(failing)
    )
    return max(0.0, 1.0 - fewer)


def _declared_outage(ev: _Evidence) -> NetworkDecision | None:
    """ACTIVE when a fresh source reports an open outage; the newer version supplies the ETA."""
    live_outage = ev.live.outage if ev.live_fresh else None
    feed_outage = ev.feed_outage if ev.feed_fresh else None
    if live_outage is not None:
        same_episode = feed_outage is not None and feed_outage.outage_id == live_outage.outage_id
        if same_episode and feed_outage is not None and feed_outage.version > live_outage.version:
            return ev.decision(
                NetworkState.ACTIVE,
                DecisionReason.LIVE_OUTAGE,
                EvidenceSource.EVENT_FEED,
                outage=feed_outage,
                evidence_at=ev.feed_heartbeat_at,
            )
        return ev.decision(
            NetworkState.ACTIVE,
            DecisionReason.LIVE_OUTAGE,
            EvidenceSource.LIVE_API,
            outage=live_outage,
            evidence_at=ev.live.observed_at,
        )
    if feed_outage is not None and not ev.live_fresh:
        return ev.decision(
            NetworkState.ACTIVE,
            DecisionReason.FEED_OUTAGE,
            EvidenceSource.EVENT_FEED,
            outage=feed_outage,
            evidence_at=ev.feed_heartbeat_at,
        )
    return None


def _suspected(ev: _Evidence) -> NetworkDecision | None:
    if not ev.suspicious:
        return None
    return ev.decision(
        NetworkState.UNKNOWN,
        DecisionReason.SUSPECTED_NODE_FAULT,
        evidence_at=ev.live.observed_at if ev.live_fresh else ev.feed_heartbeat_at,
    )


def _no_declared_outage(ev: _Evidence) -> NetworkDecision:
    if ev.live_fresh:
        if ev.feed_fresh and ev.feed_outage is not None:
            # Two fresh sources disagree. Neither an all-clear nor a firm outage can be claimed.
            return ev.decision(
                NetworkState.UNKNOWN,
                DecisionReason.CONFLICTING_EVIDENCE,
                last_known_outage=ev.feed_outage,
                evidence_at=ev.live.observed_at,
            )
        return ev.decision(
            NetworkState.CONFIRMED_CLEAR,
            DecisionReason.LIVE_CLEAR,
            EvidenceSource.LIVE_API,
            evidence_at=ev.live.observed_at,
        )
    if ev.feed_fresh:
        # The feed is alive and silent about this node. Silence is not a confirmed all-clear.
        return ev.decision(
            NetworkState.UNKNOWN,
            DecisionReason.CLEAR_NOT_CONFIRMED,
            evidence_at=ev.feed_heartbeat_at,
        )
    last_known = ev.feed_outage or ev.live.outage
    if last_known is not None:
        return ev.decision(
            NetworkState.UNKNOWN,
            DecisionReason.STALE_LAST_KNOWN_OUTAGE,
            last_known_outage=last_known,
            evidence_at=ev.feed_heartbeat_at if ev.feed_outage else ev.live.observed_at,
        )
    return ev.decision(NetworkState.UNKNOWN, DecisionReason.NO_FRESH_EVIDENCE)


def _is_fresh(observed_at: datetime | None, now: datetime, max_age: timedelta) -> bool:
    if observed_at is None:
        return False
    age = now - observed_at
    return -MAX_CLOCK_SKEW <= age <= max_age
