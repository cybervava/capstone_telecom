"""The last checks before a credit is applied, with Billing scripted.

A credit may be applied days after it was proposed. What must hold then: nothing recorded at
the proposal is taken on trust, the billing-cycle cap is enforced where the money moves, and an
approval that took a long time does not cost the write its retry window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from skylink_care.adapters.result import Err, ErrorKind
from skylink_care.db.actions import ActionRepository, ActionStatus
from skylink_care.db.approvals import ApprovalRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from tests.support.credits import (
    BODY,
    INPUTS,
    KEY,
    MONTHLY_FEE,
    NOW,
    ScriptedBilling,
    a_contact,
    account,
    credit,
    decide,
    executor,
    proposed,
    stored,
)

BILLING_DOWN = Err(ErrorKind.UNAVAILABLE, "billing down", status_code=503)


def another_outage(amount: str, **overrides: Any) -> Any:
    """A credit already on the account for a different outage of the same service."""
    fields = {
        "credit_id": "CR-0",
        "outage_id": "OUT-0",
        "amount_inr": Decimal(amount),
        "idempotency_key": "credit:S1:OUT-0",
    }
    return credit(**{**fields, **overrides})


# --- the retry window ----------------------------------------------------------------------------


async def test_approval_given_long_after_the_proposal_still_gets_its_retry_window(
    db: Database, contacts: ContactRepository
) -> None:
    """The clock for giving up starts at the approval. Started at the proposal, the first
    hiccup of Billing would end a credit that a supervisor had just approved."""
    action = await proposed(db, await a_contact(contacts))
    async with db.connection() as conn:  # proposed thirty hours ago
        await conn.execute(
            "UPDATE actions SET created_at = created_at - interval '30 hours' WHERE action_id = %s",
            (action.action_id,),
        )
    await decide(db, action, now=datetime.now(UTC))
    billing = ScriptedBilling(existing=BILLING_DOWN)

    await executor(db, billing, give_up_after=timedelta(hours=24)).run_once()

    waiting = await stored(db, action)
    assert waiting.status is ActionStatus.IN_FLIGHT  # deferred, to be tried again
    assert waiting.ready_at is not None
    assert waiting.ready_at > waiting.created_at + timedelta(hours=29)


async def test_the_retry_window_is_counted_by_the_ledgers_clock_not_by_the_approvers(
    db: Database, contacts: ContactRepository
) -> None:
    """When an action became runnable is a fact of the ledger, stamped by the database like
    every other time the executor judges an age by. Taken from the clock the decision was made
    by, a clock that reads a day behind would end an approved credit at Billing's first hiccup.
    """
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action, now=NOW - timedelta(days=30))
    billing = ScriptedBilling(existing=BILLING_DOWN)

    await executor(db, billing, give_up_after=timedelta(hours=24)).run_once()

    waiting = await stored(db, action)
    assert waiting.status is ActionStatus.IN_FLIGHT  # deferred, to be tried again
    assert waiting.ready_at is not None
    assert abs(waiting.ready_at - datetime.now(UTC)) < timedelta(seconds=30)


async def test_action_that_needs_no_approval_is_runnable_from_its_proposal(
    db: Database, contacts: ContactRepository
) -> None:
    ready = await proposed(db, await a_contact(contacts), level="none")
    waiting = await proposed(db, await a_contact(contacts), key="credit:S2:OUT-1")

    assert ready.ready_at is not None
    assert abs(ready.ready_at - ready.created_at) < timedelta(seconds=1)
    assert waiting.ready_at is None


@pytest.mark.parametrize(
    ("failure", "gave_up"),
    [
        (BILLING_DOWN, True),
        (Err(ErrorKind.INVALID, "bad request", status_code=422), False),
    ],
    ids=["no answer until the window closed", "refused outright"],
)
async def test_giving_up_is_told_apart_from_a_refusal(
    db: Database, contacts: ContactRepository, failure: Err, gave_up: bool
) -> None:
    """After giving up nobody knows whether an earlier attempt landed. A refusal is known."""
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    billing = ScriptedBilling(applied=failure)

    await executor(db, billing, give_up_after=timedelta(0)).run_once()

    ended = await stored(db, action)
    assert ended.status is ActionStatus.FAILED
    assert ended.gave_up is gave_up


# --- nothing on trust ----------------------------------------------------------------------------


async def test_stored_request_that_no_longer_matches_its_hash_is_not_sent(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    async with db.connection() as conn:  # the amount was changed behind the approval's back
        await conn.execute(
            "UPDATE actions SET request_body = jsonb_set(request_body, '{amount_inr}',"
            " '\"480.00\"') WHERE action_id = %s",
            (action.action_id,),
        )
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    blocked = await stored(db, action)
    assert blocked.status is ActionStatus.BLOCKED
    assert "hash" in (blocked.last_error or "")


async def test_amount_above_the_limit_needs_a_supervisor_whatever_the_ledger_says(
    db: Database, contacts: ContactRepository
) -> None:
    """The level is worked out again from the amount: INR 900 is above the INR 500 limit, so
    the agent's approval this row asked for is not enough."""
    action = await proposed(db, await a_contact(contacts), level="agent", amount_inr="900.00")
    await decide(db, action, role="agent")
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    blocked = await stored(db, action)
    assert blocked.status is ActionStatus.BLOCKED
    assert "supervisor" in (blocked.last_error or "")


