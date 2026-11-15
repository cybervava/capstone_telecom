"""Proactive notifications: affected customers are told before they call, if they agreed to be.

Consent is the customer's and can change at any moment, so what counts is the consent record at
the moment of sending. These tests change it at each interesting point and check what reached the
(simulated) SMS and push gateway.
"""

from __future__ import annotations

from typing import Any

from skylink_care.agents.notifications import OUTAGE_RESOLVED, OUTAGE_STARTED, action_key
from skylink_care.db.actions import ActionStatus
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def neighbours_on_one_node(simulator: Simulator) -> tuple[str, list[dict[str, Any]]]:
    """A node and the active, correctly mapped broadband customers on it."""
    anchor = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    node_id = str(anchor["true_node_id"])
    people = await simulator.find_customers(
        50, service_type="broadband", mapping_defect="none", status="active", node_id=node_id
    )
    assert len(people) >= 4, "the data pack should give a node with several customers"
    return node_id, people


async def set_consent(simulator: Simulator, customer: dict[str, Any], agreed: bool) -> None:
    body: dict[str, Any] = {"customer_id": customer["customer_id"], "outage_notifications": agreed}
    if agreed:
        body["channel"] = "sms"
    await simulator.post("/admin/v1/consent", body)


async def sent_for(simulator: Simulator, outage_id: str) -> list[dict[str, Any]]:
    """Messages the gateway accepted for an outage, oldest first."""
    found = await simulator.get("/notify/v1/messages", outage_id=outage_id, limit=200)
    return [dict(item) for item in reversed(found["messages"])]


def templates_to(messages: list[dict[str, Any]], customer: dict[str, Any]) -> list[str]:
    return [
        message["template_id"]
        for message in messages
        if message["customer_id"] == customer["customer_id"]
    ]


