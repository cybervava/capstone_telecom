"""The three-state network decision.

The rule under test: nothing but positive, fresh evidence may produce CONFIRMED_CLEAR. A timeout,
an empty read model or an old answer is UNKNOWN.
"""

from datetime import UTC, datetime, timedelta

import pytest

from skylink_care.domain.models import MappingStatus, NetworkState, NodeMapping, OutageInfo
from skylink_care.domain.network import (
    DecisionLimits,
    DecisionReason,
    EvidenceSource,
    FeedView,
    LiveStatus,
    NetworkDecision,
    decide_network_state,
)

NOW = datetime(2026, 11, 15, 6, 0, 0, tzinfo=UTC)
LIMITS = DecisionLimits(
    status_max_age=timedelta(seconds=60),
    feed_max_age=timedelta(seconds=90),
    line_test_cluster_threshold=3,
)
NODE = "BLR-OLT-0012"

MAPPED = NodeMapping(MappingStatus.RESOLVED, NODE, (NODE,), "2026-11-01")
OUTAGE = OutageInfo(
    outage_id="OUT-20261115-0003",
    version=2,
    cause="FIBRE_CUT",
    planned=False,
    started_at=NOW - timedelta(minutes=50),
    eta=NOW + timedelta(hours=2),
    eta_source="field_update",
    resolved_at=None,
)

FRESH = NOW - timedelta(seconds=5)
LIVE_OUTAGE = LiveStatus.in_outage(OUTAGE, observed_at=FRESH)
LIVE_CLEAR = LiveStatus.operational(observed_at=FRESH)
LIVE_DOWN = LiveStatus.unavailable("TIMEOUT")

FEED_FRESH_QUIET = FeedView(heartbeat_at=NOW - timedelta(seconds=10), active_outage=None)
FEED_FRESH_OUTAGE = FeedView(heartbeat_at=NOW - timedelta(seconds=10), active_outage=OUTAGE)
FEED_STALE_QUIET = FeedView(heartbeat_at=NOW - timedelta(minutes=10), active_outage=None)
FEED_STALE_OUTAGE = FeedView(heartbeat_at=NOW - timedelta(minutes=10), active_outage=OUTAGE)
FEED_NEVER = FeedView(heartbeat_at=None, active_outage=None)


def decide(
    mapping: NodeMapping, live: LiveStatus, feed: FeedView, *, line_test_failures: int = 0
) -> NetworkDecision:
    return decide_network_state(
        mapping=mapping,
        live=live,
        feed=feed,
        now=NOW,
        limits=LIMITS,
        line_test_failures_on_node=line_test_failures,
    )


# --- ACTIVE --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feed", [FEED_FRESH_QUIET, FEED_FRESH_OUTAGE, FEED_STALE_QUIET, FEED_NEVER]
)
def test_live_outage_is_active_whatever_the_feed_says(feed: FeedView) -> None:
    decision = decide(MAPPED, LIVE_OUTAGE, feed)

    assert decision.state is NetworkState.ACTIVE
    assert decision.reason is DecisionReason.LIVE_OUTAGE
    assert decision.source is EvidenceSource.LIVE_API
    assert decision.outage == OUTAGE
    assert decision.evidence_at == FRESH


def test_fresh_feed_outage_is_active_when_the_live_api_is_down() -> None:
    decision = decide(MAPPED, LIVE_DOWN, FEED_FRESH_OUTAGE)

    assert decision.state is NetworkState.ACTIVE
    assert decision.reason is DecisionReason.FEED_OUTAGE
    assert decision.source is EvidenceSource.EVENT_FEED
    assert decision.outage == OUTAGE
    assert decision.evidence_at == FEED_FRESH_OUTAGE.heartbeat_at


# --- CONFIRMED_CLEAR -----------------------------------------------------------------------------


@pytest.mark.parametrize("feed", [FEED_FRESH_QUIET, FEED_STALE_QUIET, FEED_NEVER])
def test_fresh_live_clear_with_no_contradiction_is_confirmed_clear(feed: FeedView) -> None:
    decision = decide(MAPPED, LIVE_CLEAR, feed)

    assert decision.state is NetworkState.CONFIRMED_CLEAR
    assert decision.reason is DecisionReason.LIVE_CLEAR
    assert decision.source is EvidenceSource.LIVE_API
    assert decision.outage is None


def test_a_stale_feed_outage_does_not_contradict_a_fresh_live_clear() -> None:
    # The feed has not been heard from for ten minutes; the live answer is five seconds old.
    decision = decide(MAPPED, LIVE_CLEAR, FEED_STALE_OUTAGE)

    assert decision.state is NetworkState.CONFIRMED_CLEAR


# --- UNKNOWN: the cases that must never become an all-clear --------------------------------------


def test_live_api_down_and_quiet_feed_is_unknown_not_clear() -> None:
    decision = decide(MAPPED, LIVE_DOWN, FEED_FRESH_QUIET)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.CLEAR_NOT_CONFIRMED
    assert decision.outage is None


