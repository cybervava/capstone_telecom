"""Two journeys off the main road: a customer who disputes an outage, and a mobile customer.

The rule for the first: evidence decides. While an outage is active nothing is arranged for the
customer's own equipment, however sure they are; they are told why, and a customer who insists
again gets a person, with the evidence. For the second: mobile is a thin path. A tower outage
is told, tied to its ticket and restored like any other; what only exists for a broadband line
(a remote line test, router steps, a technician at the door) is not offered.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from tests.support.credit_journeys import credits_of, decide, pending
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def customer(simulator: Simulator, service_type: str = "broadband") -> dict[str, Any]:
    return await simulator.find_customer(
        service_type=service_type, mapping_defect="none", status="active"
    )


async def in_an_outage(
    platform: Platform, simulator: Simulator, person: dict[str, Any], text: str, **outage: Any
) -> tuple[Any, dict[str, Any], str]:
    started = await simulator.start_outage(person["true_node_id"], **outage)
    await platform.feed()
    intake = await platform.contact(text, authenticated_customer_id=person["customer_id"])
    await platform.settle()
    *_, reply = await platform.replies(intake.contact_id)
    return intake, started, reply


async def answer(platform: Platform, intake: Any, text: str) -> str:
    await platform.say(intake.contact_id, text)
    await platform.settle()
    *_, latest = await platform.replies(intake.contact_id)
    return latest


async def handoffs(platform: Platform) -> dict[str, str]:
    return {item.reason: item.summary for item in await platform.runtime.handoffs.list_open()}


async def tools_used(platform: Platform, intake: Any) -> set[str]:
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    return {record.step for record in trail if record.step.startswith("tool:")}


# --- the network says outage, the customer says device -------------------------------------------


async def test_customer_who_blames_the_router_during_an_outage_is_told_why_nothing_is_arranged(
    platform: Platform, simulator: Simulator
) -> None:
    person = await customer(simulator)

    intake, outage, reply = await in_an_outage(
        platform,
        simulator,
        person,
        "It is not an outage, my router is faulty. Send a technician to replace the router.",
    )

    assert f"confirmed outage in your area (outage {outage['outage_id']})" in reply
    assert "would not bring your service back while this outage is active" in reply
    assert "I'll test your line" in reply
    assert "Please try these steps" not in reply
    assert await handoffs(platform) == {}
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_NETWORK"
    state = (await simulator.state())["tables"]
    assert state["appointments"] == 0
    assert "tool:network.line_test" not in await tools_used(platform, intake)


async def test_customer_who_insists_again_gets_a_person_and_the_person_gets_the_evidence(
    platform: Platform, simulator: Simulator
) -> None:
    person = await customer(simulator)
    intake, outage, _ = await in_an_outage(
        platform, simulator, person, "my internet is down, I think my router is broken"
    )

    reply = await answer(platform, intake, "No. The problem is with my router, replace my router.")

    assert "I can't settle that here while the outage in your area is active" in reply
    assert "A member of our team will take over" in reply
    passed_on = await handoffs(platform)
    assert set(passed_on) == {"CUSTOMER_DISPUTES_OUTAGE"}
    summary = passed_on["CUSTOMER_DISPUTES_OUTAGE"]
    assert f"outage {outage['outage_id']}" in summary
    assert "insists_device_fault" in summary
    assert (await simulator.state())["tables"]["appointments"] == 0


async def test_question_during_the_outage_is_answered_and_is_not_a_dispute(
    platform: Platform, simulator: Simulator
) -> None:
    person = await customer(simulator)
    intake, outage, first = await in_an_outage(platform, simulator, person, "my internet is down")
    assert "would not bring your service back" not in first  # nobody disputed anything

    again = await answer(platform, intake, "any update on my internet?")
    once_more = await answer(platform, intake, "is the internet back yet?")

    assert f"outage {outage['outage_id']}" in again
    assert f"outage {outage['outage_id']}" in once_more
    assert await handoffs(platform) == {}


async def test_dispute_is_settled_by_a_line_test_once_the_outage_is_over(
    platform: Platform, simulator: Simulator
) -> None:
    person = await customer(simulator)
    await simulator.line_fault(person["service_id"], fault=False)
    intake, outage, _ = await in_an_outage(
        platform, simulator, person, "my internet is down and I am sure it is my router"
    )
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    reply = await answer(platform, intake, "it is still not working, it is the router I tell you")

    assert "a remote test of your line" in reply
    assert "tool:network.line_test" in await tools_used(platform, intake)
    assert "CUSTOMER_DISPUTES_OUTAGE" not in await handoffs(platform)


# --- mobile --------------------------------------------------------------------------------------


async def test_mobile_customer_in_a_tower_outage_is_told_ticketed_and_restored(
    platform: Platform, simulator: Simulator
) -> None:
    person = await customer(simulator, "mobile")

    intake, outage, reply = await in_an_outage(
        platform, simulator, person, "no signal on my mobile since morning", cause="TOWER_FAULT"
    )

    assert f"confirmed outage in your area (outage {outage['outage_id']})" in reply
    assert "a fault at the mobile tower" in reply
    assert "router" not in reply
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.service_type, contact.status) == ("mobile", "AWAITING_NETWORK")
    assert (await simulator.state())["tables"]["incidents"] == 1
    assert "tool:network.line_test" not in await tools_used(platform, intake)

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    *_, restored = await platform.replies(intake.contact_id)
    assert "Your service should be working again" in restored
    assert "restart your phone" in restored
    assert not [word for word in ("router", "technician", "your line") if word in restored]


async def test_mobile_customer_who_blames_the_phone_is_told_why_and_never_of_a_technician(
    platform: Platform, simulator: Simulator
) -> None:
    person = await customer(simulator, "mobile")

    _, outage, reply = await in_an_outage(
        platform,
        simulator,
        person,
        "no signal on my mobile, the problem is with my device, send an engineer",
        cause="TOWER_FAULT",
    )

    assert f"outage {outage['outage_id']}" in reply
    assert "would not bring your service back while this outage is active" in reply
    assert not [word for word in ("router", "technician", "your line") if word in reply]


async def test_mobile_customer_on_a_working_tower_goes_to_a_person_without_router_advice(
    platform: Platform, simulator: Simulator
) -> None:
    """There is no remote test for a mobile connection here and no guide for a phone. What can
    be said is what the network shows; the rest is for a person."""
    person = await customer(simulator, "mobile")

    intake = await platform.contact(
        "my mobile data is not working", authenticated_customer_id=person["customer_id"]
    )
    await platform.settle()

    *_, reply = await platform.replies(intake.contact_id)
    assert "no outage has been reported for your area" in reply
    assert "to check your connection" in reply
    assert not [word for word in ("router", "technician", "your line") if word in reply]
    assert "Please try these steps" not in reply
    assert set(await handoffs(platform)) == {"TROUBLESHOOTING"}
    assert (await simulator.state())["tables"]["appointments"] == 0
    assert "tool:network.line_test" not in await tools_used(platform, intake)


@pytest.mark.parametrize(
    ("minutes", "credited"),
    [(300, False), (26 * 60, True)],
    ids=["five hours: below the mobile minimum", "twenty-six hours"],
)
async def test_mobile_outage_is_credited_by_the_mobile_rule(
    platform: Platform, simulator: Simulator, minutes: int, credited: bool
) -> None:
    """A day at least, INR 25 at least, by the placeholder policy: not the broadband rule."""
    person = await customer(simulator, "mobile")
    intake, outage, _ = await in_an_outage(
        platform,
        simulator,
        person,
        "no network on my mobile, will I get a refund?",
        cause="TOWER_FAULT",
        started_minutes_ago=minutes,
    )
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    restored = next(
        reply
        for reply in await platform.replies(intake.contact_id)
        if "Your service should" in reply
    )
    if not credited:
        assert "shorter than the minimum the policy covers" in restored
        assert await pending(platform, "supervisor") == []
        return
    hours = -(-minutes // 60)
    amount = max(Decimal(person["monthly_fee_inr"]) * hours / 720, Decimal("25.00"))
    expected = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert f"service credit of INR {expected}" in restored
    (approval,) = await pending(platform, "agent")
    await decide(platform, approval, approve=True, role="agent")
    await platform.settle()
    (credit,) = await credits_of(simulator, person)
    assert Decimal(credit["amount_inr"]) == expected
