"""Journeys that end in a service credit: what the end-to-end credit tests share.

The expected amounts are worked out by hand from the placeholder policy
(``data/policies/credit_policy.v0.yaml``): fee x billable hours x tier multiplier / 720, at
least $50 for broadband, rounded to the cent. They are not taken from the engine under test.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from skylink_care.db.approvals import Approval, DecisionOutcome
from tests.support.platform import Platform
from tests.support.stack import Simulator

SUPERVISOR_ABOVE = Decimal("500.00")
MINIMUM = Decimal("50.00")


def expected_credit(monthly_fee: str, outage_minutes: int) -> Decimal:
    """The broadband credit for an outage of this length, by the policy document."""
    hours = -(-outage_minutes // 60)
    multiplier = Decimal("2.0") if outage_minutes >= 4320 else Decimal("1.0")
    if 1440 <= outage_minutes < 4320:
        multiplier = Decimal("1.5")
    amount = Decimal(monthly_fee) * hours * multiplier / 720
    amount = min(max(amount, MINIMUM), Decimal(monthly_fee))
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def minutes_for(monthly_fee: str, *, above_limit: bool) -> int:
    """An outage length, in whole hours, just under or just over the supervisor limit."""
    hours = next(
        h for h in range(4, 24 * 40) if expected_credit(monthly_fee, h * 60) > SUPERVISOR_ABOVE
    )
    return (hours if above_limit else hours - 1) * 60


async def premium_customer(simulator: Simulator) -> dict[str, Any]:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active", min_monthly_fee=1499
    )
    await simulator.line_fault(customer["service_id"], fault=False)
    return customer


async def restored_after(
    platform: Platform,
    simulator: Simulator,
    customer: dict[str, Any],
    minutes: int,
    *,
    text: str = "internet down",
    **outage: Any,
) -> tuple[Any, dict[str, Any], str]:
    """The customer contacts us during an outage that has lasted ``minutes``; it is then resolved.

    Returns the intake, the outage and the message that told the customer it was over.
    """
    started = await simulator.start_outage(
        customer["true_node_id"], started_minutes_ago=minutes, **outage
    )
    await platform.feed()
    intake = await platform.contact(text, authenticated_customer_id=customer["customer_id"])
    await platform.settle()
    await simulator.resolve_outage(started["outage_id"])
    await platform.feed()
    await platform.settle()
    restored = next(
        reply
        for reply in await platform.replies(intake.contact_id)
        if "Your service should" in reply
    )
    return intake, started, restored


async def pending(platform: Platform, role: str) -> list[Approval]:
    return await platform.runtime.approvals.list_pending(role, now=platform.runtime.now())


async def decide(
    platform: Platform, approval: Approval, *, approve: bool, role: str, who: str = "staff-1"
) -> DecisionOutcome:
    result = await platform.runtime.approvals.decide(
        approval.approval_id,
        approve=approve,
        decided_by=who,
        role=role,
        request_hash=approval.request_hash,
        now=platform.runtime.now(),
    )
    return result.outcome


async def credits_of(simulator: Simulator, customer: dict[str, Any]) -> list[dict[str, Any]]:
    found = await simulator.get(f"/billing/v1/accounts/{customer['customer_id']}/credits")
    return [dict(item) for item in found["credits"]]


async def credits_of_unchecked(simulator: Simulator, customer: dict[str, Any]) -> Any:
    """The account's credits, or "unavailable" while Billing is failing."""
    try:
        return await credits_of(simulator, customer)
    except httpx.HTTPStatusError:
        return "unavailable"


async def handoff_reasons(platform: Platform) -> list[str]:
    return [item.reason for item in await platform.runtime.handoffs.list_open()]
