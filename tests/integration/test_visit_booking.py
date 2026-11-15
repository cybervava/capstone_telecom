"""The visit booking in the executor, with Field Service and the node's state scripted.

What must hold: the executor books only on evidence it has read itself and that holds now, a
visit it declines is not a failure, and a retry never books twice.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from skylink_care.adapters.gateway import ExecutorClients
from skylink_care.adapters.models import AppointmentDto, AppointmentList
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.db.actions import (
    VISIT_BOOK,
    VISIT_LANE,
    Action,
    ActionRepository,
    ActionStatus,
    NewAction,
)
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.line_tests import LineTest, LineTestRepository
from skylink_care.domain.dispatch import VisitLimits
from skylink_care.domain.models import NetworkState
from skylink_care.domain.network import DecisionReason, EvidenceSource, NetworkDecision
from skylink_care.executor.visits import VisitBooker
from skylink_care.executor.worker import ActionExecutor
from tests.support.credits import a_contact, stored, woken_with

NOW = datetime(2026, 11, 15, 6, 30, tzinfo=UTC)
NODE, SERVICE = "BLR-OLT-0012", "S1"
SLOT = "SL-560038-20261116-AM"
FIRST_FAILED = NOW - timedelta(minutes=12)
BODY = {
    "customer_id": "C1",
    "service_id": SERVICE,
    "slot_id": SLOT,
    "reason": "Line still failing its remote test after the hold; node confirmed clear",
    "complaint_ref": "SKL-261115-000001-9",
}
INPUTS = {
    "node_id": NODE,
    "service_type": "broadband",
    "first_failed_at": FIRST_FAILED.isoformat(),
    "consent_message_id": 42,
}
LIMITS = VisitLimits(
    hold=timedelta(minutes=10),
    status_max_age=timedelta(seconds=60),
    line_test_max_age=timedelta(minutes=30),
)
DOWN = Err(ErrorKind.UNAVAILABLE, "field service down", status_code=503)


def appointment(**overrides: Any) -> AppointmentDto:
    fields: dict[str, Any] = {
        "appointment_id": "AP-1",
        "slot_id": SLOT,
        "start": NOW + timedelta(hours=21),
        "end": NOW + timedelta(hours=25),
        "status": "BOOKED",
        "service_id": SERVICE,
        "customer_id": "C1",
    }
    return AppointmentDto(**{**fields, **overrides})


class ScriptedField:
    def __init__(
        self,
        *,
        booked: Result[AppointmentList] | None = None,
        by_key: Result[AppointmentDto] | None = None,
        book: Result[AppointmentDto] | None = None,
    ) -> None:
        self.booked = booked or Ok(AppointmentList(appointments=()), status_code=200)
        self.by_key = by_key or Err(ErrorKind.NOT_FOUND, "no such key", status_code=404)
        self.answer = book or Ok(appointment(), status_code=201)
        self.lookups: list[str] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def booked_for_service(self, service_id: str, **_: Any) -> Result[AppointmentList]:
        return self.booked

    async def appointment_by_key(self, key: str, **_: Any) -> Result[AppointmentDto]:
        self.lookups.append(key)
        return self.by_key

    async def book(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[AppointmentDto]:
        self.writes.append((idempotency_key, body))
        return self.answer


class ScriptedNode:
    """What the executor finds when it looks at the node itself."""

    def __init__(
        self,
        state: NetworkState = NetworkState.CONFIRMED_CLEAR,
        *,
        evidence_at: datetime | None = NOW - timedelta(seconds=5),
    ) -> None:
        self.state, self.evidence_at = state, evidence_at
        self.asked: list[tuple[str, str]] = []

    async def node_state(self, node_id: str, service_type: str, **_: Any) -> NetworkDecision:
        self.asked.append((node_id, service_type))
        return NetworkDecision(
            state=self.state,
            reason=DecisionReason.LIVE_CLEAR,
            source=EvidenceSource.LIVE_API,
            node_id=node_id,
            decided_at=NOW,
            evidence_at=self.evidence_at,
        )


def executor(
    db: Database, field: ScriptedField, node: ScriptedNode | None = None
) -> ActionExecutor:
    clients = ExecutorClients(
        billing=None,  # type: ignore[arg-type]
        field=field,  # type: ignore[arg-type]
        incident=None,  # type: ignore[arg-type]
        notify=None,  # type: ignore[arg-type]
        network=None,  # type: ignore[arg-type]
        crm=None,  # type: ignore[arg-type]
    )
    booker = VisitBooker(
        field,  # type: ignore[arg-type]
        LineTestRepository(db),
        node or ScriptedNode(),
        AuditRepository(db),
        LIMITS,
        now=lambda: NOW,
    )
    return ActionExecutor(
        ActionRepository(db),
        AuditRepository(db),
        HandoffRepository(db),
        clients,
        visibility_seconds=0.01,
        backoff_base_seconds=0.01,
        visits=booker,
    )


async def line_tested(db: Database, result: str = "FAIL", *, at: datetime | None = None) -> None:
    test = LineTest(uuid4().hex, SERVICE, NODE, result, at or NOW - timedelta(seconds=40))
    await LineTestRepository(db).record(test, contact_id=None)


async def proposed(
    db: Database, contact_id: UUID, *, inputs: dict[str, Any] | None = None, **changes: Any
) -> Action:
    new = NewAction(
        action_key=f"visit:{contact_id}:1",
        action_type=VISIT_BOOK,
        lane=VISIT_LANE,
        requested_by="agent:dispatch",
        contact_id=contact_id,
        correlation_id=uuid4().hex,
        request_body={**BODY, **changes},
        inputs=dict(INPUTS if inputs is None else inputs),
    )
    proposal = await ActionRepository(db).propose_only_one_open(new)
    assert proposal is not None
    return proposal.action


# --- booking -------------------------------------------------------------------------------------


async def test_visit_whose_evidence_still_holds_is_booked_exactly_as_it_was_proposed(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await a_contact(contacts)
    await line_tested(db)
    action = await proposed(db, contact_id)
    field, node = ScriptedField(), ScriptedNode()

    await executor(db, field, node).run_once()

    assert field.writes == [(action.action_key, BODY)]
    assert field.lookups == []  # a first attempt has no earlier answer to look for
    assert node.asked == [(NODE, "broadband")]
    done = await stored(db, action)
    assert (done.status, done.external_ref) == (ActionStatus.SUCCEEDED, "AP-1")
    assert await woken_with(inbox, contact_id) == ["SUCCEEDED"]
    trail = await AuditRepository(db).for_contact(contact_id)
    assert [r.outcome for r in trail if r.step == "visit.recheck"] == ["ELIGIBLE"]


@pytest.mark.parametrize(
    ("node", "line", "reason"),
    [
        (ScriptedNode(NetworkState.ACTIVE), "FAIL", "OUTAGE_ACTIVE"),
        (ScriptedNode(NetworkState.UNKNOWN, evidence_at=None), "FAIL", "NETWORK_UNVERIFIED"),
        (ScriptedNode(evidence_at=NOW - timedelta(minutes=5)), "FAIL", "CLEAR_NOT_FRESH"),
        (ScriptedNode(), "PASS", "LINE_PASSING"),
        (ScriptedNode(), "INCONCLUSIVE", "LINE_FAULT_NOT_PROVEN"),
        (ScriptedNode(), None, "LINE_FAULT_NOT_PROVEN"),
    ],
    ids=["outage", "unverified", "clear but old", "line passes", "inconclusive", "never tested"],
)
async def test_visit_the_evidence_no_longer_supports_is_declined_and_nobody_is_alerted(
    db: Database,
    contacts: ContactRepository,
    inbox: InboxRepository,
    handoffs: HandoffRepository,
    node: ScriptedNode,
    line: str | None,
    reason: str,
) -> None:
    contact_id = await a_contact(contacts)
    if line is not None:
        await line_tested(db, line)
    action = await proposed(db, contact_id)
    field = ScriptedField()

    await executor(db, field, node).run_once()

    assert field.writes == []
    declined = await stored(db, action)
    assert (declined.status, declined.last_error) == (ActionStatus.SKIPPED, reason)
    assert await woken_with(inbox, contact_id) == ["SKIPPED"]
    assert await handoffs.list_open() == []


async def test_line_test_too_old_by_the_time_of_booking_books_nothing(
    db: Database, contacts: ContactRepository
) -> None:
    """Zero dispatches on stale data: the executor was delayed, and the proof has aged."""
    await line_tested(db, at=NOW - timedelta(minutes=31))
    action = await proposed(
        db, await a_contact(contacts), inputs={**INPUTS, "first_failed_at": None}
    )
    field = ScriptedField()

    await executor(db, field).run_once()

    assert field.writes == []
    assert (await stored(db, action)).last_error == "RETEST_NEEDED"


async def test_latest_test_of_the_line_is_what_counts(
    db: Database, contacts: ContactRepository
) -> None:
    await line_tested(db, "FAIL", at=NOW - timedelta(minutes=2))
    await line_tested(db, "PASS", at=NOW - timedelta(seconds=30))  # it has come back since
    action = await proposed(db, await a_contact(contacts))
    field = ScriptedField()

    await executor(db, field).run_once()

    assert field.writes == []
    assert (await stored(db, action)).last_error == "LINE_PASSING"


async def test_visit_already_booked_for_the_service_is_not_booked_again(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await a_contact(contacts)
    await line_tested(db)
    action = await proposed(db, contact_id)
    existing = appointment(appointment_id="AP-0")
    cancelled = appointment(appointment_id="AP-X", status="CANCELLED")
    listing = Ok(AppointmentList(appointments=(cancelled, existing)), status_code=200)
    field = ScriptedField(booked=listing)

    await executor(db, field).run_once()

    assert field.writes == []
    skipped = await stored(db, action)
    assert (skipped.status, skipped.last_error) == (ActionStatus.SKIPPED, "VISIT_ALREADY_BOOKED")
    assert await woken_with(inbox, contact_id) == ["SKIPPED"]


async def test_visit_whose_time_has_passed_does_not_stop_the_next_one(
    db: Database, contacts: ContactRepository
) -> None:
    """Field Service keeps a visit BOOKED after its slot is over: nothing to wait for."""
    await line_tested(db)
    action = await proposed(db, await a_contact(contacts))
    last_week = appointment(
        appointment_id="AP-OLD",
        start=NOW - timedelta(days=7),
        end=NOW - timedelta(days=7, hours=-4),
    )
    field = ScriptedField(booked=Ok(AppointmentList(appointments=(last_week,)), status_code=200))

    await executor(db, field).run_once()

    assert len(field.writes) == 1
    assert (await stored(db, action)).status is ActionStatus.SUCCEEDED


@pytest.mark.parametrize("code", ["SLOT_FULL", "SLOT_NOT_FOUND"])
async def test_slot_that_filled_up_or_passed_is_the_conversations_to_handle(
    db: Database, contacts: ContactRepository, handoffs: HandoffRepository, code: str
) -> None:
    status = 409 if code == "SLOT_FULL" else 404
    kind = ErrorKind.CONFLICT if code == "SLOT_FULL" else ErrorKind.NOT_FOUND
    await line_tested(db)
    action = await proposed(db, await a_contact(contacts))
    field = ScriptedField(book=Err(kind, "slot unavailable", status_code=status, code=code))

    await executor(db, field).run_once()

    assert len(field.writes) == 1
    ended = await stored(db, action)
    assert (ended.status, ended.last_error) == (ActionStatus.SKIPPED, f"SLOT_TAKEN: {code}")
    assert await handoffs.list_open() == []


# --- failures and retries ------------------------------------------------------------------------


async def test_field_service_that_cannot_be_read_means_nothing_is_written_blind(
    db: Database, contacts: ContactRepository
) -> None:
    await line_tested(db)
    action = await proposed(db, await a_contact(contacts))
    field = ScriptedField(booked=DOWN)

    await executor(db, field).run_once()

    assert field.writes == []
    assert (await stored(db, action)).status is ActionStatus.IN_FLIGHT  # to be tried again


async def test_retry_finds_the_booking_an_earlier_attempt_made_and_books_nothing_more(
    db: Database, contacts: ContactRepository
) -> None:
    await line_tested(db)
    action = await proposed(db, await a_contact(contacts))
    lost = ScriptedField(book=Err(ErrorKind.TIMEOUT, "no answer in time"))
    worker = executor(db, lost)
    await worker.run_once()
    assert (await stored(db, action)).status is ActionStatus.IN_FLIGHT
    found = ScriptedField(by_key=Ok(appointment(appointment_id="AP-7"), status_code=200))

    await _retry(db, action, executor(db, found))

    assert found.lookups == [action.action_key]
    assert found.writes == []
    done = await stored(db, action)
    assert (done.status, done.external_ref) == (ActionStatus.SUCCEEDED, "AP-7")


async def test_retry_that_cannot_learn_what_the_earlier_attempt_did_writes_nothing(
    db: Database, contacts: ContactRepository
) -> None:
    await line_tested(db)
    action = await proposed(db, await a_contact(contacts))
    await executor(db, ScriptedField(book=Err(ErrorKind.TIMEOUT, "no answer"))).run_once()
    blind = ScriptedField(by_key=DOWN)

    await _retry(db, action, executor(db, blind))

    assert blind.writes == []
    assert (await stored(db, action)).status is ActionStatus.IN_FLIGHT


async def _retry(db: Database, action: Action, worker: ActionExecutor) -> None:
    """Run until the deferred action has become runnable again and was attempted."""
    for _ in range(200):
        await worker.run_once()
        if (await stored(db, action)).attempts > 1:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the deferred action was never attempted again")


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {**INPUTS, "node_id": ""},
        {**INPUTS, "service_type": None},
        {**INPUTS, "first_failed_at": "half past six"},
    ],
    ids=["nothing recorded", "no node", "no service type", "unreadable time"],
)
async def test_visit_whose_recorded_terms_cannot_be_read_is_blocked(
    db: Database, contacts: ContactRepository, inputs: dict[str, Any]
) -> None:
    await line_tested(db)
    action = await proposed(db, await a_contact(contacts), inputs=inputs)
    field = ScriptedField()

    await executor(db, field).run_once()

    assert field.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED


async def test_executor_without_a_booker_books_nothing(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts))
    clients = ExecutorClients(
        billing=None,  # type: ignore[arg-type]
        field=None,  # type: ignore[arg-type]
        incident=None,  # type: ignore[arg-type]
        notify=None,  # type: ignore[arg-type]
        network=None,  # type: ignore[arg-type]
        crm=None,  # type: ignore[arg-type]
    )
    worker = ActionExecutor(
        ActionRepository(db), AuditRepository(db), HandoffRepository(db), clients
    )

    await worker.run_once()

    failed = await stored(db, action)
    assert (failed.status, failed.last_error) == (ActionStatus.FAILED, "no handler for this type")


# --- one booking in progress per service ---------------------------------------------------------


async def test_second_booking_for_a_service_cannot_be_proposed_while_one_is_open(
    db: Database, contacts: ContactRepository
) -> None:
    actions = ActionRepository(db)
    first = await proposed(db, await a_contact(contacts))
    other_contact = await a_contact(contacts)

    def visit(key: str, service_id: str) -> NewAction:
        return NewAction(
            action_key=key,
            action_type=VISIT_BOOK,
            lane=VISIT_LANE,
            requested_by="agent:dispatch",
            contact_id=other_contact,
            correlation_id=uuid4().hex,
            request_body={**BODY, "service_id": service_id},
            inputs=dict(INPUTS),
        )

    assert await actions.propose_only_one_open(visit(f"visit:{other_contact}:1", SERVICE)) is None
    another_service = await actions.propose_only_one_open(visit("visit:other:1", "S2"))
    assert another_service is not None
    assert another_service.created

    again = await actions.propose_only_one_open(
        NewAction(
            action_key=first.action_key,
            action_type=VISIT_BOOK,
            lane=VISIT_LANE,
            requested_by="agent:dispatch",
            correlation_id=uuid4().hex,
            request_body=dict(BODY),
            inputs=dict(INPUTS),
        )
    )
    assert again is not None  # the same proposal again is the same action, not a second one
    assert (again.created, again.action.action_id) == (False, first.action_id)

    await actions.finish(first.action_id, ActionStatus.SKIPPED, error="OUTAGE_ACTIVE")
    later = await actions.propose_only_one_open(visit(f"visit:{other_contact}:2", SERVICE))
    assert later is not None
    assert later.created


async def test_latest_line_test_of_a_service_is_found_whoever_ran_it(db: Database) -> None:
    tests = LineTestRepository(db)
    assert await tests.latest_for_service(SERVICE) is None
    await line_tested(db, "FAIL", at=NOW - timedelta(minutes=3))
    await line_tested(db, "PASS", at=NOW - timedelta(minutes=1))
    other = LineTest(uuid4().hex, "S2", NODE, "FAIL", NOW)
    await tests.record(other, contact_id=None)

    latest = await tests.latest_for_service(SERVICE)

    assert latest is not None
    assert (latest.result, latest.tested_at) == ("PASS", NOW - timedelta(minutes=1))
