"""Technician visits: offered on evidence, booked for the slot the customer chose, once.

The rule under test: a visit needs a line that still fails after the hold on a node proven
clear, the customer's own choice of a slot, and no visit booked for the service already. The
executor checks all of it again before it books. Nothing is booked on a guess or on evidence
that has gone stale.
"""

from __future__ import annotations

from typing import Any

import pytest

from skylink_care.db.actions import VISIT_BOOK, ActionStatus
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def customer_on_a_healthy_node(simulator: Simulator) -> dict[str, Any]:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    await simulator.line_fault(customer["service_id"], fault=False)
    return customer


async def offered_a_visit(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> tuple[Any, str]:
    """The customer's line fails, the hold runs out, the line still fails: a visit is offered."""
    settings = platform.runtime.settings
    await simulator.line_fault(customer["service_id"], fault=True)
    intake = await platform.contact(
        "my internet is down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()
    *_, offer = await platform.replies(intake.contact_id)
    assert "I can book a visit in one of these slots" in offer
    return intake, offer


async def answer(platform: Platform, intake: Any, text: str) -> str:
    await platform.say(intake.contact_id, text)
    await platform.settle()
    *_, latest = await platform.replies(intake.contact_id)
    return latest


async def appointments(simulator: Simulator, customer: dict[str, Any]) -> list[dict[str, Any]]:
    found = await simulator.get("/field/v1/appointments", service_id=customer["service_id"])
    return [dict(item) for item in found["appointments"]]


async def visit_actions(platform: Platform, intake: Any) -> list[Any]:
    actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
    return [action for action in actions if action.action_type == VISIT_BOOK]


async def handoff_reasons(platform: Platform) -> list[str]:
    return [item.reason for item in await platform.runtime.handoffs.list_open()]


async def offered_slot_ids(platform: Platform, intake: Any) -> list[str]:
    """The slots of the latest offer, as recorded with the message that made it."""
    messages = await platform.runtime.messages.list_for_contact(intake.contact_id)
    offers = [m for m in messages if (m.meta or {}).get("template_id", "").startswith("visit_")]
    return list(offers[-1].meta["facts"]["slots"])


def slots_in(offer: str) -> list[str]:
    return [line for line in offer.splitlines() if line[:2] in {"1.", "2.", "3."}]


# --- the offer -----------------------------------------------------------------------------------


async def test_visit_is_offered_with_slots_and_nothing_is_booked_until_the_customer_chooses(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)

    intake, offer = await offered_a_visit(platform, simulator, customer)

    assert len(slots_in(offer)) == platform.runtime.settings.visit_slots_offered
    assert 'Reply with the number of the slot you want (or "yes" for the first one)' in offer
    assert await appointments(simulator, customer) == []
    assert await visit_actions(platform, intake) == []
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_CUSTOMER"
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    decided = [record.outcome for record in trail if record.step == "dispatch.decide"]
    assert decided == ["NO_CONSENT"]  # everything but the customer's word was there


@pytest.mark.parametrize(("reply", "position"), [("2", 1), ("yes please", 0), ("the third one", 2)])
async def test_chosen_slot_is_booked_once_and_confirmed_with_its_appointment(
    platform: Platform, simulator: Simulator, reply: str, position: int
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, offer = await offered_a_visit(platform, simulator, customer)
    wanted = slots_in(offer)[position].split(". ", 1)[1]

    confirmation = await answer(platform, intake, reply)

    (appointment,) = await appointments(simulator, customer)
    assert appointment["status"] == "BOOKED"
    assert f"Your technician visit is booked for {wanted}" in confirmation
    assert f"appointment {appointment['appointment_id']}" in confirmation
    replies = await platform.replies(intake.contact_id)
    assert sum(f"I'm booking a technician visit for {wanted}" in r for r in replies) == 1
    (action,) = await visit_actions(platform, intake)
    assert action.status is ActionStatus.SUCCEEDED
    assert action.request_body["complaint_ref"] == intake.complaint_ref
    assert action.inputs["consent_message_id"] is not None
    assert await handoff_reasons(platform) == []
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    steps = [(record.step, record.outcome) for record in trail]
    assert ("visit.answer", "CHOSEN") in steps
    assert ("visit.recheck", "ELIGIBLE") in steps  # the executor's own look, before booking


async def test_refusal_books_nothing_and_a_change_of_mind_gets_a_fresh_offer(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)

    declined = await answer(platform, intake, "no thanks")

    assert "I haven't booked a visit" in declined
    assert await appointments(simulator, customer) == []
    assert await handoff_reasons(platform) == []

    again = await answer(platform, intake, "actually my internet is still down, send a technician")

    assert "I can book a visit in one of these slots" in again
    assert await appointments(simulator, customer) == []


async def test_answer_that_names_no_slot_is_asked_again_and_then_goes_to_a_person(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)

    asked_again = await answer(platform, intake, "how much will it cost me?")

    assert "I won't book a visit on a guess" in asked_again
    assert await appointments(simulator, customer) == []

    handed_over = await answer(platform, intake, "whenever")

    assert "I still couldn't tell whether you would like a technician visit" in handed_over
    assert await handoff_reasons(platform) == ["LINE_FAULT"]
    assert await appointments(simulator, customer) == []


async def test_offer_nobody_answers_goes_to_a_person_instead_of_being_closed(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)

    platform.advance(platform.runtime.settings.close_quiet_seconds + 1)
    await platform.settle()

    assert await handoff_reasons(platform) == ["VISIT_OFFER_UNANSWERED"]
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "HANDOFF"
    assert await appointments(simulator, customer) == []


@pytest.mark.parametrize(
    "reply",
    ["not 2", "I can't do 2", "ok how much will it cost", "wait a second", "it works ok"],
)
async def test_words_that_only_sound_like_a_choice_book_nothing(
    platform: Platform, simulator: Simulator, reply: str
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)

    await answer(platform, intake, reply)

    assert await appointments(simulator, customer) == []
    assert await visit_actions(platform, intake) == []


async def test_fault_report_while_an_offer_is_open_is_answered_with_a_fresh_offer(
    platform: Platform, simulator: Simulator
) -> None:
    """Not every message after an offer is an answer to it. This one is a customer still
    without service: they are offered the slots again, not told their reply made no sense."""
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)

    reply = await answer(platform, intake, "any update? my internet is still not working")

    assert "I can book a visit in one of these slots" in reply
    assert "on a guess" not in reply
    assert await handoff_reasons(platform) == []
    assert await appointments(simulator, customer) == []


async def test_customer_who_says_it_works_now_is_not_sent_a_technician(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)

    reply = await answer(platform, intake, "it is working now, thanks")

    assert "I've closed complaint" in reply
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "CLOSED"
    assert await appointments(simulator, customer) == []
    assert await handoff_reasons(platform) == []


# --- evidence that changes -----------------------------------------------------------------------


async def test_outage_declared_after_the_offer_means_no_visit_however_the_customer_answers(
    platform: Platform, simulator: Simulator
) -> None:
    """The truck roll this platform exists to prevent: the fault was the node after all."""
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    await platform.settle()

    reply = await answer(platform, intake, "1")

    assert "confirmed outage in your area" in reply
    assert await appointments(simulator, customer) == []
    assert await visit_actions(platform, intake) == []


async def test_line_that_passes_when_the_customer_answers_is_not_sent_a_technician(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await simulator.line_fault(customer["service_id"], fault=False)

    reply = await answer(platform, intake, "yes")

    assert "a remote test of your line" in reply
    assert "passed" in reply
    assert await appointments(simulator, customer) == []
    assert await visit_actions(platform, intake) == []


async def test_outage_between_the_choice_and_the_booking_stops_the_executor(
    platform: Platform, simulator: Simulator
) -> None:
    """The proposal was sound when it was made. The executor looks for itself and declines."""
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()  # the choice is read and the booking proposed
    (proposed,) = await visit_actions(platform, intake)
    assert proposed.status is ActionStatus.READY

    await simulator.start_outage(customer["true_node_id"])
    await platform.settle()

    assert await appointments(simulator, customer) == []
    (declined,) = await visit_actions(platform, intake)
    assert (declined.status, declined.last_error) == (ActionStatus.SKIPPED, "OUTAGE_ACTIVE")
    *_, latest = await platform.replies(intake.contact_id)
    assert "confirmed outage in your area" in latest
    assert await handoff_reasons(platform) == []  # declined on purpose: nobody to alert


async def test_network_that_cannot_be_read_at_booking_time_books_nothing(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()

    await simulator.fault("network", "down")
    await platform.executor.run_once()

    assert await appointments(simulator, customer) == []
    (declined,) = await visit_actions(platform, intake)
    assert (declined.status, declined.last_error) == (ActionStatus.SKIPPED, "NETWORK_UNVERIFIED")


async def test_booking_that_ends_after_a_person_took_over_is_still_told_to_the_customer(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "2")
    await platform.worker.run_once()  # the booking is proposed
    await platform.say(intake.contact_id, "I want to talk to a human agent")
    await platform.worker.run_once()  # and the contact goes to a person before it is made
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "HANDOFF"

    await platform.settle()
    await platform.settle()  # a second pass changes nothing

    (appointment,) = await appointments(simulator, customer)
    replies = await platform.replies(intake.contact_id)
    confirmed = [reply for reply in replies if "Your technician visit is booked for" in reply]
    assert len(confirmed) == 1
    assert f"appointment {appointment['appointment_id']}" in confirmed[0]
    assert await handoff_reasons(platform) == ["CUSTOMER_REQUESTED_HUMAN"]


async def test_booking_declined_after_a_person_took_over_reaches_that_person(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()
    await platform.say(intake.contact_id, "I want to talk to a human agent")
    await platform.worker.run_once()
    await simulator.start_outage(customer["true_node_id"])

    await platform.settle()

    assert await appointments(simulator, customer) == []
    *_, latest = await platform.replies(intake.contact_id)
    assert "I couldn't book a technician visit" in latest
    reasons = await handoff_reasons(platform)
    assert set(reasons) == {"CUSTOMER_REQUESTED_HUMAN", "VISIT_NOT_BOOKED"}
    note = next(
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "VISIT_NOT_BOOKED"
    )
    assert "SKIPPED: OUTAGE_ACTIVE" in note.summary


# --- a booking that does not go straight through -----------------------------------------------


async def test_question_while_the_booking_is_being_made_does_not_lose_it(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()  # proposed; the executor has not run yet

    await platform.say(intake.contact_id, "any update? my internet is still not working")
    await platform.worker.run_once()

    *_, meanwhile = await platform.replies(intake.contact_id)
    assert "is still being booked" in meanwhile
    assert "I can book a visit in one of these slots" not in meanwhile

    await platform.settle()

    *_, confirmation = await platform.replies(intake.contact_id)
    assert "Your technician visit is booked for" in confirmation
    assert len(await appointments(simulator, customer)) == 1
    assert len(await visit_actions(platform, intake)) == 1


async def test_booking_declined_for_an_outage_does_not_block_the_one_asked_for_afterwards(
    platform: Platform, simulator: Simulator
) -> None:
    """The first booking was declined: the node went down. Once that is over and the line
    still fails, the customer chooses again, and that choice is a booking of its own."""
    settings = platform.runtime.settings
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.settle()
    assert await appointments(simulator, customer) == []

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()
    replies = await platform.replies(intake.contact_id)
    assert any("Your service should be working again" in reply for reply in replies)
    held = await answer(platform, intake, "the outage is over but my internet is still down")
    assert "found a fault" in held  # a new fault report: a new hold, before anything else
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 2):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()
    *_, offer = await platform.replies(intake.contact_id)
    assert "I can book a visit in one of these slots" in offer

    confirmation = await answer(platform, intake, "1")

    assert "Your technician visit is booked for" in confirmation
    first, second = await visit_actions(platform, intake)
    assert (first.status, second.status) == (ActionStatus.SKIPPED, ActionStatus.SUCCEEDED)
    assert first.action_key != second.action_key
    assert len(await appointments(simulator, customer)) == 1


async def test_booking_declined_on_a_passing_blip_is_proposed_again_and_made(
    platform: Platform, simulator: Simulator
) -> None:
    """The executor could not read the network and declined. By the time the conversation
    looks again everything holds, the customer has said nothing new, and their choice stands."""
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()
    await simulator.fault("network", "down")
    await platform.executor.run_once()
    await simulator.fault("network", "ok")

    await platform.settle()

    declined, made = await visit_actions(platform, intake)
    assert (declined.status, declined.last_error) == (ActionStatus.SKIPPED, "NETWORK_UNVERIFIED")
    assert made.status is ActionStatus.SUCCEEDED
    assert made.request_body["slot_id"] == declined.request_body["slot_id"]
    assert made.inputs["consent_message_id"] == declined.inputs["consent_message_id"]
    assert len(await appointments(simulator, customer)) == 1


async def test_choice_does_not_outlive_a_later_message_from_the_customer(
    platform: Platform, simulator: Simulator
) -> None:
    """Same blip, but the customer has written again in the meantime. What they chose before
    is no longer taken as their word: they are asked again, and nothing is booked."""
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    await platform.say(intake.contact_id, "1")
    await platform.worker.run_once()
    await simulator.fault("network", "down")
    await platform.executor.run_once()
    await simulator.fault("network", "ok")
    await platform.say(intake.contact_id, "hold on, let me check with my family first")

    await platform.settle()

    assert await appointments(simulator, customer) == []
    assert len(await visit_actions(platform, intake)) == 1  # the declined one, and no other
    *_, latest = await platform.replies(intake.contact_id)
    assert "Your technician visit is booked" not in latest


# --- one visit per service -----------------------------------------------------------------------


async def test_service_with_a_visit_booked_is_told_of_it_and_not_offered_another(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    first, _ = await offered_a_visit(platform, simulator, customer)
    await answer(platform, first, "1")
    (booked,) = await appointments(simulator, customer)

    second, _ = await second_contact_after_the_hold(platform, simulator, customer)

    *_, told = await platform.replies(second.contact_id)
    assert "A technician visit is already booked for this service" in told
    assert await appointments(simulator, customer) == [booked]
    assert await visit_actions(platform, second) == []


async def second_contact_after_the_hold(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> tuple[Any, None]:
    settings = platform.runtime.settings
    intake = await platform.contact(
        "still no internet at home", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()
    return intake, None


async def test_two_conversations_choosing_at_once_end_in_one_visit_and_no_false_claim(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    first, _ = await offered_a_visit(platform, simulator, customer)
    second, _ = await second_contact_after_the_hold(platform, simulator, customer)
    await platform.say(first.contact_id, "1")
    await platform.say(second.contact_id, "1")
    await platform.worker.run_once()  # both read; one proposal is refused by the constraint

    told = [(await platform.replies(c.contact_id))[-1] for c in (first, second)]
    assert sum("I'm booking a technician visit" in reply for reply in told) == 1
    assert sum("being arranged at this moment through another conversation" in r for r in told) == 1
    assert not any("already booked" in reply for reply in told)  # nothing is booked yet

    await platform.settle()

    assert len(await appointments(simulator, customer)) == 1
    other = second if "I'm booking" in told[0] else first
    later = await answer(platform, other, "is my internet fixed yet? it is still down")
    assert "A technician visit is already booked for this service" in later
    assert len(await appointments(simulator, customer)) == 1


async def test_visit_whose_time_has_passed_does_not_stand_in_the_way_of_the_next(
    platform: Platform, simulator: Simulator
) -> None:
    """Field Service keeps a visit BOOKED after its slot is over. The technician came; the
    line has failed again; that old visit is not a reason to refuse a new one."""
    customer = await customer_on_a_healthy_node(simulator)
    intake, _ = await offered_a_visit(platform, simulator, customer)
    (earliest, *_) = await offered_slot_ids(platform, intake)
    await simulator.book_slot(earliest, customer)
    still_to_come = await answer(platform, intake, "my internet is still down")
    assert "A technician visit is already booked for this service" in still_to_come

    platform.advance(3 * 24 * 3600)
    again = await platform.contact(
        "my internet is down again", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    settings = platform.runtime.settings
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()

    *_, offer = await platform.replies(again.contact_id)
    assert "I can book a visit in one of these slots" in offer
    assert "already booked" not in offer


async def test_slot_that_fills_up_before_the_booking_is_offered_again_from_what_is_free(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    intake, offer = await offered_a_visit(platform, simulator, customer)
    first_slot = slots_in(offer)[0].split(". ", 1)[1]
    (first_id, *_) = await offered_slot_ids(platform, intake)
    await simulator.fill_slot(first_id, not_for=customer["service_id"])

    reply = await answer(platform, intake, "1")

    assert "The slot you chose has just been taken" in reply
    assert first_slot not in reply
    assert await appointments(simulator, customer) == []
    assert await handoff_reasons(platform) == []

    confirmation = await answer(platform, intake, "1")

    assert "Your technician visit is booked for" in confirmation
    assert len(await appointments(simulator, customer)) == 1


async def test_field_service_that_cannot_be_read_sends_the_repair_to_a_person(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_on_a_healthy_node(simulator)
    await simulator.fault("field", "down")
    settings = platform.runtime.settings
    await simulator.line_fault(customer["service_id"], fault=True)
    intake = await platform.contact(
        "my internet is down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()

    *_, latest = await platform.replies(intake.contact_id)
    assert "A member of our team will arrange it" in latest
    assert await handoff_reasons(platform) == ["LINE_FAULT"]
