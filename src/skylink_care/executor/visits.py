"""The visit booking: the last checks before a technician is sent.

A visit is proposed when a line still fails on a healthy node after the hold and the customer
has chosen a slot. Between that moment and this one the node may have gone into outage, or
another conversation may have booked a visit for the same service. So the rule that allowed
the proposal (``domain.dispatch``) is applied again here, on evidence the executor reads
itself: the live state of the node, the latest test of the line, and Field Service's own list
of bookings for the service.

A visit the evidence no longer supports is not a failure: it ends as SKIPPED with the reason,
and the conversation that asked for it decides what to say. Nothing is ever booked on evidence
that has gone stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from skylink_care.adapters.clients import FieldWriter
from skylink_care.adapters.gateway import ExecutorClients
from skylink_care.adapters.result import Err, ErrorKind, Ok
from skylink_care.agents.dispatch import is_open_visit
from skylink_care.clock import Clock, utc_now
from skylink_care.config import Settings
from skylink_care.db.actions import Action
from skylink_care.db.audit import AuditRepository
from skylink_care.db.database import Database
from skylink_care.db.line_tests import LineTestRepository
from skylink_care.domain.dispatch import VisitEvidence, VisitLimits, VisitReason, decide_visit
from skylink_care.domain.network import NetworkDecision
from skylink_care.executor.outcomes import (
    MissingPrerequisiteError,
    NotAttempted,
    Outcome,
    Skipped,
)
from skylink_care.observability.audit import Auditor
from skylink_care.runtime import build_diagnostics, visit_limits

ACTOR = "system:executor"
SLOT_TAKEN = "SLOT_TAKEN"
# Field Service's answers for a slot that filled up or has passed.
_SLOT_GONE = frozenset({"SLOT_FULL", "SLOT_NOT_FOUND"})


class NodeChecker(Protocol):
    async def node_state(
        self, node_id: str, service_type: str, *, audit: Auditor
    ) -> NetworkDecision: ...


@dataclass(frozen=True)
class _Terms:
    """What the proposal recorded, read back. Anything unreadable stops the booking."""

    service_id: str
    service_type: str
    node_id: str
    first_failed_at: datetime | None


class VisitBooker:
    def __init__(
        self,
        field: FieldWriter,
        line_tests: LineTestRepository,
        nodes: NodeChecker,
        audit: AuditRepository,
        limits: VisitLimits,
        *,
        now: Clock,
    ) -> None:
        self._field = field
        self._line_tests = line_tests
        self._nodes = nodes
        self._audit = audit
        self._limits = limits
        self._now = now

    async def __call__(self, action: Action) -> Outcome:
        terms = _terms(action)
        correlation_id = action.correlation_id
        if action.attempts > 1:
            # An earlier attempt may have booked the visit with its answer lost.
            earlier = await self._field.appointment_by_key(
                action.action_key, correlation_id=correlation_id
            )
            if isinstance(earlier, Ok):
                return earlier
            if earlier.kind is not ErrorKind.NOT_FOUND:
                return NotAttempted(earlier)
        booked = await self._field.booked_for_service(
            terms.service_id, correlation_id=correlation_id
        )
        if not isinstance(booked, Ok):
            return NotAttempted(booked)
        now = self._now()
        already = any(is_open_visit(item, now) for item in booked.value.appointments)
        reason = await self._recheck(action, terms, already_booked=already)
        if reason is not VisitReason.ELIGIBLE:
            return Skipped(reason.value)
        result = await self._field.book(
            action.request_body, idempotency_key=action.action_key, correlation_id=correlation_id
        )
        if isinstance(result, Err) and result.code in _SLOT_GONE:
            # The customer's choice was overtaken by events. That is not a failed write for a
            # person to chase: the conversation offers the slots that are free now.
            return Skipped(f"{SLOT_TAKEN}: {result.code}")
        return result

    async def _recheck(self, action: Action, terms: _Terms, *, already_booked: bool) -> VisitReason:
        """Apply the visit rule to what is true now, and leave a record of the answer."""
        audit = Auditor(
            self._audit, contact_id=action.contact_id, correlation_id=action.correlation_id
        )
        node = await self._nodes.node_state(terms.node_id, terms.service_type, audit=audit)
        line = await self._line_tests.latest_for_service(terms.service_id)
        evidence = VisitEvidence(
            # Whether the service is active and the customer agreed were settled at the proposal.
            service_active=True,
            visit_already_booked=already_booked,
            network_state=node.state,
            network_evidence_at=node.evidence_at,
            line_result=line.result if line else None,
            line_tested_at=line.tested_at if line else None,
            first_failed_at=terms.first_failed_at,
            consented=True,
        )
        decision = decide_visit(evidence, self._limits, now=self._now())
        await audit.record(
            actor=ACTOR,
            step="visit.recheck",
            outcome=decision.reason.value,
            inputs={
                "action_key": action.action_key,
                "node_id": terms.node_id,
                "network_state": node.state.value,
                "network_reason": node.reason.value,
                "line_test": line.result if line else None,
                "line_tested_at": line.tested_at.isoformat() if line else None,
                "visit_already_booked": already_booked,
            },
        )
        return decision.reason


def _terms(action: Action) -> _Terms:
    body, shown = action.request_body, action.inputs
    try:
        first_failed = shown.get("first_failed_at")
        return _Terms(
            service_id=_text(body.get("service_id")),
            service_type=_text(shown.get("service_type")),
            node_id=_text(shown.get("node_id")),
            first_failed_at=datetime.fromisoformat(first_failed) if first_failed else None,
        )
    except (TypeError, ValueError) as exc:
        raise MissingPrerequisiteError(
            f"the stored visit request cannot be read ({type(exc).__name__})"
        ) from exc


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("a required identifier is missing")
    return value


def build_visit_booker(
    settings: Settings, database: Database, clients: ExecutorClients, *, now: Clock = utc_now
) -> VisitBooker:
    """The booker of the executor process: it reads the node with the executor's own key."""
    return VisitBooker(
        clients.field,
        LineTestRepository(database),
        build_diagnostics(settings, database, clients.network, now=now),
        AuditRepository(database),
        visit_limits(settings, line_test_max_age_seconds=settings.visit_evidence_max_age_seconds),
        now=now,
    )