def test_live_api_down_and_stale_feed_reports_the_last_known_outage() -> None:
    decision = decide(MAPPED, LIVE_DOWN, FEED_STALE_OUTAGE)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.STALE_LAST_KNOWN_OUTAGE
    assert decision.outage is None
    assert decision.last_known_outage == OUTAGE
    assert decision.evidence_at == FEED_STALE_OUTAGE.heartbeat_at


@pytest.mark.parametrize("feed", [FEED_STALE_QUIET, FEED_NEVER])
def test_no_live_answer_and_no_fresh_feed_is_unknown(feed: FeedView) -> None:
    decision = decide(MAPPED, LIVE_DOWN, feed)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.NO_FRESH_EVIDENCE
    assert decision.source is EvidenceSource.NONE


def test_live_clear_against_a_fresh_feed_outage_is_unknown() -> None:
    decision = decide(MAPPED, LIVE_CLEAR, FEED_FRESH_OUTAGE)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.CONFLICTING_EVIDENCE
    assert decision.last_known_outage == OUTAGE


def test_an_old_live_clear_is_not_evidence() -> None:
    old = LiveStatus.operational(observed_at=NOW - timedelta(seconds=61))

    decision = decide(MAPPED, old, FEED_FRESH_QUIET)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.CLEAR_NOT_CONFIRMED


def test_a_live_clear_exactly_at_the_age_limit_still_counts() -> None:
    edge = LiveStatus.operational(observed_at=NOW - timedelta(seconds=60))

    assert decide(MAPPED, edge, FEED_FRESH_QUIET).state is NetworkState.CONFIRMED_CLEAR


def test_a_live_answer_dated_in_the_future_is_not_evidence() -> None:
    skewed = LiveStatus.operational(observed_at=NOW + timedelta(minutes=5))

    assert decide(MAPPED, skewed, FEED_FRESH_QUIET).state is NetworkState.UNKNOWN


def test_an_old_live_outage_falls_back_to_the_feed() -> None:
    old = LiveStatus.in_outage(OUTAGE, observed_at=NOW - timedelta(minutes=5))

    decision = decide(MAPPED, old, FEED_FRESH_OUTAGE)

    assert decision.state is NetworkState.ACTIVE
    assert decision.source is EvidenceSource.EVENT_FEED


def test_feed_heartbeat_exactly_at_the_age_limit_still_counts() -> None:
    edge = FeedView(heartbeat_at=NOW - timedelta(seconds=90), active_outage=OUTAGE)

    assert decide(MAPPED, LIVE_DOWN, edge).state is NetworkState.ACTIVE


def test_feed_heartbeat_one_second_past_the_limit_is_stale() -> None:
    late = FeedView(heartbeat_at=NOW - timedelta(seconds=91), active_outage=OUTAGE)

    assert decide(MAPPED, LIVE_DOWN, late).state is NetworkState.UNKNOWN


# --- mapping -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mapping",
    [
        NodeMapping(MappingStatus.MISSING, None, (), None),
        NodeMapping(MappingStatus.AMBIGUOUS, None, ("BLR-OLT-0012", "BLR-OLT-0044"), "2026-11-01"),
        NodeMapping(MappingStatus.STALE, None, ("BLR-OLT-0009",), "2026-11-01"),
    ],
    ids=["missing", "ambiguous", "stale"],
)
@pytest.mark.parametrize(
    "live", [LIVE_OUTAGE, LIVE_CLEAR, LIVE_DOWN], ids=["outage", "clear", "down"]
)
def test_unresolved_mapping_is_unknown_whatever_the_network_says(
    mapping: NodeMapping, live: LiveStatus
) -> None:
    decision = decide(mapping, live, FEED_FRESH_QUIET)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.MAPPING_UNRESOLVED
    assert decision.node_id is None


# --- invariants ----------------------------------------------------------------------------------


def test_only_confirmed_clear_unlocks_the_device_flow() -> None:
    assert decide(MAPPED, LIVE_CLEAR, FEED_FRESH_QUIET).allows_device_flow
    assert not decide(MAPPED, LIVE_OUTAGE, FEED_FRESH_QUIET).allows_device_flow
    assert not decide(MAPPED, LIVE_DOWN, FEED_FRESH_QUIET).allows_device_flow


def test_decision_records_when_it_was_made_and_for_which_node() -> None:
    decision = decide(MAPPED, LIVE_CLEAR, FEED_FRESH_QUIET)

    assert decision.decided_at == NOW
    assert decision.node_id == NODE


def test_resolved_outage_cannot_be_reported_as_a_live_outage() -> None:
    resolved = OutageInfo(
        outage_id="OUT-1",
        version=3,
        cause="FIBRE_CUT",
        planned=False,
        started_at=NOW - timedelta(hours=3),
        eta=None,
        eta_source=None,
        resolved_at=NOW - timedelta(hours=1),
    )

    with pytest.raises(ValueError, match="open"):
        LiveStatus.in_outage(resolved, observed_at=FRESH)


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        decide_network_state(
            mapping=MAPPED,
            live=LIVE_CLEAR,
            feed=FEED_FRESH_QUIET,
            now=datetime(2026, 11, 15, 6, 0),  # noqa: DTZ001 - the point of the test
            limits=LIMITS,
        )