async def test_customers_on_the_node_are_told_about_a_declared_outage_before_they_call(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    opted_out, opted_in = people[0], people[1]
    await set_consent(simulator, opted_out, agreed=False)
    await set_consent(simulator, opted_in, agreed=True)
    agreed = {person["customer_id"] for person in people[2:] if person["consent"]}
    agreed.add(opted_in["customer_id"])

    outage = await simulator.start_outage(node_id, eta_minutes=90)
    outcome = await platform.feed()
    await platform.settle()

    assert outcome.declared == 1
    messages = await sent_for(simulator, outage["outage_id"])
    recipients = [message["customer_id"] for message in messages]
    assert len(recipients) == len(set(recipients)), "nobody is told twice"
    assert agreed <= set(recipients)
    assert opted_out["customer_id"] not in recipients
    (message,) = [item for item in messages if item["customer_id"] == opted_in["customer_id"]]
    assert (message["template_id"], message["channel"]) == (OUTAGE_STARTED, "sms")
    assert outage["outage_id"] in message["body"]
    assert "Expected back by" in message["body"]
    assert "do not need to restart your router" in message["body"]
    # The text is the same for everyone on the node: nothing personal is in it.
    assert opted_in["customer_id"] not in message["body"]
    assert opted_in["phone"][-4:] not in message["body"]

    skipped = await platform.runtime.actions.get_by_key(
        action_key(opted_out["customer_id"], outage["outage_id"], OUTAGE_STARTED)
    )
    assert skipped is not None
    assert (skipped.status, skipped.last_error) == (ActionStatus.SKIPPED, "NO_CONSENT")
    assert await platform.runtime.handoffs.list_open() == []  # a skip alerts nobody
    assert (await simulator.state())["tables"]["incidents"] == 0  # no ticket without a complaint


async def test_every_recipient_had_agreed_to_outage_messages(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, _ = await neighbours_on_one_node(simulator)
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    await platform.settle()

    messages = await sent_for(simulator, outage["outage_id"])
    assert messages
    for message in messages:
        consent = await simulator.get(f"/crm/v1/customers/{message['customer_id']}/consent")
        assert consent["outage_notifications"] is True
        assert consent["channel"] == message["channel"]


async def test_consent_withdrawn_after_the_message_was_queued_stops_it(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    changed_mind = people[0]
    await set_consent(simulator, changed_mind, agreed=True)
    outage = await simulator.start_outage(node_id)
    await platform.feed()  # the message is now queued for sending

    await set_consent(simulator, changed_mind, agreed=False)
    await platform.settle()

    messages = await sent_for(simulator, outage["outage_id"])
    assert templates_to(messages, changed_mind) == []
    action = await platform.runtime.actions.get_by_key(
        action_key(changed_mind["customer_id"], outage["outage_id"], OUTAGE_STARTED)
    )
    assert action is not None
    assert (action.status, action.last_error) == (ActionStatus.SKIPPED, "NO_CONSENT")


async def test_update_and_repeat_delivery_of_the_feed_send_nothing_more(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, _ = await neighbours_on_one_node(simulator)
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    await platform.settle()
    first = await sent_for(simulator, outage["outage_id"])

    await simulator.post(f"/admin/v1/outages/{outage['outage_id']}/update", {"eta_minutes": 20})
    await platform.feed()
    # The consumer loses its place and reads the whole feed again.
    async with platform.runtime.db.connection() as conn:
        await conn.execute("UPDATE feed_state SET applied_seq = 0")
    replayed = await platform.feed()
    await platform.settle()

    assert (replayed.declared, replayed.notifications, replayed.contacts_woken) == (0, 0, 0)
    assert await sent_for(simulator, outage["outage_id"]) == first


async def test_restored_goes_only_to_those_who_were_told_it_started(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    told, opted_in_later, left = people[0], people[1], people[2]
    await set_consent(simulator, told, agreed=True)
    await set_consent(simulator, left, agreed=True)
    await set_consent(simulator, opted_in_later, agreed=False)
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    await platform.settle()

    await set_consent(simulator, opted_in_later, agreed=True)  # agrees while the outage is on
    await set_consent(simulator, left, agreed=False)  # withdraws while the outage is on
    platform.advance(900)
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    messages = await sent_for(simulator, outage["outage_id"])
    assert templates_to(messages, told) == [OUTAGE_STARTED, OUTAGE_RESOLVED]
    assert templates_to(messages, left) == [OUTAGE_STARTED]
    assert templates_to(messages, opted_in_later) == []  # no "restored" out of nowhere
    restored = next(
        item
        for item in messages
        if item["customer_id"] == told["customer_id"] and item["template_id"] == OUTAGE_RESOLVED
    )
    assert "has been resolved at" in restored["body"]
    never_proposed = await platform.runtime.actions.get_by_key(
        action_key(opted_in_later["customer_id"], outage["outage_id"], OUTAGE_RESOLVED)
    )
    assert never_proposed is None
    assert await platform.runtime.handoffs.list_open() == []


async def test_outage_learnt_late_is_recorded_but_not_announced(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    await set_consent(simulator, people[0], agreed=True)
    outage = await simulator.start_outage(node_id)

    # The consumer was down and catches up long after the NOC declared the outage.
    platform.advance(platform.runtime.settings.notify_max_event_age_seconds + 60)
    outcome = await platform.feed()
    await platform.settle()

    assert (outcome.declared, outcome.notifications) == (1, 0)
    assert await sent_for(simulator, outage["outage_id"]) == []
    known = await platform.runtime.read_model.get_outage(outage["outage_id"])
    assert known is not None
    assert known.is_open


async def test_notification_gateway_down_delays_messages_without_losing_or_repeating_them(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    await set_consent(simulator, people[0], agreed=True)
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    await simulator.fault("notify", "response_loss")

    await platform.executor.run_once()  # messages are accepted, the answers are lost
    await simulator.fault("notify", "ok")
    await platform.settle()

    messages = await sent_for(simulator, outage["outage_id"])
    assert templates_to(messages, people[0]) == [OUTAGE_STARTED]
    recipients = [message["customer_id"] for message in messages]
    assert len(recipients) == len(set(recipients))
    action = await platform.runtime.actions.get_by_key(
        action_key(people[0]["customer_id"], outage["outage_id"], OUTAGE_STARTED)
    )
    assert action is not None
    assert action.status is ActionStatus.SUCCEEDED
    assert action.attempts > 1


async def test_consent_lost_between_attempts_does_not_hide_a_message_that_went_out(
    platform: Platform, simulator: Simulator
) -> None:
    """The first attempt delivered but its answer was lost; then the customer withdrew consent.

    The record must say the message was sent, because it was.
    """
    node_id, people = await neighbours_on_one_node(simulator)
    person = people[0]
    await set_consent(simulator, person, agreed=True)
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    await simulator.fault("notify", "response_loss")
    await platform.executor.run_once()
    await simulator.fault("notify", "ok")

    await set_consent(simulator, person, agreed=False)
    await platform.settle()

    action = await platform.runtime.actions.get_by_key(
        action_key(person["customer_id"], outage["outage_id"], OUTAGE_STARTED)
    )
    assert action is not None
    assert action.status is ActionStatus.SUCCEEDED
    assert action.external_ref is not None
    assert templates_to(await sent_for(simulator, outage["outage_id"]), person) == [OUTAGE_STARTED]


async def test_crm_down_holds_notifications_back_instead_of_sending_unchecked(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    await set_consent(simulator, people[0], agreed=True)
    outage = await simulator.start_outage(node_id)
    await platform.feed()
    await simulator.fault("crm", "down")

    await platform.executor.run_once()

    assert await sent_for(simulator, outage["outage_id"]) == []
    action = await platform.runtime.actions.get_by_key(
        action_key(people[0]["customer_id"], outage["outage_id"], OUTAGE_STARTED)
    )
    assert action is not None
    assert action.status is ActionStatus.IN_FLIGHT  # waiting to be tried again

    await simulator.fault("crm", "ok")
    await platform.settle()
    assert templates_to(await sent_for(simulator, outage["outage_id"]), people[0]) == [
        OUTAGE_STARTED
    ]


async def test_notifications_never_delay_a_complaint_registration(
    platform: Platform, simulator: Simulator
) -> None:
    node_id, people = await neighbours_on_one_node(simulator)
    outage = await simulator.start_outage(node_id)
    await platform.feed()  # a queue of notifications is waiting
    intake = await platform.contact(
        "internet down", authenticated_customer_id=people[0]["customer_id"]
    )

    claimed = await platform.runtime.actions.claim_runnable(limit=1, visibility_seconds=0.01)

    assert [action.action_type for action in claimed] == ["COMPLAINT_REGISTER"]
    assert claimed[0].contact_id == intake.contact_id
    assert outage["outage_id"]
