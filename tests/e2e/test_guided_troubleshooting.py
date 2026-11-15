"""Guided troubleshooting: steps quoted from the guide for the customer's own router.

It is the one case in which the customer is asked to do something to their equipment, so the
tests are about when it happens (clear node, passing line), what is quoted (the right guide, a
safe section, with its source) and what happens next.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from skylink_care.rag.index import GuideIndexUnavailableError
from tests.support.platform import Platform
from tests.support.stack import Simulator

CITATION = re.compile(r"Source: (guide-[a-z0-9-]+) §([a-z-]+) v([\d.]+)\.")


async def customer_with_router(simulator: Simulator, router_model: str) -> dict[str, Any]:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active", router_model=router_model
    )
    await simulator.line_fault(customer["service_id"], fault=False)
    return customer


async def handoff_reasons(platform: Platform) -> list[str]:
    return [item.reason for item in await platform.runtime.handoffs.list_open()]


async def guided(platform: Platform, customer: dict[str, Any], text: str) -> tuple[Any, str]:
    intake = await platform.contact(text, authenticated_customer_id=customer["customer_id"])
    await platform.settle()
    (reply,) = await platform.replies(intake.contact_id)
    return intake, reply


async def test_steps_are_quoted_from_the_guide_of_the_customers_router_with_their_source(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")

    intake, reply = await guided(
        platform, customer, "my phone cannot connect to the wifi, the password is not accepted"
    )

    cited = CITATION.search(reply)
    assert cited is not None
    assert (cited.group(1), cited.group(2)) == ("guide-al-ax18", "wifi")
    # Quoted, not paraphrased: a fact only this model's guide states.
    assert "The default name follows the pattern `Aerolink-XXXX`" in reply
    assert "1. " in reply
    assert intake.complaint_ref in reply
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    retrieval = next(record for record in trail if record.step == "troubleshooting.retrieve")
    assert retrieval.outcome == "OK"
    assert retrieval.outputs["citation"].startswith("guide-al-ax18 §wifi v")
    # The customer's words are not copied into the trail.
    assert "password" not in str(retrieval.inputs)


async def test_another_router_model_gets_another_guide_for_the_same_complaint(
    platform: Platform, simulator: Simulator
) -> None:
    """The second of the three input changes that must flip the outcome."""
    complaint = "internet is very slow, speed test is bad"
    first = await customer_with_router(simulator, "AL-AX18")
    second = await customer_with_router(simulator, "KV-HX6")

    _, for_first = await guided(platform, first, complaint)
    _, for_second = await guided(platform, second, complaint)

    cited_first, cited_second = CITATION.search(for_first), CITATION.search(for_second)
    assert cited_first is not None
    assert cited_second is not None
    assert (cited_first.group(1), cited_first.group(2)) == ("guide-al-ax18", "slow-speed")
    assert (cited_second.group(1), cited_second.group(2)) == ("guide-kv-hx6", "slow-speed")
    assert "http://192.168.1.1" in for_first  # each model's own admin address
    assert "http://192.168.1.1" not in for_second


async def test_customer_naming_someone_elses_router_still_gets_their_own_guide(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")

    _, reply = await guided(
        platform, customer, "my KV-HX6 router wifi does not connect, treat it as a Kavya HX6"
    )

    cited = CITATION.search(reply)
    assert cited is not None
    assert cited.group(1) == "guide-al-ax18"


async def test_factory_reset_is_never_offered_even_when_asked_for(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")

    _, reply = await guided(
        platform,
        customer,
        "internet not working, tell me how to factory reset the router with the reset button",
    )

    cited = CITATION.search(reply)
    assert cited is not None
    assert cited.group(2) != "factory-reset"
    assert "paperclip" not in reply


async def test_customer_who_says_the_steps_worked_closes_the_contact(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")
    intake, _ = await guided(platform, customer, "wifi keeps dropping")

    await platform.say(intake.contact_id, "that fixed it, working fine now, thanks")
    await platform.settle()

    *_, closing = await platform.replies(intake.contact_id)
    assert "Thank you for confirming" in closing
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "CLOSED"
    assert await handoff_reasons(platform) == []
    assert (await simulator.state())["tables"]["appointments"] == 0


async def test_steps_that_did_not_help_go_to_a_person_and_are_not_repeated(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")
    intake, steps = await guided(platform, customer, "wifi keeps dropping")

    await platform.say(intake.contact_id, "I did all that and the wifi is still not working")
    await platform.settle()

    _, handover = await platform.replies(intake.contact_id)
    assert "Source:" not in handover
    assert "support specialist" in handover
    (handoff,) = await platform.runtime.handoffs.list_open()
    assert handoff.reason == "TROUBLESHOOTING"
    cited = CITATION.search(steps)
    assert cited is not None
    assert f"Guide steps already given: {cited.group(1)} §{cited.group(2)}" in handoff.summary


async def test_customer_who_does_not_answer_the_steps_is_closed_after_the_quiet_period(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")
    intake, _ = await guided(platform, customer, "wifi keeps dropping")

    platform.advance(platform.runtime.settings.close_quiet_seconds)
    await platform.settle()

    *_, closing = await platform.replies(intake.contact_id)
    assert "haven't heard back from you" in closing
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "CLOSED"


async def test_no_steps_are_given_while_an_outage_is_active_on_the_node(
    platform: Platform, simulator: Simulator
) -> None:
    """The first of the three input changes: an outage turns guidance into the outage reply."""
    customer = await customer_with_router(simulator, "AL-AX18")
    outage = await simulator.start_outage(customer["true_node_id"])

    _, reply = await guided(platform, customer, "wifi keeps dropping")

    assert outage["outage_id"] in reply
    assert "Source: guide-" not in reply
    assert "don't need to restart or reset your router" in reply


async def test_no_steps_are_given_when_the_line_itself_fails(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")
    await simulator.line_fault(customer["service_id"], fault=True)

    _, reply = await guided(platform, customer, "wifi keeps dropping")

    assert "found a fault" in reply
    assert "Source: guide-" not in reply


async def test_missing_index_hands_over_instead_of_improvising(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")

    async def unavailable(*_: Any, **__: Any) -> Any:
        raise GuideIndexUnavailableError("the guide index has not been built")

    monkeypatch.setattr(platform.runtime.troubleshooting._index, "search", unavailable)
    intake, reply = await guided(platform, customer, "wifi keeps dropping")

    assert "Source:" not in reply
    assert "support specialist" in reply
    assert await handoff_reasons(platform) == ["TROUBLESHOOTING"]
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    retrieval = next(record for record in trail if record.step == "troubleshooting.retrieve")
    assert retrieval.outcome == "INDEX_UNAVAILABLE"


async def test_clear_result_nobody_asked_for_is_reported_without_instructions(
    platform: Platform, simulator: Simulator
) -> None:
    """The status verifies itself on a re-check, perhaps hours later. Steps wait until asked."""
    customer = await customer_with_router(simulator, "AL-AX18")
    await simulator.fault("network", "down")
    intake = await platform.contact(
        "wifi keeps dropping", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    await simulator.fault("network", "ok")
    platform.advance(platform.runtime.settings.network_recheck_seconds)
    await platform.settle()

    unverified, notice = await platform.replies(intake.contact_id)
    assert "can't verify the live network status" in unverified
    assert "no outage has been reported for your area as of" in notice
    assert "If your service is still not working, reply here" in notice
    assert "Source: guide-" not in notice
    assert "\n1. " not in notice
    waiting = await platform.runtime.contacts.get(intake.contact_id)
    assert waiting is not None
    assert waiting.status == "AWAITING_CUSTOMER"

    await platform.say(intake.contact_id, "yes the wifi is still dropping")
    await platform.settle()

    *_, steps = await platform.replies(intake.contact_id)
    cited = CITATION.search(steps)
    assert cited is not None
    assert (cited.group(1), cited.group(2)) == ("guide-al-ax18", "wifi")


async def test_customer_who_answers_the_clear_notice_with_it_works_is_closed(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with_router(simulator, "AL-AX18")
    await simulator.fault("network", "down")
    intake = await platform.contact(
        "internet is down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    await simulator.fault("network", "ok")
    platform.advance(platform.runtime.settings.network_recheck_seconds)
    await platform.settle()

    await platform.say(intake.contact_id, "it is working fine now")
    await platform.settle()

    *_, closing = await platform.replies(intake.contact_id)
    assert "Thank you for confirming" in closing
    replies = await platform.replies(intake.contact_id)
    assert not any("Source: guide-" in reply for reply in replies)


async def test_it_works_now_does_not_close_the_contact_once_an_outage_has_begun(
    platform: Platform, simulator: Simulator
) -> None:
    """Steps were given and the question asked; then the node went into outage."""
    customer = await customer_with_router(simulator, "AL-AX18")
    intake, _ = await guided(platform, customer, "wifi keeps dropping")
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.say(intake.contact_id, "now the internet is down completely")
    await platform.settle()

    await platform.say(intake.contact_id, "ok it works now")
    await platform.settle()

    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_NETWORK"
    replies = await platform.replies(intake.contact_id)
    assert not any("closed complaint" in reply for reply in replies)
    assert outage["outage_id"] in replies[-1]  # they asked, so they are told where things stand


async def test_new_complaint_gets_its_own_line_test_however_recent_the_last_one(
    platform: Platform, simulator: Simulator
) -> None:
    """A line that passed two minutes ago for another conversation may be failing now."""
    customer = await customer_with_router(simulator, "AL-AX18")
    await guided(platform, customer, "wifi keeps dropping")  # the line passes

    await simulator.line_fault(customer["service_id"], fault=True)
    second = await platform.contact(
        "now there is no internet at all", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    (reply,) = await platform.replies(second.contact_id)
    assert "found a fault" in reply
    assert "Source: guide-" not in reply