async def test_supervisor_approval_covers_an_amount_that_needs_only_an_agent(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts), level="supervisor")
    await decide(db, action, role="supervisor")
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert len(billing.writes) == 1
    assert (await stored(db, action)).status is ActionStatus.SUCCEEDED


async def test_executor_that_reviews_every_credit_does_not_apply_one_proposed_without_review(
    db: Database, contacts: ContactRepository, handoffs: HandoffRepository
) -> None:
    """The two processes disagree about the deployment switch: the stricter reading wins."""
    action = await proposed(db, await a_contact(contacts), level="none")
    billing = ScriptedBilling()

    await executor(db, billing, review_all=True).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED
    assert [item.reason for item in await handoffs.list_open()] == ["ACTION_FAILED"]


async def test_credit_priced_under_another_policy_version_is_refused(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts), policy_version="v9-superseded")
    await decide(db, action)
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    refused = await stored(db, action)
    assert (refused.status, refused.last_error) == (ActionStatus.FAILED, "POLICY_VERSION_CHANGED")
    assert not refused.gave_up


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {**INPUTS, "monthly_fee_inr": "NaN"},
        {**INPUTS, "monthly_fee_inr": "-5"},
        {**INPUTS, "billing_cycle_day": 31},
        {**INPUTS, "service_type": "satellite"},
    ],
    ids=["nothing recorded", "fee not a number", "negative fee", "no such day", "unknown service"],
)
async def test_credit_whose_recorded_terms_cannot_be_read_is_blocked(
    db: Database, contacts: ContactRepository, inputs: dict[str, Any]
) -> None:
    action = await proposed(db, await a_contact(contacts), inputs=inputs)
    await decide(db, action)
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED


@pytest.mark.parametrize("missing", ["customer_id", "service_id", "outage_id", "amount_inr"])
async def test_credit_whose_request_lacks_what_identifies_it_is_blocked(
    db: Database, contacts: ContactRepository, missing: str
) -> None:
    action = await proposed(db, await a_contact(contacts), **{missing: ""})
    await decide(db, action)
    billing = ScriptedBilling()

    await executor(db, billing).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED


async def test_without_a_policy_the_executor_applies_no_credit(
    db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    billing = ScriptedBilling()

    await executor(db, billing, credit_policy=None).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).status is ActionStatus.BLOCKED


