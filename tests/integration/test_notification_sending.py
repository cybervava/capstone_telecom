"""The executor's notification handler, with the CRM and the gateway scripted.

The gateway in these tests accepts anything it is sent, so every "not sent" here is the
platform's own decision, not the gateway's refusal.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from skylink_care.adapters.gateway import ExecutorClients
from skylink_care.adapters.models import ConsentDto, MessageReceipt
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.agents import notifications
from skylink_care.db.actions import (
    NOTIFICATION_LANE,
    NOTIFY_SEND,
    Action,
    ActionRepository,
    ActionStatus,
    NewAction,
)
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.ops import OpsRepository
from skylink_care.domain.models import Channel, OutageInfo
from skylink_care.executor.worker import ActionExecutor, _ops_subject

OUTAGE = OutageInfo(
    outage_id="OUT-1",
    version=1,
    cause="FIBRE_CUT",
    planned=False,
    started_at=datetime(2026, 11, 15, 4, 30, tzinfo=UTC),
    eta=None,
    eta_source=None,
    resolved_at=None,
)
MESSAGE = notifications.outage_started(OUTAGE, service_types=["broadband"], area="Indiranagar")
KEY = notifications.action_key("C1", "OUT-1", notifications.OUTAGE_STARTED)
AGREED = Ok(ConsentDto(outage_notifications=True, channel="sms"), status_code=200)
REFUSED = Ok(ConsentDto(outage_notifications=False, channel="none"), status_code=200)
ACCEPTED = Ok(MessageReceipt(message_id="MSG-1", status="ACCEPTED"), status_code=202)
DOWN = Err(ErrorKind.UNAVAILABLE, "upstream unavailable", status_code=503)


class ScriptedCrm:
    def __init__(self, consent: Result[ConsentDto]) -> None:
        self.consent_result = consent
        self.asked: list[str] = []

    async def consent(self, customer_id: str, *, correlation_id: str) -> Result[ConsentDto]:
        self.asked.append(customer_id)
        return self.consent_result


class ScriptedNotify:
    def __init__(
        self,
        send: Result[MessageReceipt] = ACCEPTED,
        earlier: Result[MessageReceipt] | None = None,
    ) -> None:
        self.send_result = send
        self.earlier = earlier or Err(ErrorKind.NOT_FOUND, "no message", status_code=404)
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.looked_up: list[str] = []

    async def send(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[MessageReceipt]:
        self.sent.append((idempotency_key, body))
        return self.send_result

    async def by_key(self, key: str, *, correlation_id: str) -> Result[MessageReceipt]:
        self.looked_up.append(key)
        return self.earlier


def notice(key: str = KEY, **overrides: Any) -> NewAction:
    fields: dict[str, Any] = {
        "action_key": key,
        "action_type": NOTIFY_SEND,
        "lane": NOTIFICATION_LANE,
        "requested_by": notifications.REQUESTED_BY,
        # Unique per proposal: the audit log is append-only and outlives each test.
        "correlation_id": uuid4().hex,
        "request_body": notifications.request_body(MESSAGE, customer_id="C1", outage_id="OUT-1"),
    }
    return NewAction(**{**fields, **overrides})


def executor(
    db: Database, crm: ScriptedCrm, notify: ScriptedNotify, **options: Any
) -> ActionExecutor:
    clients = ExecutorClients(
        billing=None,  # type: ignore[arg-type]
        field=None,  # type: ignore[arg-type]
        incident=None,  # type: ignore[arg-type]
        notify=notify,  # type: ignore[arg-type]
        network=None,  # type: ignore[arg-type]
        crm=crm,  # type: ignore[arg-type]
    )
    return ActionExecutor(
        ActionRepository(db),
        AuditRepository(db),
        HandoffRepository(db),
        clients,
        visibility_seconds=0.01,
        backoff_base_seconds=0.01,
        **options,
    )


async def stored(actions: ActionRepository, key: str = KEY) -> Action:
    action = await actions.get_by_key(key)
    assert action is not None
    return action


async def audit_outcomes(db: Database, action: Action) -> list[str]:
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT outcome FROM audit_log WHERE step = 'action:NOTIFY_SEND'"
            " AND correlation_id = %s ORDER BY audit_id",
            (action.correlation_id,),
        )
        return [row["outcome"] for row in await cursor.fetchall()]


async def test_message_is_sent_exactly_as_proposed_when_the_customer_agrees(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    crm, notify = ScriptedCrm(AGREED), ScriptedNotify()

    await executor(db, crm, notify).run_once()

    assert crm.asked == ["C1"]
    assert notify.sent == [
        (KEY, notifications.request_body(MESSAGE, customer_id="C1", outage_id="OUT-1"))
    ]
    action = await stored(actions)
    assert (action.status, action.external_ref) == (ActionStatus.SUCCEEDED, "MSG-1")


async def test_no_consent_means_the_gateway_is_never_called(
    db: Database, actions: ActionRepository, handoffs: HandoffRepository
) -> None:
    await actions.propose(notice())
    notify = ScriptedNotify()

    await executor(db, ScriptedCrm(REFUSED), notify).run_once()

    assert notify.sent == []
    action = await stored(actions)
    assert (action.status, action.last_error) == (ActionStatus.SKIPPED, "NO_CONSENT")
    assert await audit_outcomes(db, action) == ["SKIPPED"]
    assert await handoffs.list_open() == []


async def test_consent_record_without_a_usable_channel_is_not_consent(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    notify = ScriptedNotify()
    muddled = Ok(ConsentDto(outage_notifications=True, channel="none"), status_code=200)

    await executor(db, ScriptedCrm(muddled), notify).run_once()

    assert notify.sent == []
    assert (await stored(actions)).status is ActionStatus.SKIPPED


async def test_message_proposed_too_long_ago_is_dropped_without_asking_anyone(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    crm, notify = ScriptedCrm(AGREED), ScriptedNotify()
    later = datetime.now(UTC) + timedelta(minutes=31)

    await executor(db, crm, notify, now=lambda: later).run_once()

    assert (crm.asked, notify.sent) == ([], [])
    action = await stored(actions)
    assert (action.status, action.last_error) == (ActionStatus.SKIPPED, "TOO_LATE")


async def test_consent_that_cannot_be_read_holds_the_message_back(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    notify = ScriptedNotify()

    await executor(db, ScriptedCrm(DOWN), notify).run_once()

    assert notify.sent == []
    action = await stored(actions)
    assert action.status is ActionStatus.IN_FLIGHT  # to be tried again
    # Nothing was sent, so the record must not say the outcome is unknown.
    assert await audit_outcomes(db, action) == ["DEFERRED"]


async def test_customer_the_crm_does_not_know_ends_the_action(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    notify = ScriptedNotify()
    unknown = Err(ErrorKind.NOT_FOUND, "no such customer", status_code=404, code="NOT_FOUND")

    await executor(db, ScriptedCrm(unknown), notify).run_once()

    assert notify.sent == []
    assert (await stored(actions)).status is ActionStatus.FAILED


async def test_gateway_refusing_for_lack_of_a_channel_is_a_skip_not_a_failure(
    db: Database, actions: ActionRepository
) -> None:
    """Consent was withdrawn between our check and the gateway's own."""
    await actions.propose(notice())
    refusal = Err(
        ErrorKind.CONFLICT, "no usable channel", status_code=409, code="NO_PREFERRED_CHANNEL"
    )

    await executor(db, ScriptedCrm(AGREED), ScriptedNotify(send=refusal)).run_once()

    action = await stored(actions)
    assert (action.status, action.last_error) == (ActionStatus.SKIPPED, "NO_CONSENT")


