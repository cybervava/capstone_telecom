"""Service credits when the conversation does not go the straight way.

A second outage while the first credit still waits, a customer who has asked for a person in
the meantime, a credit that is on record already, a write nobody can confirm: in each case the
customer is told what became of every credit, in the amount that was frozen, and a credit that
was not applied reaches a person.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from skylink_care.db.approvals import Approval, DecisionOutcome
from tests.support import credits as ledger
from tests.support.credit_journeys import (
    credits_of,
    decide,
    expected_credit,
    handoff_reasons,
    pending,
    premium_customer,
    restored_after,
)
from tests.support.platform import Platform
from tests.support.stack import Simulator

SIX_HOURS = 6 * 3600


async def in_outage(
    platform: Platform,
    simulator: Simulator,
    customer: dict[str, Any],
    minutes: int,
    text: str = "internet down",
) -> tuple[Any, dict[str, Any]]:
    """The customer has contacted us about an outage that is still going on."""
    outage = await simulator.start_outage(customer["true_node_id"], started_minutes_ago=minutes)
    await platform.feed()
    intake = await platform.contact(text, authenticated_customer_id=customer["customer_id"])
    await platform.settle()
    return intake, outage


async def resolve(platform: Platform, simulator: Simulator, outage: dict[str, Any]) -> None:
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()


async def on_record(
    platform: Platform,
    customer: dict[str, Any],
    outage: dict[str, Any],
    contact_id: Any,
    amount: str,
    level: str = "agent",
) -> Any:
    """A credit for this service and outage that is in the ledger before the outage ends."""
    return await ledger.proposed(
        platform.runtime.db,
        contact_id,
        level=level,
        key=f"credit:{customer['service_id']}:{outage['outage_id']}",
        inputs={
            "monthly_fee_usd": customer["monthly_fee_usd"],
            "service_type": "broadband",
            "billing_cycle_day": 10,
        },
        asked_at=platform.runtime.now(),
        customer_id=customer["customer_id"],
        service_id=customer["service_id"],
        outage_id=outage["outage_id"],
        amount_usd=amount,
    )


def by_outage(approvals: list[Approval]) -> dict[str, Approval]:
    return {str(approval.summary["outage_id"]): approval for approval in approvals}


# --- more than one credit ------------------------------------------------------------------------


async def test_second_outage_while_the_first_credit_waits_loses_neither_outcome(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, first, _ = await restored_after(platform, simulator, customer, 300)
    platform.advance(SIX_HOURS)
    second = await simulator.start_outage(customer["true_node_id"], started_minutes_ago=240)
    await platform.feed()
    await platform.say(intake.contact_id, "internet is down again")
    await platform.settle()
    await resolve(platform, simulator, second)
    waiting = by_outage(await pending(platform, "agent"))
    assert set(waiting) == {first["outage_id"], second["outage_id"]}

    await decide(platform, waiting[first["outage_id"]], approve=True, role="agent")
    await platform.settle()
    await decide(platform, waiting[second["outage_id"]], approve=True, role="agent")
    await platform.settle()

    applied = {item["outage_id"]: item for item in await credits_of(simulator, customer)}
    assert set(applied) == {first["outage_id"], second["outage_id"]}
    told = [reply for reply in await platform.replies(intake.contact_id) if "CR-" in reply]
    assert len(told) == 2
    assert all(
        f"credit reference {item['credit_id']}" in "".join(told) for item in applied.values()
    )


async def test_first_credit_rejected_after_a_second_was_proposed_still_reaches_the_customer(
    platform: Platform, simulator: Simulator
) -> None:
    """The second credit must not make the contact forget that it waits for the first."""
    customer = await premium_customer(simulator)
    intake, first, _ = await restored_after(platform, simulator, customer, 300)
    platform.advance(SIX_HOURS)
    second = await simulator.start_outage(customer["true_node_id"], started_minutes_ago=240)
    await platform.feed()
    await platform.say(intake.contact_id, "internet is down again")
    await platform.settle()
    await resolve(platform, simulator, second)
    waiting = by_outage(await pending(platform, "agent"))

    await decide(platform, waiting[first["outage_id"]], approve=False, role="agent")
    await platform.settle()

    *_, told = await platform.replies(intake.contact_id)
    assert "has not been applied" in told
    assert "CREDIT_NOT_APPLIED" in await handoff_reasons(platform)

    # A person holds the contact now. The second credit's outcome is still owed.
    await decide(platform, waiting[second["outage_id"]], approve=True, role="agent")
    await platform.settle()

    *_, confirmation = await platform.replies(intake.contact_id)
    assert "has been applied to your account" in confirmation
    (credit,) = await credits_of(simulator, customer)
    assert credit["outage_id"] == second["outage_id"]


# --- the outcome arrives after the workflow let go ---------------------------------------------


@pytest.mark.parametrize("approve", [True, False], ids=["applied", "rejected"])
async def test_outcome_of_a_credit_is_told_after_the_contact_went_to_a_person(
    platform: Platform, simulator: Simulator, approve: bool
) -> None:
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)
    await platform.say(intake.contact_id, "I want to talk to a human agent")
    await platform.settle()
    handed_over = await platform.runtime.contacts.get(intake.contact_id)
    assert handed_over is not None
    assert handed_over.status == "HANDOFF"
    (approval,) = await pending(platform, "agent")

    await decide(platform, approval, approve=approve, role="agent")
    await platform.settle()
    await platform.settle()  # a second pass changes nothing: the news is told once

    replies = await platform.replies(intake.contact_id)
    amount = expected_credit(customer["monthly_fee_usd"], 300)
    if approve:
        applied = f"credit of ${amount} for your outage has been applied"
        assert sum(applied in reply for reply in replies) == 1
        assert "CREDIT_NOT_APPLIED" not in await handoff_reasons(platform)
        return
    assert sum("has not been applied" in reply for reply in replies) == 1
    follow_up = next(
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "CREDIT_NOT_APPLIED"
    )
    assert f"${amount}" in follow_up.summary
    assert "REJECTED" in follow_up.summary


# --- a credit that is on record already ----------------------------------------------------------


async def test_credit_on_record_with_another_amount_stands_and_the_difference_is_reviewed(
    platform: Platform, simulator: Simulator
) -> None:
    """Worked out again, the credit comes to something else. What was frozen is what can be
    approved and applied, so that is the amount the customer hears, at every step."""
    customer = await premium_customer(simulator)
    worked_out_now = expected_credit(customer["monthly_fee_usd"], 300)
    intake, outage = await in_outage(platform, simulator, customer, 300)
    await on_record(platform, customer, outage, intake.contact_id, "75.00")
    assert worked_out_now != Decimal("75.00")

    await resolve(platform, simulator, outage)

    *_, restored = await platform.replies(intake.contact_id)
    assert "credit of $75.00 for this outage is already being processed" in restored
    assert f"${worked_out_now}" not in restored
    assert "review the amount" in restored
    (review,) = await platform.runtime.handoffs.list_open()
    assert review.reason == "CREDIT_REVIEW"
    assert "On record: $75.00" in review.summary
    assert f"worked out now: ${worked_out_now}" in review.summary

    (approval,) = await pending(platform, "agent")
    assert approval.summary["amount_usd"] == "75.00"
    await decide(platform, approval, approve=True, role="agent")
    await platform.settle()

    (credit,) = await credits_of(simulator, customer)
    assert Decimal(credit["amount_usd"]) == Decimal("75.00")
    *_, confirmation = await platform.replies(intake.contact_id)
    assert "credit of $75.00 for your outage has been applied" in confirmation


@pytest.mark.parametrize(
    ("ended", "said", "reviewed"),
    [
        ("REJECTED", "looked at earlier and has not been applied", True),
        ("EXPIRED", "looked at earlier and has not been applied", True),
        ("FAILED", "looked at earlier and has not been applied", True),
        ("SUCCEEDED", "credit of ${amount} for this outage has already been applied", False),
        ("SKIPPED", "A service credit for this outage is already on your account", False),
    ],
)
async def test_credit_that_ended_earlier_is_reported_as_it_ended_and_not_waited_for(
    platform: Platform, simulator: Simulator, ended: str, said: str, reviewed: bool
) -> None:
    """An earlier conversation of the same customer got to this outage first and its credit
    has ended, one way or another. This one says what became of it and waits for nothing."""
    customer = await premium_customer(simulator)
    amount = str(expected_credit(customer["monthly_fee_usd"], 300))
    intake, outage = await in_outage(platform, simulator, customer, 300)
    earlier = await platform.contact(
        "no internet since morning", authenticated_customer_id=customer["customer_id"]
    )
    action = await on_record(platform, customer, outage, earlier.contact_id, amount, "none")
    async with platform.runtime.db.connection() as conn:
        await conn.execute(
            "UPDATE actions SET status = %s WHERE action_id = %s", (ended, action.action_id)
        )
    await platform.settle()

    await resolve(platform, simulator, outage)

    restored = next(
        reply
        for reply in await platform.replies(intake.contact_id)
        if "Your service should" in reply
    )
    assert said.format(amount=amount) in restored
    assert "you are due a service credit" not in restored
    reviews = [
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "CREDIT_REVIEW"
    ]
    assert bool(reviews) is reviewed
    if reviewed:
        assert f"ended {ended}" in reviews[0].summary
    waiting = await platform.runtime.contacts.get(intake.contact_id)
    assert waiting is not None
    assert waiting.status == "AWAITING_CUSTOMER"  # not held open for an outcome long past


async def test_credit_applied_through_another_conversation_is_not_proposed_again(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    amount = str(expected_credit(customer["monthly_fee_usd"], 300))
    intake, outage = await in_outage(
        platform, simulator, customer, 300, text="internet down, will I get a refund?"
    )
    earlier = await platform.contact(
        "no internet since morning", authenticated_customer_id=customer["customer_id"]
    )
    await on_record(platform, customer, outage, earlier.contact_id, amount)
    (approval,) = await pending(platform, "agent")
    assert await decide(platform, approval, approve=True, role="agent") is (DecisionOutcome.DECIDED)
    await platform.settle()
    assert len(await credits_of(simulator, customer)) == 1

    await resolve(platform, simulator, outage)

    restored = next(
        reply
        for reply in await platform.replies(intake.contact_id)
        if "Your service should" in reply
    )
    assert "a credit for this outage is already on your account" in restored
    assert await pending(platform, "supervisor") == []
    assert len(await credits_of(simulator, customer)) == 1
    assert "CREDIT_REVIEW" not in await handoff_reasons(platform)


async def test_credit_rejected_earlier_and_now_worth_something_else_is_reviewed_once(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, outage = await in_outage(platform, simulator, customer, 300)
    earlier = await platform.contact(
        "no internet since morning", authenticated_customer_id=customer["customer_id"]
    )
    rejected = await on_record(platform, customer, outage, earlier.contact_id, "75.00")
    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=False, role="agent")
    await platform.settle()

    await resolve(platform, simulator, outage)

    restored = next(
        reply
        for reply in await platform.replies(intake.contact_id)
        if "Your service should" in reply
    )
    assert "looked at earlier and has not been applied" in restored
    reviews = [
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "CREDIT_REVIEW" and item.contact_id == intake.contact_id
    ]
    assert len(reviews) == 1
    assert f"On record: $75.00 ({rejected.action_key}, REJECTED)" in reviews[0].summary


# --- the customer writes as the outcome arrives --------------------------------------------------


async def test_outcome_and_a_message_that_arrive_together_are_both_answered(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)
    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=True, role="agent")
    await platform.executor.run_once()  # applied; the news is waiting for the conversation
    before = len(await platform.replies(intake.contact_id))

    await platform.say(intake.contact_id, "working now, thanks")
    await platform.settle()

    confirmation, closing = (await platform.replies(intake.contact_id))[before:]
    assert "has been applied to your account" in confirmation
    assert "I've closed complaint" in closing
    closed = await platform.runtime.contacts.get(intake.contact_id)
    assert closed is not None
    assert closed.status == "CLOSED"


# --- a write nobody can confirm ------------------------------------------------------------------


async def test_credit_whose_outcome_is_unknown_is_not_reported_as_not_applied(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Billing stopped answering and the executor gave up. Whether an attempt landed is not
    known, and neither the customer nor the person who follows up is told that it did not."""
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)
    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=True, role="agent")
    await simulator.fault("billing", "down")
    monkeypatch.setattr(platform.executor, "_give_up_after", timedelta(0))

    await platform.settle()

    *_, told = await platform.replies(intake.contact_id)
    assert "could not confirm that the service credit" in told
    assert "has not been applied" not in told
    follow_up = next(
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "CREDIT_UNCONFIRMED"
    )
    assert "look for a credit under this key" in follow_up.summary
    assert f"credit:{customer['service_id']}:" in follow_up.summary
