"""The NOC event feed in the customer journey: confirmation, restoration and closure.

The feed consumer runs only when a test calls ``platform.feed()``, so each test states exactly
what the platform has been told. Time moves only when a test advances the shared clock.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from skylink_care.db.actions import ActionStatus
from tests.support.platform import Platform
from tests.support.stack import Simulator

ALL_CLEAR = re.compile(
    r"no outage has been reported|all clear|working normally|network is (?:fine|ok|up|healthy)",
    re.IGNORECASE,
)


async def broadband_customer(simulator: Simulator, **filters: Any) -> dict[str, Any]:
    defaults = {"service_type": "broadband", "mapping_defect": "none", "status": "active"}
    customer = await simulator.find_customer(**{**defaults, **filters})
    # The data pack seeds a few faulty lines. A test that wants one says so itself.
    await simulator.line_fault(customer["service_id"], fault=False)
    return customer


async def handoff_reasons(platform: Platform) -> list[str]:
    return [item.reason for item in await platform.runtime.handoffs.list_open()]


async def incidents_on(simulator: Simulator, node_id: str) -> list[dict[str, Any]]:
    found = await simulator.get("/incident/v1/incidents", node_id=node_id)
    return [dict(item) for item in found["incidents"]]


async def decision_reasons(platform: Platform, contact_id: Any) -> list[str]:
    trail = await platform.runtime.audit.for_contact(contact_id)
    return [record.outputs["reason"] for record in trail if record.step == "diagnostics.decide"]


async def told_about_outage(
    platform: Platform, simulator: Simulator
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """A customer who contacted us during a declared outage and was told about it."""
    customer = await broadband_customer(simulator)
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    return customer, outage, intake


# --- the feed as evidence --------------------------------------------------------------------


async def test_feed_proves_the_outage_while_the_network_api_is_down(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    outage = await simulator.start_outage(customer["true_node_id"], eta_minutes=120)
    await platform.feed()
    await simulator.fault("network", "down")

    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    (reply,) = await platform.replies(intake.contact_id)
    assert outage["outage_id"] in reply
    assert "expected back by" in reply
    assert await decision_reasons(platform, intake.contact_id) == ["FEED_OUTAGE"]
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.network_state, contact.outage_id) == ("ACTIVE", outage["outage_id"])
    assert await handoff_reasons(platform) == []


async def test_a_feed_that_has_gone_stale_proves_nothing(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    await simulator.fault("network", "down")
    platform.advance(platform.runtime.settings.feed_max_age_seconds + 1)

    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    (reply,) = await platform.replies(intake.contact_id)
    assert "can't verify the live network status" in reply
    assert f"showed an outage in your area (outage {outage['outage_id']})" in reply
    assert "can't confirm whether it has ended" in reply
    assert ALL_CLEAR.search(reply) is None
    assert await decision_reasons(platform, intake.contact_id) == ["STALE_LAST_KNOWN_OUTAGE"]


async def test_a_feed_that_cannot_be_read_is_recorded_and_stops_being_trusted(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    await platform.feed()
    await simulator.start_outage(customer["true_node_id"])
    await simulator.fault("network_feed", "down")
    await simulator.fault("network", "down")

    outcome = await platform.ingest.poll_once()

    assert outcome.error == "UNAVAILABLE"
    state = await platform.runtime.read_model.feed_state()
    assert state.last_error is not None
    assert "UNAVAILABLE" in state.last_error
    # The heartbeat is the last good poll: it ages from here, and the read model never learnt
    # of the outage, so with the live API down as well nothing can be claimed either way.
    platform.advance(platform.runtime.settings.feed_max_age_seconds + 1)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    assert await decision_reasons(platform, intake.contact_id) == ["NO_FRESH_EVIDENCE"]


# --- suspected fault to declared outage ------------------------------------------------------


async def test_suspected_fault_becomes_the_declared_outage_on_the_same_incident(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    node_id = customer["true_node_id"]
    outage = await simulator.start_outage(node_id, detect_after_seconds=480)
    await platform.feed()  # only the equipment alarm so far

    intake = await platform.contact(
        "internet stopped working a minute ago",
        authenticated_customer_id=customer["customer_id"],
    )
    await platform.settle()

    (suspected,) = await platform.replies(intake.contact_id)
    assert "signs of a possible network problem" in suspected
    (ticket,) = await incidents_on(simulator, node_id)
    assert (ticket["kind"], ticket["outage_id"]) == ("SUSPECTED_NODE_FAULT", None)
    assert ticket["complaint_refs"] == [intake.complaint_ref]

    platform.advance(480)  # the NOC declares the outage
    assert (await platform.feed()).declared == 1
    await platform.settle()

    _, confirmed = await platform.replies(intake.contact_id)
    assert outage["outage_id"] in confirmed
    assert "confirmed outage" in confirmed
    (ticket,) = await incidents_on(simulator, node_id)  # still the one ticket, now upgraded
    assert (ticket["kind"], ticket["outage_id"]) == ("NETWORK_OUTAGE", outage["outage_id"])
    assert ticket["complaint_refs"] == [intake.complaint_ref]
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.status, contact.outage_id) == ("AWAITING_NETWORK", outage["outage_id"])
    assert await handoff_reasons(platform) == []


async def test_second_customer_during_the_suspected_phase_joins_the_same_incident(
    platform: Platform, simulator: Simulator
) -> None:
    first = await broadband_customer(simulator)
    node_id = first["true_node_id"]
    neighbours = await simulator.find_customers(
        5, service_type="broadband", mapping_defect="none", status="active", node_id=node_id
    )
    second = next(item for item in neighbours if item["customer_id"] != first["customer_id"])
    await simulator.start_outage(node_id, detect_after_seconds=480)

    refs = []
    for person in (first, second):
        intake = await platform.contact(
            "no internet", authenticated_customer_id=person["customer_id"]
        )
        refs.append(intake.complaint_ref)
    await platform.settle()

    (ticket,) = await incidents_on(simulator, node_id)
    assert sorted(ticket["complaint_refs"]) == sorted(refs)
    assert await handoff_reasons(platform) == []


async def test_suspected_fault_that_clears_closes_its_ticket_and_moves_the_customer_on(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    node_id = customer["true_node_id"]
    alarm = await simulator.post(
        "/admin/v1/alarms", {"node_id": node_id, "alarm_type": "LOS", "severity": "critical"}
    )
    await platform.feed()
    intake = await platform.contact(
        "internet keeps cutting out", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    (ticket,) = await incidents_on(simulator, node_id)
    assert (ticket["kind"], ticket["status"]) == ("SUSPECTED_NODE_FAULT", "OPEN")

    await simulator.post(f"/admin/v1/alarms/{alarm['alarm_id']}/clear")
    await platform.feed()
    platform.advance(platform.runtime.settings.network_recheck_seconds)
    await platform.settle()

    (ticket,) = await incidents_on(simulator, node_id)
    assert ticket["status"] == "RESOLVED"
    assert await platform.runtime.episodes.find_open(node_id, "broadband") is None
    suspected, clear = await platform.replies(intake.contact_id)
    assert "signs of a possible network problem" in suspected
    assert "no outage has been reported for your area as of" in clear
    # Nobody asked just now, so the customer is told and asked, not instructed.
    assert "If your service is still not working, reply here" in clear
    assert "Source: guide-" not in clear
    assert await handoff_reasons(platform) == []


async def test_suspected_fault_nobody_declares_goes_to_a_person_after_the_longer_wait(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    customer = await broadband_customer(simulator)
    await simulator.post(
        "/admin/v1/alarms",
        {"node_id": customer["true_node_id"], "alarm_type": "LOS", "severity": "critical"},
    )
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    # Longer than a plain unverified status waits, because the NOC needs time to declare.
    for _ in range(settings.network_recheck_limit + 2):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()
    assert await handoff_reasons(platform) == []

    remaining = settings.suspected_wait_seconds // settings.network_recheck_seconds
    for _ in range(remaining):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()

    assert await handoff_reasons(platform) == ["NETWORK_STATUS_UNVERIFIED"]
    replies = await platform.replies(intake.contact_id)
    assert len(replies) == 2
    assert not any(ALL_CLEAR.search(reply) for reply in replies)


# --- told once ---------------------------------------------------------------------------------


async def test_customer_told_by_the_live_status_is_not_told_again_by_the_feed(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    outage = await simulator.start_outage(customer["true_node_id"])
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()  # told from the live status; the feed consumer has not run yet

    outcome = await platform.feed()
    await platform.settle()

    assert (outcome.declared, outcome.contacts_woken) == (1, 0)
    (reply,) = await platform.replies(intake.contact_id)
    assert outage["outage_id"] in reply


async def test_an_eta_update_does_not_repeat_what_the_customer_knows(
    platform: Platform, simulator: Simulator
) -> None:
    _, outage, intake = await told_about_outage(platform, simulator)

    await simulator.post(f"/admin/v1/outages/{outage['outage_id']}/update", {"eta_minutes": 45})
    await platform.feed()
    await platform.settle()
    assert len(await platform.replies(intake.contact_id)) == 1

    await platform.say(intake.contact_id, "any update on my internet?")
    await platform.settle()
    _, update = await platform.replies(intake.contact_id)
    assert "update from the field team" in update  # the customer asked, and gets the new ETA


# --- restoration and closure -------------------------------------------------------------------


async def test_waiting_customer_is_told_when_the_outage_is_resolved_and_can_confirm(
    platform: Platform, simulator: Simulator
) -> None:
    customer, outage, intake = await told_about_outage(platform, simulator)

    platform.advance(1800)
    await simulator.resolve_outage(outage["outage_id"])
    assert (await platform.feed()).resolved == 1
    await platform.settle()

    _, restored = await platform.replies(intake.contact_id)
    assert f"resolved the outage in your area (outage {outage['outage_id']}) at" in restored
    assert "restart your router once" in restored
    assert intake.complaint_ref in restored
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.status, contact.network_state) == ("AWAITING_CUSTOMER", "CONFIRMED_CLEAR")
    (ticket,) = await incidents_on(simulator, customer["true_node_id"])
    assert ticket["status"] == "RESOLVED"
    assert await platform.runtime.episodes.find_open(customer["true_node_id"], "broadband") is None

    await platform.say(intake.contact_id, "working now, thanks")
    await platform.settle()

    *_, closing = await platform.replies(intake.contact_id)
    assert "Thank you for confirming" in closing
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "CLOSED"
    assert contact.closed_at is not None
    assert await handoff_reasons(platform) == []
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    steps = [(record.step, record.outcome) for record in trail]
    assert ("restoration.told", "OK") in steps
    assert ("contact.closed", "CUSTOMER_CONFIRMED") in steps
    assert (await simulator.state())["tables"]["appointments"] == 0


async def waiting_on_one_outage(
    platform: Platform, simulator: Simulator, count: int
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
    """Customers on one node who contacted us during its declared outage and wait for its end."""
    anchor = await broadband_customer(simulator)
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
    outage = await simulator.start_outage(anchor["true_node_id"])
    await platform.feed()
    intakes = [
        await platform.contact("internet down", authenticated_customer_id=person["customer_id"])
        for person in people
    ]
    await platform.settle()
    return outage, people, intakes


async def test_customers_with_faults_of_their_own_do_not_make_a_restored_node_look_faulty(
    platform: Platform, simulator: Simulator
) -> None:
    """When an outage ends, every waiting line on the node is tested within minutes. Among so
    many, a few have faults of their own. They are no reason to doubt the node again, to keep
    the news from everyone on it, or to raise a second ticket with the NOC."""
    outage, people, intakes = await waiting_on_one_outage(platform, simulator, 20)
    for person in people[-3:]:
        await simulator.line_fault(person["service_id"], fault=True)

    platform.advance(1800)
    await simulator.resolve_outage(outage["outage_id"])
    assert (await platform.feed()).resolved == 1
    for intake in intakes:  # in the order they called: the three with a fault come last
        await platform.worker.process_contact(intake.contact_id)
    await platform.settle()

    for intake in intakes:
        *_, latest = await platform.replies(intake.contact_id)
        assert "resolved the outage in your area" in latest
        assert "SUSPECTED_NODE_FAULT" not in await decision_reasons(platform, intake.contact_id)
    (ticket,) = await incidents_on(simulator, people[0]["true_node_id"])
    assert (ticket["kind"], ticket["status"]) == ("NETWORK_OUTAGE", "RESOLVED")
    assert await handoff_reasons(platform) == []


async def test_faults_met_first_make_a_restored_node_suspect_only_until_more_lines_are_in(
    platform: Platform, simulator: Simulator
) -> None:
    """The other order: the three customers with a fault of their own happen to be tested
    first. Three failing of three is evidence against the node and is treated as such: the
    customers after them are told that the status cannot be verified, and a ticket is raised.
    With eleven lines tested, three failing are no more than coincidence explains. The rest
    hear that the outage is over, at their next look so do those who were kept waiting, and
    the ticket is closed again."""
    outage, people, intakes = await waiting_on_one_outage(platform, simulator, 20)
    node_id = people[0]["true_node_id"]
    for person in people[:3]:
        await simulator.line_fault(person["service_id"], fault=True)

    platform.advance(1800)
    await simulator.resolve_outage(outage["outage_id"])
    assert (await platform.feed()).resolved == 1
    for intake in intakes:  # in the order they called: the three with a fault come first
        await platform.worker.process_contact(intake.contact_id)
    await platform.settle()  # ... and those kept waiting are looked at again

    said = [await platform.replies(intake.contact_id) for intake in intakes]
    kept_waiting = [
        any("signs of a possible network problem" in reply for reply in replies) for replies in said
    ]
    assert kept_waiting == [False, False] + [True] * 8 + [False] * 10
    assert all("resolved the outage in your area" in replies[-1] for replies in said)
    assert await platform.runtime.episodes.find_open(node_id, "broadband") is None
    tickets = await incidents_on(simulator, node_id)
    assert sorted((ticket["kind"], ticket["status"]) for ticket in tickets) == [
        ("NETWORK_OUTAGE", "RESOLVED"),
        ("SUSPECTED_NODE_FAULT", "RESOLVED"),
    ]
    assert await handoff_reasons(platform) == []


async def test_a_node_whose_lines_go_on_failing_after_the_noc_resolved_is_suspected_again(
    platform: Platform, simulator: Simulator
) -> None:
    """The NOC reports the outage resolved and the live status is clear, but every line tested
    on the node still fails. Two customers hear of the resolution. From the third failing line
    on nobody is told that it is over, and the NOC gets the node back as a suspected fault."""
    outage, people, intakes = await waiting_on_one_outage(platform, simulator, 6)
    for person in people:
        await simulator.line_fault(person["service_id"], fault=True)

    platform.advance(1800)
    await simulator.resolve_outage(outage["outage_id"])
    assert (await platform.feed()).resolved == 1
    for intake in intakes:
        await platform.worker.process_contact(intake.contact_id)
    await platform.settle()

    latest = [(await platform.replies(intake.contact_id))[-1] for intake in intakes]
    told_restored = [reply for reply in latest if "resolved the outage in your area" in reply]
    told_suspected = [reply for reply in latest if "signs of a possible network problem" in reply]
    assert (len(told_restored), len(told_suspected)) == (2, 4)
    tickets = await incidents_on(simulator, people[0]["true_node_id"])
    assert sorted(ticket["kind"] for ticket in tickets) == [
        "NETWORK_OUTAGE",
        "SUSPECTED_NODE_FAULT",
    ]
    assert await handoff_reasons(platform) == []


async def test_restored_contact_nobody_answers_is_closed_after_the_quiet_period(
    platform: Platform, simulator: Simulator
) -> None:
    _, outage, intake = await told_about_outage(platform, simulator)
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    platform.advance(platform.runtime.settings.close_quiet_seconds - 60)
    await platform.settle()
    still_open = await platform.runtime.contacts.get(intake.contact_id)
    assert still_open is not None
    assert still_open.status == "AWAITING_CUSTOMER"

    platform.advance(60)
    await platform.settle()

    *_, closing = await platform.replies(intake.contact_id)
    assert "haven't heard back from you" in closing
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "CLOSED"
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    assert ("contact.closed", "NO_REPLY") in [(record.step, record.outcome) for record in trail]


async def test_customer_who_writes_after_the_contact_closed_reaches_a_person(
    platform: Platform, simulator: Simulator
) -> None:
    _, outage, intake = await told_about_outage(platform, simulator)
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()
    await platform.say(intake.contact_id, "it's back")
    await platform.settle()
    closed = await platform.runtime.contacts.get(intake.contact_id)
    assert closed is not None
    assert closed.status == "CLOSED"

    await platform.say(intake.contact_id, "it has stopped again")
    await platform.settle()

    assert await handoff_reasons(platform) == ["CUSTOMER_FOLLOW_UP"]
    reopened = await platform.runtime.contacts.get(intake.contact_id)
    assert reopened is not None
    assert (reopened.status, reopened.closed_at) == ("HANDOFF", None)


async def test_customer_who_says_it_still_fails_after_restoration_is_not_closed(
    platform: Platform, simulator: Simulator
) -> None:
    _, outage, intake = await told_about_outage(platform, simulator)
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    await platform.say(intake.contact_id, "no, my internet is still not working")
    await platform.settle()

    replies = await platform.replies(intake.contact_id)
    assert not any("closed complaint" in reply for reply in replies)
    assert "Source: guide-" in replies[-1]  # the network is clear now: on to the equipment
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_CUSTOMER"
    assert contact.closed_at is None


async def test_resolution_reported_by_the_feed_is_passed_on_while_the_network_api_is_down(
    platform: Platform, simulator: Simulator
) -> None:
    _, outage, intake = await told_about_outage(platform, simulator)
    await simulator.fault("network", "down")

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    _, restored = await platform.replies(intake.contact_id)
    assert f"resolved the outage in your area (outage {outage['outage_id']})" in restored
    assert ALL_CLEAR.search(restored) is None  # a report from the NOC, not a check of the line
    assert (await decision_reasons(platform, intake.contact_id))[-1] == "CLEAR_NOT_CONFIRMED"


async def test_resolved_outage_is_not_called_restored_while_another_one_is_active(
    platform: Platform, simulator: Simulator
) -> None:
    customer, first, intake = await told_about_outage(platform, simulator)
    second = await simulator.start_outage(customer["true_node_id"], cause="POWER_FAILURE")
    await platform.feed()
    await platform.settle()

    await simulator.resolve_outage(first["outage_id"])
    await platform.feed()
    await platform.settle()

    replies = await platform.replies(intake.contact_id)
    assert not any("Your service should be working again" in reply for reply in replies)
    assert second["outage_id"] in replies[-1]
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.status, contact.outage_id) == ("AWAITING_NETWORK", second["outage_id"])


async def test_resolution_and_a_new_outage_arriving_together_do_not_read_as_restored(
    platform: Platform, simulator: Simulator
) -> None:
    """Both events reach the conversation in one batch: the riskier order of the two."""
    customer, first, intake = await told_about_outage(platform, simulator)
    second = await simulator.start_outage(customer["true_node_id"], cause="POWER_FAILURE")
    await simulator.resolve_outage(first["outage_id"])

    await platform.feed()
    await platform.settle()

    replies = await platform.replies(intake.contact_id)
    assert len(replies) == 2
    assert not any("Your service should be working again" in reply for reply in replies)
    assert second["outage_id"] in replies[-1]
    tickets = await incidents_on(simulator, customer["true_node_id"])
    # One ticket per outage, each closed with its own outage; never two open at once.
    assert [(t["outage_id"], t["status"]) for t in tickets] == [
        (first["outage_id"], "RESOLVED"),
        (second["outage_id"], "OPEN"),
    ]


async def test_lost_resolution_event_is_caught_by_the_watch_on_the_live_status(
    platform: Platform, simulator: Simulator
) -> None:
    """The feed never delivers the resolution; the customer must not wait for it for ever."""
    _, outage, intake = await told_about_outage(platform, simulator)
    await simulator.resolve_outage(outage["outage_id"])  # the consumer does not hear of it

    platform.advance(platform.runtime.settings.outage_recheck_seconds)
    await platform.settle()

    _, restored = await platform.replies(intake.contact_id)
    assert f"(outage {outage['outage_id']}) is no longer active on our network" in restored
    assert " at " not in restored.split("Your service")[0]  # no resolution time is invented
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert (contact.status, contact.network_state) == ("AWAITING_CUSTOMER", "CONFIRMED_CLEAR")


async def test_long_outage_is_watched_quietly_and_never_handed_off_for_lasting(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    _, _, intake = await told_about_outage(platform, simulator)

    for _ in range(settings.network_recheck_limit + 3):
        platform.advance(settings.outage_recheck_seconds)
        await platform.settle()

    assert len(await platform.replies(intake.contact_id)) == 1
    assert await handoff_reasons(platform) == []
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_NETWORK"


async def test_off_topic_remark_while_waiting_on_an_outage_does_not_end_the_wait(
    platform: Platform, simulator: Simulator
) -> None:
    _, outage, intake = await told_about_outage(platform, simulator)

    await platform.say(intake.contact_id, "this is useless, what a stupid company")
    await platform.settle()
    waiting = await platform.runtime.contacts.get(intake.contact_id)
    assert waiting is not None
    assert waiting.status == "AWAITING_NETWORK"

    platform.advance(platform.runtime.settings.close_quiet_seconds)
    await platform.settle()
    assert not any(
        "haven't heard back" in reply for reply in await platform.replies(intake.contact_id)
    )

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()
    *_, restored = await platform.replies(intake.contact_id)
    assert "resolved the outage" in restored


async def test_outage_resolved_while_the_reply_was_being_prepared_is_followed_by_restored(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The NOC resolves the outage after the turn read the status and before it answered.

    The customer is told what was true a moment earlier; the correction must follow by itself,
    and the ticket raised by the stale turn must not stay open.
    """
    customer = await broadband_customer(simulator)
    node_id = customer["true_node_id"]
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    real = platform.runtime.episodes.open_or_join
    once = {"pending": True}

    async def resolved_meanwhile(*args: Any, **kwargs: Any) -> Any:
        if once["pending"]:
            once["pending"] = False
            await simulator.resolve_outage(outage["outage_id"])
            await platform.feed()
        return await real(*args, **kwargs)

    monkeypatch.setattr(platform.runtime.episodes, "open_or_join", resolved_meanwhile)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    active, restored = await platform.replies(intake.contact_id)
    assert outage["outage_id"] in active
    assert f"resolved the outage in your area (outage {outage['outage_id']})" in restored
    (ticket,) = await incidents_on(simulator, node_id)
    assert ticket["status"] == "RESOLVED"
    assert await platform.runtime.episodes.find_open(node_id, "broadband") is None
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_CUSTOMER"


async def test_contact_abandoned_at_the_identity_question_is_closed_quietly(
    platform: Platform, simulator: Simulator
) -> None:
    intake = await platform.contact("my internet is not working")
    await platform.settle()

    platform.advance(platform.runtime.settings.close_quiet_seconds)
    await platform.settle()

    asked, closing = await platform.replies(intake.contact_id)
    assert "customer ID" in asked
    assert "haven't heard back from you" in closing
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "CLOSED"
    actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
    assert [action.status for action in actions] == [ActionStatus.SUCCEEDED]  # the registration