async def test_send_with_no_answer_is_an_unknown_outcome_and_is_replayed(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    lost = Err(ErrorKind.TIMEOUT, "no answer in time")

    await executor(db, ScriptedCrm(AGREED), ScriptedNotify(send=lost)).run_once()

    action = await stored(actions)
    assert action.status is ActionStatus.IN_FLIGHT
    assert await audit_outcomes(db, action) == ["OUTCOME_UNKNOWN"]


@pytest.mark.parametrize(
    ("earlier", "status", "reference"),
    [
        (ACCEPTED, ActionStatus.SUCCEEDED, "MSG-1"),
        (None, ActionStatus.SKIPPED, None),
        (DOWN, ActionStatus.IN_FLIGHT, None),
    ],
    ids=["earlier attempt went out", "nothing went out", "gateway cannot say yet"],
)
async def test_consent_gone_on_a_repeat_attempt_is_settled_by_what_was_actually_sent(
    db: Database,
    actions: ActionRepository,
    earlier: Result[MessageReceipt] | None,
    status: ActionStatus,
    reference: str | None,
) -> None:
    await actions.propose(notice())
    await actions.claim_runnable(limit=1, visibility_seconds=0)  # an earlier, unanswered attempt
    notify = ScriptedNotify(earlier=earlier)

    await executor(db, ScriptedCrm(REFUSED), notify).run_once()

    assert notify.sent == []  # consent is gone: nothing new is sent in any case
    assert notify.looked_up == [KEY]
    action = await stored(actions)
    assert (action.status, action.external_ref) == (status, reference)


async def test_late_repeat_attempt_is_not_called_skipped_when_the_first_one_went_out(
    db: Database, actions: ActionRepository
) -> None:
    """The gateway answered too late and the action then outlived its time limit."""
    await actions.propose(notice())
    await actions.claim_runnable(limit=1, visibility_seconds=0)
    crm, notify = ScriptedCrm(AGREED), ScriptedNotify(earlier=ACCEPTED)
    later = datetime.now(UTC) + timedelta(minutes=31)

    await executor(db, crm, notify, now=lambda: later).run_once()

    assert (crm.asked, notify.sent, notify.looked_up) == ([], [], [KEY])
    action = await stored(actions)
    assert (action.status, action.external_ref) == (ActionStatus.SUCCEEDED, "MSG-1")


async def test_gateway_refusal_on_a_repeat_attempt_is_checked_against_the_first_one(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    await actions.claim_runnable(limit=1, visibility_seconds=0)
    refusal = Err(
        ErrorKind.CONFLICT, "no usable channel", status_code=409, code="NO_PREFERRED_CHANNEL"
    )
    notify = ScriptedNotify(send=refusal, earlier=ACCEPTED)

    await executor(db, ScriptedCrm(AGREED), notify).run_once()

    assert (await stored(actions)).status is ActionStatus.SUCCEEDED


async def test_skip_keeps_what_held_the_message_up_on_the_record(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    crm, notify = ScriptedCrm(DOWN), ScriptedNotify()
    await executor(db, crm, notify).run_once()  # consent could not be read: held back
    later = datetime.now(UTC) + timedelta(minutes=31)

    await asyncio.sleep(0.05)  # let the short test back-off pass
    await executor(db, crm, notify, now=lambda: later).run_once()

    action = await stored(actions)
    assert action.status is ActionStatus.SKIPPED
    assert action.last_error is not None
    assert action.last_error.startswith("TOO_LATE (after: UNAVAILABLE")


async def test_failed_notification_still_alerts_when_the_audit_write_fails(
    db: Database,
    actions: ActionRepository,
    contacts: ContactRepository,
    handoffs: HandoffRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outcome is final by then: losing the audit line must not also lose the alert."""
    intake = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="hello",
            correlation_id=uuid4().hex,
            received_at=datetime.now(UTC),
        )
    )
    await actions.propose(notice(contact_id=intake.contact_id))
    unknown = Err(ErrorKind.NOT_FOUND, "no such customer", status_code=404, code="NOT_FOUND")
    worker = executor(db, ScriptedCrm(unknown), ScriptedNotify())

    async def broken_audit(*_: Any, **__: Any) -> None:
        raise ConnectionError("audit store unavailable")

    monkeypatch.setattr(AuditRepository, "append", broken_audit)
    await worker.run_once()

    assert (await stored(actions)).status is ActionStatus.FAILED
    assert [item.reason for item in await handoffs.list_open()] == ["ACTION_FAILED"]


async def test_skipped_first_message_quietly_skips_the_one_that_depends_on_it(
    db: Database, actions: ActionRepository, handoffs: HandoffRepository
) -> None:
    started = await actions.propose(notice())
    restored_key = notifications.action_key("C1", "OUT-1", notifications.OUTAGE_RESOLVED)
    await actions.propose(notice(restored_key, depends_on=started.action.action_id))
    notify = ScriptedNotify()
    worker = executor(db, ScriptedCrm(REFUSED), notify)

    await worker.run_once()
    await worker.run_once()

    assert notify.sent == []
    restored = await stored(actions, restored_key)
    assert (restored.status, restored.last_error) == (
        ActionStatus.SKIPPED,
        "prerequisite action SKIPPED",
    )
    assert await handoffs.list_open() == []


async def test_many_proposals_are_recorded_once_and_found_again_by_key(
    db: Database, actions: ActionRepository
) -> None:
    batch = [notice(f"notify:C{n}:OUT-1:outage_started.v1") for n in range(1, 4)]

    async with db.transaction() as conn:
        inserted = await actions.propose_many(conn, batch)
        repeated = await actions.propose_many(conn, [*batch, notice("notify:C9:OUT-1:x")])
        nothing = await actions.propose_many(conn, [])
        found = await actions.lookup_keys(conn, [batch[0].action_key, "notify:nobody"])
        empty = await actions.lookup_keys(conn, [])

    assert (inserted, repeated, nothing) == (3, 1, 0)
    assert set(found) == {batch[0].action_key}
    assert found[batch[0].action_key].status is ActionStatus.READY
    assert empty == {}


# --- a failure that no conversation is waiting for -----------------------------------------------


async def test_failed_notification_no_contact_waits_for_is_queued_for_operations(
    db: Database, actions: ActionRepository, handoffs: HandoffRepository
) -> None:
    """It used to be a log line. Now a person is told, in the queue that is theirs."""
    await actions.propose(notice())
    unknown = Err(ErrorKind.NOT_FOUND, "no such customer", status_code=404, code="NOT_FOUND")

    await executor(db, ScriptedCrm(unknown), ScriptedNotify()).run_once()

    assert (await stored(actions)).status is ActionStatus.FAILED
    (item,) = await OpsRepository(db).list_open()
    assert item.kind == "ACTION_FAILED"
    # About the outage and the message, not about one customer.
    assert item.subject == f"notify:OUT-1:{notifications.OUTAGE_STARTED}"
    assert "NOTIFY_SEND" in item.summary
    assert "NOT_FOUND" in item.summary
    assert item.details["action_type"] == NOTIFY_SEND
    assert item.details["last_action_key"] == KEY
    assert await handoffs.list_open() == []  # there is no conversation to hand to a person


async def test_notifications_failing_for_one_outage_are_one_item_that_counts_them(
    db: Database, actions: ActionRepository
) -> None:
    unknown = Err(ErrorKind.NOT_FOUND, "no such customer", status_code=404, code="NOT_FOUND")
    for customer in ("C1", "C2", "C3"):
        key = notifications.action_key(customer, "OUT-1", notifications.OUTAGE_STARTED)
        body = notifications.request_body(MESSAGE, customer_id=customer, outage_id="OUT-1")
        await actions.propose(notice(key, request_body=body))

    await executor(db, ScriptedCrm(unknown), ScriptedNotify()).run_once()

    (item,) = await OpsRepository(db).list_open()
    assert item.occurrences == 3


async def test_notification_that_keeps_being_put_off_is_queued_for_operations_while_it_goes_on(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    worker = executor(db, ScriptedCrm(DOWN), ScriptedNotify(), alert_after_attempts=2)

    for _ in range(40):
        await worker.run_once()
        if await OpsRepository(db).list_open():
            break
        await asyncio.sleep(0.02)

    (item,) = await OpsRepository(db).list_open()
    assert item.kind == "ACTION_DELAYED"
    assert (await stored(actions)).status is ActionStatus.IN_FLIGHT  # still being tried


async def test_failure_a_conversation_waits_for_goes_to_that_conversations_queue(
    db: Database,
    actions: ActionRepository,
    contacts: ContactRepository,
    handoffs: HandoffRepository,
) -> None:
    intake = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="hello",
            correlation_id=uuid4().hex,
            received_at=datetime.now(UTC),
        )
    )
    await actions.propose(notice(contact_id=intake.contact_id))
    unknown = Err(ErrorKind.NOT_FOUND, "no such customer", status_code=404, code="NOT_FOUND")

    await executor(db, ScriptedCrm(unknown), ScriptedNotify()).run_once()

    assert [item.reason for item in await handoffs.list_open()] == ["ACTION_FAILED"]
    assert await OpsRepository(db).list_open() == []


async def test_failing_write_of_one_kind_is_one_item_whatever_it_was_for(
    db: Database, actions: ActionRepository
) -> None:
    """Found by review: only notifications were counted together. A ticket API that is down
    in a large outage refuses a write for every node, and that is one line to read."""
    await actions.propose(notice())
    stored_notice = await stored(actions)
    for_a_node = replace(stored_notice, action_type="INCIDENT_UPDATE", action_key="incident:N1:2")
    for_another = replace(stored_notice, action_type="INCIDENT_UPDATE", action_key="incident:N2:1")

    assert _ops_subject(for_a_node) == _ops_subject(for_another) == "write:INCIDENT_UPDATE"
    assert _ops_subject(stored_notice) == f"notify:OUT-1:{notifications.OUTAGE_STARTED}"


async def test_queue_that_refuses_the_item_does_not_disturb_the_executor(
    db: Database, actions: ActionRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    await actions.propose(notice())
    unknown = Err(ErrorKind.NOT_FOUND, "no such customer", status_code=404, code="NOT_FOUND")

    async def refuses(*_: Any, **__: Any) -> bool:
        raise RuntimeError("the queue is not there")

    monkeypatch.setattr(OpsRepository, "raise_item", refuses)
    await executor(db, ScriptedCrm(unknown), ScriptedNotify()).run_once()

    assert (await stored(actions)).status is ActionStatus.FAILED


async def test_what_a_gateway_says_is_masked_before_staff_read_it(
    db: Database, actions: ActionRepository
) -> None:
    await actions.propose(notice())
    echoes = Err(
        ErrorKind.UNAVAILABLE,
        "could not look up 9876543210 (a.kumar@example.com)",
        status_code=503,
    )
    worker = executor(db, ScriptedCrm(echoes), ScriptedNotify(), alert_after_attempts=2)

    for _ in range(40):
        await worker.run_once()
        if await OpsRepository(db).list_open():
            break
        await asyncio.sleep(0.02)

    (item,) = await OpsRepository(db).list_open()
    shown = item.summary + str(item.details)
    assert "9876543210" not in shown
    assert "a.kumar@example.com" not in shown
    assert "<PHONE>" in item.summary
