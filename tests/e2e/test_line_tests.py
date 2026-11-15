"""Remote line tests in the journey: the hold before a repair, and lines failing together.

The rule under test: probing is free, acting is gated. A failed line never leads straight to a
repair visit, because the first minutes of an undeclared node fault look exactly the same.
"""

from __future__ import annotations

from typing import Any

from tests.support.platform import Platform
from tests.support.stack import Simulator


async def healthy_node_customers(simulator: Simulator, count: int) -> list[dict[str, Any]]:
    """Customers on one node with no outage, each with a line that passes its test."""
    anchor = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    people = await simulator.find_customers(
        count,
        service_type="broadband",
        mapping_defect="none",
        status="active",
        node_id=anchor["true_node_id"],
    )
    assert len(people) == count, "the data pack should give a node with enough customers"
    for person in people:
        await simulator.line_fault(person["service_id"], fault=False)
    return people


async def handoff_reasons(platform: Platform) -> list[str]:
    return [item.reason for item in await platform.runtime.handoffs.list_open()]


async def line_tests_run(platform: Platform, contact_id: Any) -> int:
    trail = await platform.runtime.audit.for_contact(contact_id)
    return sum(1 for record in trail if record.step == "tool:network.line_test")


async def contact_with_failing_line(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> Any:
    await simulator.line_fault(customer["service_id"], fault=True)
    intake = await platform.contact(
        "my internet is down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    return intake


async def test_failing_line_on_a_clear_node_is_held_before_any_repair_is_arranged(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    (customer,) = await healthy_node_customers(simulator, 1)

    intake = await contact_with_failing_line(platform, simulator, customer)

    (hold,) = await platform.replies(intake.contact_id)
    assert "no outage has been reported for your area as of" in hold
    assert "found a fault" in hold
    assert "checking whether it is part of a wider network problem" in hold
    assert f"up to {settings.dispatch_hold_seconds // 60} minutes" in hold
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.status, contact.network_state) == ("AWAITING_NETWORK", "CONFIRMED_CLEAR")
    assert await handoff_reasons(platform) == []

    # The hold is watched in silence: nothing is said or queued until it is over.
    elapsed = 0
    while elapsed + settings.network_recheck_seconds < settings.dispatch_hold_seconds:
        platform.advance(settings.network_recheck_seconds)
        elapsed += settings.network_recheck_seconds
        await platform.settle()
    assert len(await platform.replies(intake.contact_id)) == 1
    assert await handoff_reasons(platform) == []

    tests_before = await line_tests_run(platform, intake.contact_id)
    platform.advance(settings.network_recheck_seconds)
    await platform.settle()

    # The verdict rests on a test made now, not on the one that started the hold.
    assert await line_tests_run(platform, intake.contact_id) == tests_before + 1
    _, verdict = await platform.replies(intake.contact_id)
    assert "fault on your line is still there" in verdict
    # Only now, with the hold over and the fault confirmed afresh, is a visit even offered,
    # and it is offered, not booked (the journey from here is in test_visits.py).
    assert "needs a technician at your address" in verdict
    assert await handoff_reasons(platform) == []
    state = (await simulator.state())["tables"]
    assert (state["appointments"], state["incidents"]) == (0, 0)  # nothing booked, no node ticket


async def test_line_that_recovers_during_the_hold_is_not_sent_for_repair(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)

    await simulator.line_fault(customer["service_id"], fault=False)
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()

    hold, verdict = await platform.replies(intake.contact_id)
    assert "found a fault" in hold
    assert "passed" in verdict
    assert "technician" not in verdict
    assert await handoff_reasons(platform) == []  # the line is fine: steps to try, not a repair


async def test_hold_ends_with_the_outage_when_the_noc_declares_one(
    platform: Platform, simulator: Simulator
) -> None:
    """The reason for the hold: what looked like one faulty line was the start of an outage."""
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)

    platform.advance(180)
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    await platform.settle()

    hold, confirmed = await platform.replies(intake.contact_id)
    assert "found a fault" in hold
    assert outage["outage_id"] in confirmed
    assert "no technician visit is needed" in confirmed
    assert await handoff_reasons(platform) == []
    state = (await simulator.state())["tables"]
    assert (state["appointments"], state["incidents"]) == (0, 1)


async def test_three_lines_failing_on_one_node_are_treated_as_a_node_fault(
    platform: Platform, simulator: Simulator
) -> None:
    """No alarm and no declared outage: only the lines themselves give the node away."""
    settings = platform.runtime.settings
    people = await healthy_node_customers(simulator, settings.line_test_cluster_threshold)
    node_id = people[0]["true_node_id"]

    intakes = [await contact_with_failing_line(platform, simulator, person) for person in people]

    *held, suspected = [await platform.replies(intake.contact_id) for intake in intakes]
    assert all("found a fault" in replies[0] for replies in held)
    assert "signs of a possible network problem" in suspected[0]
    assert await handoff_reasons(platform) == []

    # At their next re-check the customers who were being held learn the same thing.
    platform.advance(settings.network_recheck_seconds)
    await platform.settle()

    for intake in intakes[:-1]:
        *_, latest = await platform.replies(intake.contact_id)
        assert "signs of a possible network problem" in latest
    found = await simulator.get("/incident/v1/incidents", node_id=node_id)
    (ticket,) = found["incidents"]
    assert ticket["kind"] == "SUSPECTED_NODE_FAULT"
    assert sorted(ticket["complaint_refs"]) == sorted(intake.complaint_ref for intake in intakes)
    assert await handoff_reasons(platform) == []
    assert (await simulator.state())["tables"]["appointments"] == 0


async def test_one_customer_asking_repeatedly_cannot_make_a_node_look_faulty(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)

    for _ in range(settings.line_test_cluster_threshold + 1):
        platform.advance(settings.line_retest_seconds + 1)  # each turn runs a new test
        await platform.say(intake.contact_id, "is my internet fixed yet?")
        await platform.settle()

    assert await line_tests_run(platform, intake.contact_id) > settings.line_test_cluster_threshold
    replies = await platform.replies(intake.contact_id)
    assert not any("signs of a possible network problem" in reply for reply in replies)
    assert (await simulator.state())["tables"]["incidents"] == 0


async def test_line_is_not_tested_again_within_the_retest_interval(
    platform: Platform, simulator: Simulator
) -> None:
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)

    await platform.say(intake.contact_id, "any news on my internet?")
    await platform.settle()

    assert await line_tests_run(platform, intake.contact_id) == 1
    first, second = await platform.replies(intake.contact_id)
    assert "found a fault" in first
    assert "found a fault" in second  # the customer asked, so they are answered again


