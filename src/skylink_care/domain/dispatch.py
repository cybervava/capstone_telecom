"""When a technician visit may be booked.

A visit is the most expensive thing this platform can arrange and the one most often arranged
for nothing: a line that fails in the minutes before the NOC declares an outage is repaired by
the NOC, not by a technician at the door. So a visit needs positive proof on every point:

* the service is active and has no visit booked already;
* the node is CONFIRMED_CLEAR, on a live answer that is fresh now and was taken after the hold;
* the customer's own line failed, the hold (the NOC detection window) has passed since the
  first failure, and a recent test made after the hold still fails;
* the customer has said yes to a visit.

Anything not proven is a "no" with its reason. Consent is checked last, so ``NO_CONSENT`` means
that everything else holds and the customer may be asked. The same rule is applied twice: when
a visit is proposed, and by the executor immediately before it books, on evidence it reads
itself.

This module is pure: callers gather the evidence and pass ``now`` in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from skylink_care.domain.models import NetworkState
from skylink_care.domain.network import MAX_CLOCK_SKEW

LINE_FAIL = "FAIL"
LINE_PASS = "PASS"  # noqa: S105 - a test result, not a password


class VisitReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    SERVICE_INACTIVE = "SERVICE_INACTIVE"
    VISIT_ALREADY_BOOKED = "VISIT_ALREADY_BOOKED"
    OUTAGE_ACTIVE = "OUTAGE_ACTIVE"
    NETWORK_UNVERIFIED = "NETWORK_UNVERIFIED"
    LINE_PASSING = "LINE_PASSING"
    LINE_FAULT_NOT_PROVEN = "LINE_FAULT_NOT_PROVEN"
    HOLD_NOT_OVER = "HOLD_NOT_OVER"
    RETEST_NEEDED = "RETEST_NEEDED"
    CLEAR_NOT_FRESH = "CLEAR_NOT_FRESH"
    NO_CONSENT = "NO_CONSENT"


@dataclass(frozen=True)
class VisitEvidence:
    service_active: bool
    visit_already_booked: bool
    network_state: NetworkState
    # When the live answer behind ``network_state`` was observed.
    network_evidence_at: datetime | None
    # The latest test of the customer's own line.
    line_result: str | None
    line_tested_at: datetime | None
    # When the line was first seen failing: the start of the hold.
    first_failed_at: datetime | None
    consented: bool


@dataclass(frozen=True)
class VisitLimits:
    # The NOC detection window: how long after the first failed test nothing is booked.
    hold: timedelta
    status_max_age: timedelta
    line_test_max_age: timedelta


@dataclass(frozen=True)
class VisitDecision:
    allowed: bool
    reason: VisitReason
    # Known once the line has been seen failing.
    hold_ends_at: datetime | None = None


def decide_visit(evidence: VisitEvidence, limits: VisitLimits, *, now: datetime) -> VisitDecision:
    """May a visit be booked for this service right now, and if not, what is missing."""
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone")
    if not evidence.service_active:
        return _no(VisitReason.SERVICE_INACTIVE)
    if evidence.visit_already_booked:
        return _no(VisitReason.VISIT_ALREADY_BOOKED)
    if evidence.network_state is NetworkState.ACTIVE:
        return _no(VisitReason.OUTAGE_ACTIVE)
    if evidence.network_state is not NetworkState.CONFIRMED_CLEAR:
        return _no(VisitReason.NETWORK_UNVERIFIED)
    if evidence.line_result == LINE_PASS:
        return _no(VisitReason.LINE_PASSING)
    tested_at = evidence.line_tested_at
    if evidence.line_result != LINE_FAIL or tested_at is None:
        return _no(VisitReason.LINE_FAULT_NOT_PROVEN)

    hold_ends_at = (evidence.first_failed_at or tested_at) + limits.hold
    if now < hold_ends_at:
        return _no(VisitReason.HOLD_NOT_OVER, hold_ends_at)
    if not _usable(tested_at, since=hold_ends_at, max_age=limits.line_test_max_age, now=now):
        # The line may have come back with the node: only a test after the hold proves a fault.
        return _no(VisitReason.RETEST_NEEDED, hold_ends_at)
    clear_at = evidence.network_evidence_at
    if clear_at is None or not _usable(
        clear_at, since=hold_ends_at, max_age=limits.status_max_age, now=now
    ):
        return _no(VisitReason.CLEAR_NOT_FRESH, hold_ends_at)
    if not evidence.consented:
        return _no(VisitReason.NO_CONSENT, hold_ends_at)
    return VisitDecision(True, VisitReason.ELIGIBLE, hold_ends_at)


def _usable(at: datetime, *, since: datetime, max_age: timedelta, now: datetime) -> bool:
    """Observed after the hold, recently enough, and not stamped ahead of the clock."""
    return since <= at <= now + MAX_CLOCK_SKEW and now - at <= max_age


def _no(reason: VisitReason, hold_ends_at: datetime | None = None) -> VisitDecision:
    return VisitDecision(False, reason, hold_ends_at)