def test_an_old_live_outage_is_kept_as_last_known_when_nothing_is_fresh() -> None:
    old = LiveStatus.in_outage(OUTAGE, observed_at=NOW - timedelta(minutes=5))

    decision = decide(MAPPED, old, FEED_NEVER)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.STALE_LAST_KNOWN_OUTAGE
    assert decision.last_known_outage == OUTAGE
    assert decision.evidence_at == old.observed_at


# --- suspicion: the minutes before the NOC declares an outage ------------------------------------


def test_live_clear_with_a_critical_alarm_is_suspected_not_clear() -> None:
    alarmed = LiveStatus.operational(observed_at=FRESH, critical_alarm=True)

    decision = decide(MAPPED, alarmed, FEED_FRESH_QUIET)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.SUSPECTED_NODE_FAULT
    assert not decision.allows_device_flow


def test_a_critical_alarm_seen_only_on_a_fresh_feed_is_also_suspicious() -> None:
    feed = FeedView(
        heartbeat_at=NOW - timedelta(seconds=10), active_outage=None, critical_alarm=True
    )

    decision = decide(MAPPED, LIVE_CLEAR, feed)

    assert decision.reason is DecisionReason.SUSPECTED_NODE_FAULT


def test_an_alarm_on_a_stale_feed_is_not_evidence_against_a_fresh_live_clear() -> None:
    feed = FeedView(
        heartbeat_at=NOW - timedelta(minutes=10), active_outage=None, critical_alarm=True
    )

    assert decide(MAPPED, LIVE_CLEAR, feed).state is NetworkState.CONFIRMED_CLEAR


def test_a_cluster_of_failed_line_tests_on_the_node_is_suspicious() -> None:
    decision = decide(MAPPED, LIVE_CLEAR, FEED_FRESH_QUIET, line_test_failures=3)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.SUSPECTED_NODE_FAULT


def test_fewer_failed_line_tests_than_the_threshold_do_not_block() -> None:
    decision = decide(MAPPED, LIVE_CLEAR, FEED_FRESH_QUIET, line_test_failures=2)

    assert decision.state is NetworkState.CONFIRMED_CLEAR


def test_suspicion_is_reported_even_when_the_live_api_is_down() -> None:
    feed = FeedView(
        heartbeat_at=NOW - timedelta(seconds=10), active_outage=None, critical_alarm=True
    )

    decision = decide(MAPPED, LIVE_DOWN, feed)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.SUSPECTED_NODE_FAULT


def test_a_declared_outage_outranks_suspicion() -> None:
    alarmed = LiveStatus.in_outage(OUTAGE, observed_at=FRESH, critical_alarm=True)

    decision = decide(MAPPED, alarmed, FEED_FRESH_QUIET, line_test_failures=9)

    assert decision.state is NetworkState.ACTIVE


# --- a feed that answers but has not caught up is not current -------------------------------------


def test_a_lagging_read_model_cannot_prove_an_outage() -> None:
    lagging = FeedView(
        heartbeat_at=NOW - timedelta(seconds=5), active_outage=OUTAGE, caught_up=False
    )

    decision = decide(MAPPED, LIVE_DOWN, lagging)

    assert decision.state is NetworkState.UNKNOWN
    assert decision.reason is DecisionReason.STALE_LAST_KNOWN_OUTAGE


def test_a_lagging_read_model_does_not_contradict_a_fresh_live_clear() -> None:
    lagging = FeedView(
        heartbeat_at=NOW - timedelta(seconds=5), active_outage=OUTAGE, caught_up=False
    )

    assert decide(MAPPED, LIVE_CLEAR, lagging).state is NetworkState.CONFIRMED_CLEAR


# --- both sources know the outage: the newer version wins -----------------------------------------


def test_newer_outage_version_from_the_feed_is_used_for_the_eta() -> None:
    newer = OutageInfo(
        outage_id=OUTAGE.outage_id,
        version=OUTAGE.version + 1,
        cause=OUTAGE.cause,
        planned=False,
        started_at=OUTAGE.started_at,
        eta=NOW + timedelta(minutes=30),
        eta_source="field_update",
        resolved_at=None,
    )
    feed = FeedView(heartbeat_at=NOW - timedelta(seconds=10), active_outage=newer)

    decision = decide(MAPPED, LIVE_OUTAGE, feed)

    assert decision.state is NetworkState.ACTIVE
    assert decision.outage == newer
    assert decision.source is EvidenceSource.EVENT_FEED


def test_a_different_outage_on_the_feed_does_not_replace_the_live_one() -> None:
    other = OutageInfo(
        outage_id="OUT-20261115-0099",
        version=9,
        cause="POWER_FAILURE",
        planned=False,
        started_at=NOW - timedelta(minutes=5),
        eta=None,
        eta_source=None,
        resolved_at=None,
    )
    feed = FeedView(heartbeat_at=NOW - timedelta(seconds=10), active_outage=other)

    decision = decide(MAPPED, LIVE_OUTAGE, feed)

    assert decision.outage == OUTAGE
    assert decision.source is EvidenceSource.LIVE_API
