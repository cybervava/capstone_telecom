"""Service credits: offered at restoration from the policy, approved by a person, applied once.

The expected amounts are worked out by hand from the placeholder policy, in
``tests.support.credit_journeys``. They are not taken from the engine under test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from skylink_care.db.actions import CREDIT_APPLY, ActionStatus
from skylink_care.db.approvals import DecisionOutcome
from tests.support.credit_journeys import (
    SUPERVISOR_ABOVE,
    credits_of,
    credits_of_unchecked,
    decide,
    expected_credit,
    handoff_reasons,
    minutes_for,
    pending,
    premium_customer,
    restored_after,
)
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def test_eligible_outage_is_offered_a_credit_that_waits_for_approval_then_is_applied(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    amount = expected_credit(customer["monthly_fee_inr"], 300)

    intake, outage, restored = await restored_after(platform, simulator, customer, 300)

    assert f"service credit of INR {amount}" in restored
    assert "(v0-placeholder)" in restored
    assert "sent for approval" in restored
    assert await credits_of(simulator, customer) == []  # proposed, not applied
    (approval,) = await pending(platform, "agent")
    assert approval.required_role == "agent"
    assert approval.summary["amount_inr"] == str(amount)
    assert approval.summary["working"]["billable_hours"] == 5
    assert approval.summary["complaint_ref"] == intake.complaint_ref

    assert await decide(platform, approval, approve=True, role="agent") is DecisionOutcome.DECIDED
    await platform.settle()

    (credit,) = await credits_of(simulator, customer)
    assert Decimal(credit["amount_inr"]) == amount
    assert (credit["outage_id"], credit["policy_version"]) == (
        outage["outage_id"],
        "v0-placeholder",
    )
    assert (credit["approved_by"], credit["complaint_ref"]) == ("staff-1", intake.complaint_ref)
    *_, confirmation = await platform.replies(intake.contact_id)
    assert f"service credit of INR {amount}" in confirmation
    assert f"credit reference {credit['credit_id']}" in confirmation
    assert await handoff_reasons(platform) == []
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    steps = [(record.step, record.outcome) for record in trail]
    assert ("credit.evaluate", "ELIGIBLE") in steps
    assert ("action:CREDIT_APPLY", "SUCCEEDED") in steps
    assert ("credit.outcome_told", "SUCCEEDED") in steps


@pytest.mark.parametrize(("above_limit", "role"), [(False, "agent"), (True, "supervisor")])
async def test_outage_length_decides_who_must_approve_the_credit(
    platform: Platform, simulator: Simulator, above_limit: bool, role: str
) -> None:
    """The third input change of the M2 gate: one hour more moves the credit past INR 500."""
    customer = await premium_customer(simulator)
    minutes = minutes_for(customer["monthly_fee_inr"], above_limit=above_limit)
    amount = expected_credit(customer["monthly_fee_inr"], minutes)
    assert (amount > SUPERVISOR_ABOVE) is above_limit

    await restored_after(platform, simulator, customer, minutes)

    (approval,) = await pending(platform, "supervisor")
    assert approval.required_role == role
    assert approval.summary["amount_inr"] == str(amount)
    assert len(await pending(platform, "agent")) == (0 if above_limit else 1)


async def test_agent_cannot_approve_what_needs_a_supervisor(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    minutes = minutes_for(customer["monthly_fee_inr"], above_limit=True)
    await restored_after(platform, simulator, customer, minutes)
    (approval,) = await pending(platform, "supervisor")

    refused = await decide(platform, approval, approve=True, role="agent")
    await platform.settle()

    assert refused is DecisionOutcome.WRONG_ROLE
    assert await credits_of(simulator, customer) == []

    assert await decide(platform, approval, approve=True, role="supervisor", who="sup-7") is (
        DecisionOutcome.DECIDED
    )
    await platform.settle()
    (credit,) = await credits_of(simulator, customer)
    assert credit["approved_by"] == "sup-7"


async def test_rejected_credit_is_not_applied_and_a_person_follows_up(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)
    (approval,) = await pending(platform, "agent")

    await decide(platform, approval, approve=False, role="agent")
    await platform.settle()

    assert await credits_of(simulator, customer) == []
    *_, told = await platform.replies(intake.contact_id)
    assert "has not been applied" in told
    (handoff,) = await platform.runtime.handoffs.list_open()
    assert handoff.reason == "CREDIT_NOT_APPLIED"
    assert "REJECTED" in handoff.summary
    action = await platform.runtime.actions.get(approval.action_id)
    assert action is not None
    assert action.status is ActionStatus.REJECTED


async def test_approval_nobody_gives_expires_and_the_customer_is_not_left_waiting(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)

    platform.advance(platform.runtime.settings.approval_ttl_seconds + 1)
    await platform.settle()

    assert await pending(platform, "supervisor") == []
    assert await credits_of(simulator, customer) == []
    replies = await platform.replies(intake.contact_id)
    assert any("has not been applied" in reply for reply in replies)
    assert "CREDIT_NOT_APPLIED" in await handoff_reasons(platform)


async def test_decision_on_a_request_the_approver_did_not_see_is_refused(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    await restored_after(platform, simulator, customer, 300)
    (approval,) = await pending(platform, "agent")

    result = await platform.runtime.approvals.decide(
        approval.approval_id,
        approve=True,
        decided_by="staff-1",
        role="agent",
        request_hash="0" * 64,
        now=platform.runtime.now(),
    )
    await platform.settle()

    assert result.outcome is DecisionOutcome.STALE
    assert await credits_of(simulator, customer) == []
    assert len(await pending(platform, "agent")) == 1


@pytest.mark.parametrize(
    ("text", "explained"),
    [("internet down", False), ("internet down, I want a refund for this", True)],
)
async def test_short_outage_earns_no_credit_and_says_why_only_when_asked(
    platform: Platform, simulator: Simulator, text: str, explained: bool
) -> None:
    customer = await premium_customer(simulator)

    _, _, restored = await restored_after(platform, simulator, customer, 90, text=text)

    assert "you are due a service credit" not in restored
    assert ("shorter than the minimum the policy covers" in restored) is explained
    assert await pending(platform, "supervisor") == []
    assert await credits_of(simulator, customer) == []


async def test_planned_maintenance_earns_no_credit(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)

    _, _, restored = await restored_after(
        platform,
        simulator,
        customer,
        600,
        text="internet down, will I get compensation?",
        planned=True,
        cause="PLANNED_MAINTENANCE",
    )

    assert "planned maintenance that was announced in advance is not covered" in restored
    assert await pending(platform, "supervisor") == []


async def test_second_complaint_about_the_same_outage_does_not_earn_a_second_credit(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    outage = await simulator.start_outage(customer["true_node_id"], started_minutes_ago=300)
    await platform.feed()
    first = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    second = await platform.contact(
        "still no internet", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    told = [(await platform.replies(intake.contact_id))[-1] for intake in (first, second)]
    assert sum("you are due a service credit" in reply for reply in told) == 1
    assert sum("already being processed for your account" in reply for reply in told) == 1
    # The same credit seen from a second conversation is nothing for a person to review.
    assert await handoff_reasons(platform) == []
    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=True, role="agent")
    await platform.settle()
    assert len(await credits_of(simulator, customer)) == 1


async def test_customer_who_confirms_before_the_credit_is_settled_stays_open_until_it_is(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)

    await platform.say(intake.contact_id, "working now, thanks")
    await platform.settle()

    *_, held_open = await platform.replies(intake.contact_id)
    assert "open until the service credit is settled" in held_open
    waiting = await platform.runtime.contacts.get(intake.contact_id)
    assert waiting is not None
    assert (waiting.status, waiting.closed_at) == ("AWAITING_APPROVAL", None)

    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=True, role="agent")
    await platform.settle()

    *_, confirmation = await platform.replies(intake.contact_id)
    assert "has been applied to your account" in confirmation
    closed = await platform.runtime.contacts.get(intake.contact_id)
    assert closed is not None
    assert closed.status == "CLOSED"


async def test_unreadable_account_sends_the_credit_question_to_a_person(
    platform: Platform, simulator: Simulator
) -> None:
    """Not knowing whether a credit is due is not the same as none being due."""
    customer = await premium_customer(simulator)
    outage = await simulator.start_outage(customer["true_node_id"], started_minutes_ago=300)
    await platform.feed()
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    await simulator.fault("billing", "down")

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    *_, restored = await platform.replies(intake.contact_id)
    assert "could not check your account for a service credit" in restored
    assert "you are due a service credit" not in restored
    assert await handoff_reasons(platform) == ["CREDIT_REVIEW"]
    assert await pending(platform, "supervisor") == []


async def test_billing_failing_while_the_credit_is_applied_delays_it_and_applies_it_once(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await premium_customer(simulator)
    intake, _, _ = await restored_after(platform, simulator, customer, 300)
    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=True, role="agent")
    await simulator.fault("billing", "down")

    await platform.executor.run_once()  # the account cannot be read: nothing is written blind
    assert await credits_of_unchecked(simulator, customer) == "unavailable"
    await simulator.fault("billing", "ok")
    await platform.settle()

    assert len(await credits_of(simulator, customer)) == 1
    action = await platform.runtime.actions.get(approval.action_id)
    assert action is not None
    assert action.status is ActionStatus.SUCCEEDED
    assert action.attempts > 1
    replies = await platform.replies(intake.contact_id)
    assert sum("credit reference CR-" in reply for reply in replies) == 1


async def test_credit_at_or_under_the_limit_is_applied_without_review_when_configured(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision D3 (23 Nov): the stack runs this way. Review-everything stays the default of
    the code. It is one setting for the whole deployment: who proposes and who applies must
    both have it."""
    monkeypatch.setattr(platform.runtime.resolution, "_review_all", False)
    monkeypatch.setattr(platform.executor.handler(CREDIT_APPLY), "_review_all", False)
    customer = await premium_customer(simulator)

    intake, _, restored = await restored_after(platform, simulator, customer, 300)

    assert "It is being applied to your account" in restored
    assert await pending(platform, "supervisor") == []
    (credit,) = await credits_of(simulator, customer)
    assert credit["approved_by"] is None
    *_, confirmation = await platform.replies(intake.contact_id)
    assert "has been applied to your account" in confirmation
