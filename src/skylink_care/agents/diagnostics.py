"""Network Diagnostics Agent: find the customer's node and decide what is known about it.

It reads the mapping, asks Network Monitoring for the node's status and, when no outage is known
on the node, runs a remote line test. It changes nothing in any enterprise system. The decision
itself is made by ``domain.network.decide_network_state``; this agent only gathers the evidence,
so the rule that a failed call is never an all-clear lives in one tested place.

A line test serves three purposes here:

* it names the node that really serves the line, which repairs a missing, ambiguous or stale
  address mapping for this contact;
* its result decides what may be done for this customer once the node is confirmed clear;
* the results on one node, taken together, expose a node fault before the NOC declares it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from skylink_care.adapters.convert import to_live_status
from skylink_care.adapters.models import NodeStatusDto
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.adapters.shared import NetworkSource
from skylink_care.db.line_tests import LineTest, LineTestRepository
from skylink_care.db.reference import ACTIVE, ReferenceRepository
from skylink_care.domain.models import MappingStatus, NetworkState, NodeMapping, ServiceType
from skylink_care.domain.network import (
    DecisionLimits,
    FeedView,
    LiveStatus,
    NetworkDecision,
    decide_network_state,
)
from skylink_care.observability.audit import Auditor

ACTOR = "agent:diagnostics"
_NO_FEED = FeedView(heartbeat_at=None, active_outage=None)
_NOT_ASKED = LiveStatus.unavailable("NOT_ASKED")


class FeedViewSource(Protocol):
    async def view(self, node_id: str, service_type: str) -> FeedView: ...


@dataclass(frozen=True)
class LineTestPolicy:
    # Lines failing on one node are counted over this window.
    cluster_window: timedelta
    # A line is not tested again sooner than this, unless a fresh result is asked for.
    retest_after: timedelta


@dataclass(frozen=True)
class Subject:
    """Whose line is being diagnosed. The contact is absent when nobody is waiting on it."""

    contact_id: UUID | None
    service_id: str
    service_type: str
    address_id: str


@dataclass(frozen=True)
class Diagnosis:
    mapping: NodeMapping
    decision: NetworkDecision
    line_test: LineTest | None = None
    failing_lines_on_node: int = 0


class _Probe:
    """The line test of one diagnosis: run at most once, whoever asks first. It also carries
    what the diagnosis was told about its reads: the audit trail, and whether a status read
    may be shared."""

    def __init__(
        self,
        test: Callable[[], Awaitable[LineTest | None]],
        audit: Auditor,
        *,
        share_status: bool = True,
    ) -> None:
        self._test = test
        self.audit = audit
        self.share_status = share_status
        self.ran = False
        self.result: LineTest | None = None

    async def line(self) -> LineTest | None:
        if not self.ran:
            self.ran = True
            self.result = await self._test()
        return self.result


class DiagnosticsAgent:
    def __init__(
        self,
        network: NetworkSource,
        reference: ReferenceRepository,
        feed: FeedViewSource,
        line_tests: LineTestRepository,
        limits: DecisionLimits,
        policy: LineTestPolicy,
        now: Callable[[], datetime],
        *,
        shared: NetworkSource | None = None,
    ) -> None:
        self._network = network
        # Status reads that other diagnoses of the same node may share (``adapters.shared``).
        self._shared = shared
        self._reference = reference
        self._feed = feed
        self._line_tests = line_tests
        self._limits = limits
        self._policy = policy
        self._now = now

    async def run(
        self,
        subject: Subject,
        *,
        audit: Auditor,
        fresh_line_test: bool = False,
        own_status_read: bool = False,
    ) -> Diagnosis:
        """Gather the evidence for one line and decide the network state of its node.

        ``fresh_line_test`` asks for a new test even if the line was tested moments ago: used
        when a decision is about to rest on whether a fault is still there.
        ``own_status_read`` keeps this diagnosis from sharing a status read that is already on
        its way: used when the turn follows news from the feed, and must see the node as it
        is after that news.
        """
        probe = _Probe(
            lambda: self._test_line(subject, audit, fresh=fresh_line_test),
            audit,
            share_status=not own_status_read,
        )
        mapping = await self._reference.resolve_mapping(subject.address_id, subject.service_type)
        if not mapping.is_resolved:
            # The mapping cannot say which node serves this address. The line itself can.
            mapping = await self._follow_line(mapping, await probe.line())
        live, feed, mapping = await self._observe(subject, mapping, probe)
        decision, failing = await self._decide(mapping, live, feed)
        if not probe.ran and mapping.is_resolved and decision.state is not NetworkState.ACTIVE:
            # No outage is proven on the node, so this line's own state is worth knowing. The
            # test also checks the mapping: a line served by a different node than the mapping
            # says is a wrong mapping, however confident the mapping looked.
            followed = await self._follow_line(mapping, await probe.line())
            if followed.node_id != mapping.node_id:
                live, feed, mapping = await self._observe(subject, followed, probe)
            decision, failing = await self._decide(mapping, live, feed)
        await self._record(subject, audit, mapping, live, feed, probe.result, failing, decision)
        return Diagnosis(mapping, decision, probe.result, failing)

    async def node_state(
        self, node_id: str, service_type: str, *, audit: Auditor
    ) -> NetworkDecision:
        """The network state of one known node, on evidence read now and with no line tested.

        For whoever is about to act on an earlier diagnosis and must not act on a stale one.
        A status that cannot be read, or that is not even asked for because the API is being
        rested, makes the state unknown, and the caller declines to act. It does not wait and
        try again: whether the customer's choice still stands is for the conversation to say,
        which looks again and proposes the action afresh if it does.
        """
        mapping = NodeMapping(MappingStatus.RESOLVED, node_id, (node_id,), None)
        live = to_live_status(await self._status(node_id, audit))
        feed = await self._feed.view(node_id, service_type)
        decision, _ = await self._decide(mapping, live, feed)
        return decision

    async def _observe(
        self, subject: Subject, mapping: NodeMapping, probe: _Probe
    ) -> tuple[LiveStatus, FeedView, NodeMapping]:
        """Live status and feed view of the mapped node, following the line if the node is gone."""
        if not mapping.is_resolved or mapping.node_id is None:
            return _NOT_ASKED, _NO_FEED, mapping
        status = await self._status(mapping.node_id, probe.audit, share=probe.share_status)
        if isinstance(status, Err) and status.kind is ErrorKind.GONE:
            # The mapping points at retired equipment: a stale mapping, not a healthy node.
            stale = replace(mapping, status=MappingStatus.STALE, node_id=None)
            followed = stale if probe.ran else await self._follow_line(stale, await probe.line())
            if followed.node_id is None or followed.node_id == mapping.node_id:
                return _NOT_ASKED, _NO_FEED, stale
            mapping = followed
            status = await self._status(followed.node_id, probe.audit, share=probe.share_status)
            if isinstance(status, Err) and status.kind is ErrorKind.GONE:
                return _NOT_ASKED, _NO_FEED, stale
        node_id = mapping.node_id or ""
        feed = await self._feed.view(node_id, subject.service_type)
        return to_live_status(status), feed, mapping

    async def _decide(
        self, mapping: NodeMapping, live: LiveStatus, feed: FeedView
    ) -> tuple[NetworkDecision, int]:
        failing = 0
        if mapping.is_resolved and mapping.node_id is not None:
            failing = await self._line_tests.failing_lines_on_node(
                mapping.node_id, since=self._now() - self._policy.cluster_window
            )
        decision = decide_network_state(
            mapping=mapping,
            live=live,
            feed=feed,
            now=self._now(),
            limits=self._limits,
            line_test_failures_on_node=failing,
        )
        return decision, failing

    async def _follow_line(self, mapping: NodeMapping, line: LineTest | None) -> NodeMapping:
        """The mapping corrected to the node the line test named, if that node can be trusted.

        The test's answer is another system's output: a node the reference data does not know,
        or knows as retired, repairs nothing.
        """
        if line is None or line.node_id == mapping.node_id:
            return mapping
        node = await self._reference.get_node(line.node_id)
        if node is None or node.status != ACTIVE:
            return mapping
        return replace(mapping, status=MappingStatus.REPAIRED, node_id=line.node_id)

    async def _record(
        self,
        subject: Subject,
        audit: Auditor,
        mapping: NodeMapping,
        live: LiveStatus,
        feed: FeedView,
        line: LineTest | None,
        failing: int,
        decision: NetworkDecision,
    ) -> None:
        await audit.record(
            actor=ACTOR,
            step="diagnostics.decide",
            outcome=decision.state.value,
            inputs={
                "address_id": subject.address_id,
                "service_type": subject.service_type,
                "mapping_status": mapping.status.value,
                "map_version": mapping.map_version,
                "live_available": live.available,
                "live_error": live.error,
                "feed_heartbeat_at": _iso(feed.heartbeat_at),
                "line_test": line.result if line else None,
                "line_served_by": line.node_id if line else None,
                "failing_lines_on_node": failing,
            },
            outputs={
                "state": decision.state.value,
                "reason": decision.reason.value,
                "source": decision.source.value,
                "node_id": decision.node_id,
                "outage_id": decision.outage.outage_id if decision.outage else None,
                "evidence_at": _iso(decision.evidence_at),
            },
        )

    async def _status(
        self, node_id: str, audit: Auditor, *, share: bool = False
    ) -> Result[NodeStatusDto]:
        source = self._shared if share and self._shared is not None else self._network
        return await audit.tool(
            actor=ACTOR,
            tool="network.node_status",
            inputs={"node_id": node_id},
            call=source.node_status(node_id, correlation_id=audit.correlation_id),
        )

    async def _test_line(self, subject: Subject, audit: Auditor, *, fresh: bool) -> LineTest | None:
        """Run a line test, or reuse a very recent one. None when no result could be had."""
        if subject.service_type != ServiceType.BROADBAND.value:
            return None  # a mobile connection has no line that can be tested from here
        if not fresh and subject.contact_id is not None:
            # The limit protects the equipment from one conversation re-testing it every turn.
            # A new complaint is new information and gets a test of its own.
            recent = await self._line_tests.latest_for_contact(
                subject.contact_id,
                subject.service_id,
                since=self._now() - self._policy.retest_after,
            )
            if recent is not None:
                return recent
        result = await audit.tool(
            actor=ACTOR,
            tool="network.line_test",
            inputs={"service_id": subject.service_id},
            call=self._network.line_test(subject.service_id, correlation_id=audit.correlation_id),
        )
        if not isinstance(result, Ok):
            return None  # recorded by the audit wrapper; a failed test is no evidence
        dto = result.value
        test = LineTest(dto.test_id, dto.service_id, dto.node_id, dto.result, dto.tested_at)
        await self._line_tests.record(test, contact_id=subject.contact_id)
        return test


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
