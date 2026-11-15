"""Approvals and the credit write, with Billing scripted.

What must hold: an approval is bound to the exact request, a decision and its effect on the
action are one transaction, and a credit is never applied without its approval or twice.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from skylink_care.adapters.models import CreditDto, CreditList
from skylink_care.adapters.result import Err, ErrorKind, Result
from skylink_care.db.actions import ActionStatus
from skylink_care.db.approvals import ApprovalRepository, ApprovalStatus, DecisionOutcome
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.domain.credit import cycle_start
from tests.support.credits import (
    BODY,
    KEY,
    NOW,
    ScriptedBilling,
    a_contact,
    account,
    credit,
    decide,
    executor,
    proposed,
    stored,
    woken_with,
)

# --- approvals -----------------------------------------------------------------------------------


async def test_credit_that_needs_approval_cannot_run_until_it_is_given(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts))
    billing = ScriptedBilling()
    worker = executor(db, billing)

    await worker.run_once()
    assert (billing.writes, (await stored(db, action)).status) == (
        [],
        ActionStatus.PENDING_APPROVAL,
    )

    result = await decide(db, action)
    await worker.run_once()

    assert result.outcome is DecisionOutcome.DECIDED
    assert result.approval is not None
    assert result.approval.decision_note == "checked against the outage record"
    ((key, body),) = billing.writes
    assert key == KEY
    # Exactly what was proposed and approved, plus who approved it.
    assert body == {**BODY, "approved_by": "staff-1"}
    done = await stored(db, action)
    assert (done.status, done.external_ref) == (ActionStatus.SUCCEEDED, "CR-1")


async def test_decision_is_taken_once_and_only_by_someone_who_may_take_it(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts), level="supervisor")
    approvals = ApprovalRepository(db)

    by_agent = await decide(db, action, role="agent")
    by_nobody = await decide(db, action, role="ops")
    by_supervisor = await decide(db, action, role="supervisor")
    again = await decide(db, action, approve=False, role="supervisor")
    missing = await approvals.decide(
        uuid4(), approve=True, decided_by="x", role="supervisor", request_hash="0" * 64, now=NOW
    )

    assert [r.outcome for r in (by_agent, by_nobody, by_supervisor, again, missing)] == [
        DecisionOutcome.WRONG_ROLE,
        DecisionOutcome.WRONG_ROLE,
        DecisionOutcome.DECIDED,
        DecisionOutcome.NOT_PENDING,
        DecisionOutcome.NOT_FOUND,
    ]
    assert (await stored(db, action)).status is ActionStatus.READY


async def test_supervisor_may_decide_what_only_needs_an_agent_and_each_role_sees_its_queue(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts), level="agent")
    approvals = ApprovalRepository(db)

    assert len(await approvals.list_pending("agent", now=NOW)) == 1
    assert len(await approvals.list_pending("supervisor", now=NOW)) == 1
    assert await approvals.list_pending("ops", now=NOW) == []
    assert (await decide(db, action, role="supervisor")).outcome is DecisionOutcome.DECIDED
    assert await approvals.list_pending("supervisor", now=NOW) == []


async def test_rejection_ends_the_action_and_wakes_the_contact(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id)
    billing = ScriptedBilling()

    await decide(db, action, approve=False)
    await executor(db, billing).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.REJECTED
    assert await woken_with(inbox, contact_id) == ["REJECTED"]


async def test_approval_nobody_gives_expires_with_its_action_and_cannot_be_given_late(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id, ttl=timedelta(seconds=-1))  # already past
    billing = ScriptedBilling()

    late = await decide(db, action)
    await executor(db, billing).run_once()  # the sweep ends it

    assert late.outcome is DecisionOutcome.NOT_PENDING
    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.EXPIRED
    approval = await ApprovalRepository(db).for_action(action.action_id)
    assert approval is not None
    assert approval.status is ApprovalStatus.EXPIRED
    assert await woken_with(inbox, contact_id) == ["EXPIRED"]


async def test_decision_on_a_credit_that_ended_some_other_way_changes_nothing_but_is_named(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id)
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE actions SET status = 'BLOCKED' WHERE action_id = %s", (action.action_id,)
        )

    result = await decide(db, action)

    assert result.outcome is DecisionOutcome.DECIDED
    assert result.action is not None  # whoever records the decision can still say what it was on
    assert (result.action.action_key, result.action.contact_id) == (KEY, contact_id)
    ended = await stored(db, action)
    assert (ended.status, ended.ready_at) == (ActionStatus.BLOCKED, None)
    assert await woken_with(inbox, contact_id) == []


# --- the write ---------------------------------------------------------------------------------


async def test_runnable_credit_without_a_matching_approval_is_blocked_not_sent(
    db: Database, contacts: ContactRepository, handoffs: HandoffRepository
) -> None:
    """It should be impossible. It is checked at the last point before money moves anyway."""
    action = await proposed(db, await a_contact(contacts))
    async with db.connection() as conn:  # someone made it runnable behind the approval's back
        await conn.execute(
            "UPDATE actions SET status = 'READY' WHERE action_id = %s", (action.action_id,)
        )
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED
    assert [item.reason for item in await handoffs.list_open()] == ["ACTION_FAILED"]


async def test_approval_of_a_different_request_does_not_cover_this_one(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    async with db.connection() as conn:  # the approval now speaks of another request
        await conn.execute(
            "UPDATE approvals SET request_hash = %s WHERE action_id = %s",
            ("f" * 64, action.action_id),
        )
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED


async def test_credit_already_on_the_account_for_this_outage_is_not_applied_again(
    db: Database, contacts: ContactRepository, inbox: InboxRepository, handoffs: HandoffRepository
) -> None:
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id)
    await decide(db, action)
    by_hand = credit(credit_id="CR-MANUAL", idempotency_key="manual-entry-77")
    billing = ScriptedBilling(existing=account(by_hand))

    await executor(db, billing).run_once()

    assert billing.writes == []
    done = await stored(db, action)
    assert (done.status, done.last_error) == (ActionStatus.SKIPPED, "ALREADY_CREDITED")
    assert await woken_with(inbox, contact_id) == ["SKIPPED"]
    assert await handoffs.list_open() == []


async def test_our_own_earlier_write_is_the_answer_and_is_not_sent_again(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    """The first attempt landed and its answer was lost. The credit is on the account under
    this action's key: that is the outcome, and nothing rests on the gateway remembering it."""
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id)
    await decide(db, action)
    billing = ScriptedBilling(existing=account(credit(credit_id="CR-9", idempotency_key=KEY)))

    await executor(db, billing).run_once()

    assert billing.writes == []
    done = await stored(db, action)
    assert (done.status, done.external_ref) == (ActionStatus.SUCCEEDED, "CR-9")
    assert await woken_with(inbox, contact_id) == ["SUCCEEDED"]