# --- the billing-cycle cap -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("already_credited", "applied"),
    [
        (MONTHLY_FEE - Decimal("120.50"), True),  # exactly reaches the cap
        (MONTHLY_FEE - Decimal("120.49"), False),  # one paisa over
    ],
    ids=["fits under the cap", "would pass the cap"],
)
async def test_cap_for_the_billing_cycle_is_enforced_when_the_credit_is_applied(
    db: Database,
    contacts: ContactRepository,
    handoffs: HandoffRepository,
    already_credited: Decimal,
    applied: bool,
) -> None:
    """Two outages in one cycle can each be priced before the other is applied. The second is
    checked against what is on the account when its turn comes."""
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    billing = ScriptedBilling(existing=account(another_outage(str(already_credited))))

    await executor(db, billing).run_once()

    ended = await stored(db, action)
    if applied:
        assert (len(billing.writes), ended.status) == (1, ActionStatus.SUCCEEDED)
        return
    assert billing.writes == []
    assert (ended.status, ended.last_error) == (ActionStatus.FAILED, "CAP_EXCEEDED")
    assert [item.reason for item in await handoffs.list_open()] == ["ACTION_FAILED"]


@pytest.mark.parametrize(
    "other",
    [
        {"created_at": datetime(2026, 11, 9, 18, 0, tzinfo=UTC)},  # 9 Nov 23:30 IST: last cycle
        {"service_id": "S2"},
        {"status": "REVERSED"},
        {"reason_code": "GOODWILL"},
    ],
    ids=["earlier cycle", "another service", "reversed", "not an outage credit"],
)
async def test_only_this_cycles_outage_credits_on_the_service_count_towards_the_cap(
    db: Database, contacts: ContactRepository, other: dict[str, Any]
) -> None:
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    billing = ScriptedBilling(existing=account(another_outage("950.00", **other)))

    await executor(db, billing).run_once()

    assert len(billing.writes) == 1
    assert (await stored(db, action)).status is ActionStatus.SUCCEEDED


async def test_cycle_turns_at_midnight_in_the_customers_time_zone(
    db: Database, contacts: ContactRepository
) -> None:
    """10 Nov 00:10 IST is 9 Nov 18:40 UTC: already the cycle that began on the 10th."""
    action = await proposed(db, await a_contact(contacts))
    await decide(db, action)
    this_cycle = another_outage("950.00", created_at=datetime(2026, 11, 9, 18, 40, tzinfo=UTC))
    billing = ScriptedBilling(existing=account(this_cycle))

    await executor(db, billing).run_once()

    assert billing.writes == []
    assert (await stored(db, action)).last_error == "CAP_EXCEEDED"


# --- what is on its way --------------------------------------------------------------------------


async def test_unsettled_credits_of_a_service_are_found_in_the_ledger(
    db: Database, contacts: ContactRepository
) -> None:
    actions = ActionRepository(db)
    waiting = await proposed(db, await a_contact(contacts))
    approved = await proposed(
        db, await a_contact(contacts), key="credit:S1:OUT-2", outage_id="OUT-2", amount_inr="75"
    )
    await decide(db, approved)
    rejected = await proposed(db, await a_contact(contacts), key="credit:S1:OUT-3")
    await decide(db, rejected, approve=False)
    await proposed(db, await a_contact(contacts), key="credit:S2:OUT-1", service_id="S2")
    # One whose amount cannot be read is listed and counts as nothing: it will never be applied.
    await proposed(db, await a_contact(contacts), key="credit:S1:OUT-4", amount_inr="a lot")

    found = {item.action_key: item.amount_inr for item in await actions.open_credits("S1")}

    assert found == {
        waiting.action_key: Decimal("120.50"),
        "credit:S1:OUT-2": Decimal("75"),
        "credit:S1:OUT-4": Decimal(0),
    }
    assert KEY in found
    assert BODY["service_id"] == "S1"


async def test_approval_past_its_time_is_no_longer_offered_even_before_the_sweep(
    db: Database, contacts: ContactRepository
) -> None:
    await proposed(db, await a_contact(contacts), ttl=timedelta(hours=1))
    approvals = ApprovalRepository(db)

    assert len(await approvals.list_pending("agent", now=NOW + timedelta(minutes=59))) == 1
    assert await approvals.list_pending("agent", now=NOW + timedelta(hours=1)) == []
