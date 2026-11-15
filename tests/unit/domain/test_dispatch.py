"""When a technician visit may be booked.

The rule under test: a visit needs positive proof on every point. Anything unproven is a "no"
with its reason, and the customer is asked for consent only when everything else already holds.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from skylink_care.domain.dispatch import (
    VisitEvidence,
    VisitLimits,
    VisitReason,
    decide_visit,
)
from skylink_care.domain.models import NetworkState

NOW = datetime(2026, 11, 15, 6, 30, 0, tzinfo=UTC)
HOLD = timedelta(minutes=10)
LIMITS = VisitLimits(
    hold=HOLD,
    status_max_age=timedelta(seconds=60),
    line_test_max_age=timedelta(minutes=5),
)
FIRST_FAILED = NOW - timedelta(minutes=12)
HOLD_ENDED = FIRST_FAILED + HOLD

# Everything a visit needs: a line that failed, still fails on a test made after the hold, a
# node proven clear after the hold, a customer who said yes, and no visit booked already.
PROVEN = VisitEvidence(
    service_active=True,
    visit_already_booked=False,
    network_state=NetworkState.CONFIRMED_CLEAR,
    network_evidence_at=NOW - timedelta(seconds=10),
    line_result="FAIL",
    line_tested_at=NOW - timedelta(seconds=30),
    first_failed_at=FIRST_FAILED,
    consented=True,
)


def test_a_line_still_failing_after_the_hold_on_a_clear_node_with_consent_gets_a_visit() -> None:
    decision = decide_visit(PROVEN, LIMITS, now=NOW)

    assert decision.allowed
    assert decision.reason is VisitReason.ELIGIBLE
    assert decision.hold_ends_at == HOLD_ENDED


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"service_active": False}, VisitReason.SERVICE_INACTIVE),
        ({"visit_already_booked": True}, VisitReason.VISIT_ALREADY_BOOKED),
        ({"network_state": NetworkState.ACTIVE}, VisitReason.OUTAGE_ACTIVE),
        ({"network_state": NetworkState.UNKNOWN}, VisitReason.NETWORK_UNVERIFIED),
        ({"line_result": "PASS"}, VisitReason.LINE_PASSING),
        ({"line_result": "INCONCLUSIVE"}, VisitReason.LINE_FAULT_NOT_PROVEN),
        ({"line_result": None}, VisitReason.LINE_FAULT_NOT_PROVEN),
        ({"line_tested_at": None}, VisitReason.LINE_FAULT_NOT_PROVEN),
        ({"consented": False}, VisitReason.NO_CONSENT),
    ],
)
def test_one_missing_proof_is_a_refusal_with_its_reason(
    change: dict[str, object], reason: VisitReason
) -> None:
    decision = decide_visit(replace(PROVEN, **change), LIMITS, now=NOW)  # type: ignore[arg-type]

    assert not decision.allowed
    assert decision.reason is reason


def test_during_the_hold_nothing_is_booked_and_the_end_of_the_hold_is_reported() -> None:
    during = replace(
        PROVEN,
        first_failed_at=NOW - timedelta(minutes=4),
        line_tested_at=NOW - timedelta(seconds=30),
    )

    decision = decide_visit(during, LIMITS, now=NOW)

    assert not decision.allowed
    assert decision.reason is VisitReason.HOLD_NOT_OVER
    assert decision.hold_ends_at == NOW + timedelta(minutes=6)


def test_the_hold_ends_exactly_when_its_time_is_up() -> None:
    at_the_end = replace(
        PROVEN, first_failed_at=NOW - HOLD, line_tested_at=NOW, network_evidence_at=NOW
    )

    assert decide_visit(at_the_end, LIMITS, now=NOW).allowed
    one_second_early = NOW - timedelta(seconds=1)
    assert (
        decide_visit(at_the_end, LIMITS, now=one_second_early).reason is VisitReason.HOLD_NOT_OVER
    )


def test_a_failure_seen_only_during_the_hold_needs_a_test_made_after_it() -> None:
    # The line may have come back with the node: only a test after the hold proves a line fault.
    stale = replace(PROVEN, line_tested_at=HOLD_ENDED - timedelta(seconds=1))

    decision = decide_visit(stale, LIMITS, now=NOW)

    assert decision.reason is VisitReason.RETEST_NEEDED


def test_a_failing_test_that_is_too_old_needs_repeating() -> None:
    later = NOW + timedelta(minutes=30)
    old = replace(PROVEN, network_evidence_at=later - timedelta(seconds=5))

    decision = decide_visit(old, LIMITS, now=later)

    assert decision.reason is VisitReason.RETEST_NEEDED


def test_when_the_first_failure_is_not_recorded_the_hold_runs_from_the_test_itself() -> None:
    unrecorded = replace(PROVEN, first_failed_at=None)

    decision = decide_visit(unrecorded, LIMITS, now=NOW)

    assert decision.reason is VisitReason.HOLD_NOT_OVER
    assert decision.hold_ends_at == PROVEN.line_tested_at + HOLD  # type: ignore[operator]


@pytest.mark.parametrize(
    ("evidence_at", "status_max_age"),
    [
        (None, timedelta(seconds=60)),
        (NOW - timedelta(seconds=61), timedelta(seconds=60)),  # older than an answer may be
        # Recent enough, but taken while the hold was still running.
        (HOLD_ENDED - timedelta(seconds=1), timedelta(minutes=3)),
    ],
    ids=["never read", "too old", "from before the hold ended"],
)
def test_the_node_must_be_proven_clear_now_and_after_the_hold(
    evidence_at: datetime | None, status_max_age: timedelta
) -> None:
    limits = replace(LIMITS, status_max_age=status_max_age)

    decision = decide_visit(replace(PROVEN, network_evidence_at=evidence_at), limits, now=NOW)

    assert not decision.allowed
    assert decision.reason is VisitReason.CLEAR_NOT_FRESH


def test_evidence_stamped_in_the_future_proves_nothing() -> None:
    ahead = NOW + timedelta(minutes=1)

    assert (
        decide_visit(replace(PROVEN, network_evidence_at=ahead), LIMITS, now=NOW).reason
        is VisitReason.CLEAR_NOT_FRESH
    )
    assert (
        decide_visit(replace(PROVEN, line_tested_at=ahead), LIMITS, now=NOW).reason
        is VisitReason.RETEST_NEEDED
    )


@pytest.mark.parametrize(
    "change",
    [
        {"service_active": False},
        {"visit_already_booked": True},
        {"network_state": NetworkState.ACTIVE},
        {"network_state": NetworkState.UNKNOWN},
        {"line_result": "PASS"},
        {"line_result": None},
        {"first_failed_at": NOW - timedelta(minutes=4)},
        {"line_tested_at": HOLD_ENDED - timedelta(seconds=1)},
        {"network_evidence_at": None},
    ],
)
def test_consent_is_the_last_thing_asked_for(change: dict[str, object]) -> None:
    """NO_CONSENT means "everything else holds": the customer is never offered a visit that
    could not be booked."""
    not_asked_yet = replace(PROVEN, consented=False, **change)  # type: ignore[arg-type]

    decision = decide_visit(not_asked_yet, LIMITS, now=NOW)

    assert not decision.allowed
    assert decision.reason is not VisitReason.NO_CONSENT


def test_an_outage_outranks_every_fact_about_the_line() -> None:
    during_outage = replace(
        PROVEN,
        network_state=NetworkState.ACTIVE,
        first_failed_at=NOW - timedelta(minutes=1),
        line_tested_at=NOW - timedelta(minutes=1),
    )

    assert decide_visit(during_outage, LIMITS, now=NOW).reason is VisitReason.OUTAGE_ACTIVE


def test_with_no_hold_configured_the_failing_test_itself_is_enough() -> None:
    no_hold = replace(LIMITS, hold=timedelta(0))
    just_failed = replace(PROVEN, first_failed_at=None, line_tested_at=NOW - timedelta(seconds=20))

    assert decide_visit(just_failed, no_hold, now=NOW).allowed


def test_times_without_a_zone_are_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        decide_visit(PROVEN, LIMITS, now=NOW.replace(tzinfo=None))