@pytest.mark.parametrize(
    ("existing", "applied", "status", "written"),
    [
        (Err(ErrorKind.UNAVAILABLE, "billing down", status_code=503), None, "IN_FLIGHT", 0),
        (None, Err(ErrorKind.TIMEOUT, "no answer in time"), "IN_FLIGHT", 1),
        (
            None,
            Err(ErrorKind.CONFLICT, "suspended", status_code=409, code="ACCOUNT_SUSPENDED"),
            "FAILED",
            1,
        ),
    ],
    ids=["account unreadable: nothing written", "answer lost: replayed", "refused: ends"],
)
async def test_billing_failures_are_told_apart(
    db: Database,
    contacts: ContactRepository,
    existing: Result[CreditList] | None,
    applied: Result[CreditDto] | None,
    status: str,
    written: int,
) -> None:
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    billing = ScriptedBilling(existing=existing, applied=applied)

    await executor(db, billing).run_once()

    assert len(billing.writes) == written
    assert (await stored(db, action)).status.value == status


async def test_credit_that_needs_no_approval_is_sent_with_no_approver(
    db: Database, contacts: ContactRepository
) -> None:
    await proposed(db, await a_contact(contacts), level="none")
    billing = ScriptedBilling()

    await executor(db, billing, review_all=False).run_once()

    ((_, body),) = billing.writes
    assert body == {**BODY, "approved_by": None}


# --- billing cycle -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "cycle_day", "start"),
    [
        (date(2026, 11, 15), 10, date(2026, 11, 10)),
        (date(2026, 11, 15), 15, date(2026, 11, 15)),
        (date(2026, 11, 15), 28, date(2026, 10, 28)),
        (date(2026, 1, 3), 20, date(2025, 12, 20)),
        (date(2026, 3, 1), 28, date(2026, 2, 28)),
    ],
)
def test_billing_cycle_starts_on_the_cycle_day_on_or_before_today(
    today: date, cycle_day: int, start: date
) -> None:
    assert cycle_start(today, cycle_day) == start