async def test_no_line_test_is_run_while_an_outage_is_known_on_the_node(
    platform: Platform, simulator: Simulator
) -> None:
    (customer,) = await healthy_node_customers(simulator, 1)
    await simulator.start_outage(customer["true_node_id"])

    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    assert await line_tests_run(platform, intake.contact_id) == 0


async def test_hold_from_an_earlier_fault_does_not_shorten_the_next_one(
    platform: Platform, simulator: Simulator
) -> None:
    """Hold, then an outage, then restoration. A fault found hours later is held from scratch."""
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    await platform.settle()
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    platform.advance(2 * 3600)  # long after the first hold would have ended
    await platform.say(intake.contact_id, "my internet is still not working")
    await platform.settle()

    *_, latest = await platform.replies(intake.contact_id)
    assert "found a fault" in latest
    assert "checking whether it is part of a wider network problem" in latest
    assert await handoff_reasons(platform) == []
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_NETWORK"


async def test_status_blip_during_the_hold_does_not_end_it_as_unverified(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)
    for _ in range(settings.network_recheck_limit + 1):  # more looks than an unverified status gets
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()

    await simulator.fault("network", "down")
    platform.advance(settings.network_recheck_seconds)
    await platform.settle()
    await simulator.fault("network", "ok")

    assert await handoff_reasons(platform) == []
    assert len(await platform.replies(intake.contact_id)) == 1
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()
    # The hold ended on its own time and for its own reason: the line still fails.
    assert await handoff_reasons(platform) == []
    *_, verdict = await platform.replies(intake.contact_id)
    assert "needs a technician at your address" in verdict


async def test_line_that_cannot_be_tested_again_after_the_hold_is_not_called_healthy(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    (customer,) = await healthy_node_customers(simulator, 1)
    intake = await contact_with_failing_line(platform, simulator, customer)

    await simulator.line_fault(customer["service_id"], fault=True, result="INCONCLUSIVE")
    rechecks = settings.dispatch_hold_seconds // settings.network_recheck_seconds
    for _ in range(rechecks + settings.network_recheck_limit + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()

    replies = await platform.replies(intake.contact_id)
    assert not any("passed" in reply for reply in replies)
    assert "couldn't test it again to confirm" in replies[-1]
    (handoff,) = await platform.runtime.handoffs.list_open()
    assert handoff.reason == "LINE_TEST_UNAVAILABLE"
    assert "Line fault first seen at" in handoff.summary
    assert (await simulator.state())["tables"]["appointments"] == 0
