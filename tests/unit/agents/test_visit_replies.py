"""What the customer is told about a technician visit."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from skylink_care.agents import replies, visit_replies
from skylink_care.agents.dispatch import OfferedSlot

REF = "SKL-261115-000001-9"
# Monday 16 November 2026, 09:00 to 13:00 and 14:00 to 18:00 in India.
MORNING = OfferedSlot(
    "SL-560038-20261116-AM",
    datetime(2026, 11, 16, 3, 30, tzinfo=UTC),
    datetime(2026, 11, 16, 7, 30, tzinfo=UTC),
)
AFTERNOON = OfferedSlot(
    "SL-560038-20261116-PM",
    datetime(2026, 11, 16, 8, 30, tzinfo=UTC),
    datetime(2026, 11, 16, 12, 30, tzinfo=UTC),
)
TESTED = datetime(2026, 11, 15, 6, 29, tzinfo=UTC)
CHECKED = datetime(2026, 11, 15, 6, 30, tzinfo=UTC)


def test_slot_is_named_by_its_day_and_hours_in_india() -> None:
    assert visit_replies.slot_label(MORNING) == "Mon 16 Nov, 09:00 to 13:00"
    assert visit_replies.slot_label(AFTERNOON) == "Mon 16 Nov, 14:00 to 18:00"


def test_offer_states_its_evidence_and_lists_the_slots_by_number() -> None:
    reply = visit_replies.visit_offer(
        REF, (MORNING, AFTERNOON), tested_at=TESTED, evidence_at=CHECKED
    )

    assert "fault on your line is still there (tested again at 11:59 IST on 15 Nov)" in reply.text
    assert "no network outage has been reported for your area as of 12:00 IST on 15 Nov" in (
        reply.text
    )
    assert "\n1. Mon 16 Nov, 09:00 to 13:00\n2. Mon 16 Nov, 14:00 to 18:00\n" in reply.text
    assert '"no" if you don\'t want a visit' in reply.text
    assert reply.facts["slots"] == [MORNING.slot_id, AFTERNOON.slot_id]
    assert reply.template_id == "visit_offer.v1"


@pytest.mark.parametrize(
    ("build", "phrase"),
    [
        (
            lambda: visit_replies.visit_choice_unclear(REF, (MORNING,)),
            "I won't book a visit on a guess",
        ),
        (lambda: visit_replies.visit_slot_taken(REF, (AFTERNOON,)), "has just been taken"),
        (
            lambda: visit_replies.visit_being_booked(REF, MORNING),
            "booking a technician visit for Mon 16 Nov, 09:00 to 13:00",
        ),
        (
            lambda: visit_replies.visit_booked(REF, MORNING, appointment_id="AP-9"),
            "is booked for Mon 16 Nov, 09:00 to 13:00 (appointment AP-9)",
        ),
        (
            lambda: visit_replies.visit_already_booked(REF, AFTERNOON),
            "already booked for this service for Mon 16 Nov, 14:00 to 18:00",
        ),
        (
            lambda: visit_replies.visit_already_booked(REF, None),
            "already booked for this service, so I haven't booked another one",
        ),
        (lambda: visit_replies.visit_declined(REF), "I haven't booked a visit"),
        (lambda: visit_replies.visit_not_booked(REF), "A member of our team will arrange it"),
    ],
)
def test_every_message_about_a_visit_says_who_is_speaking_and_carries_the_reference(
    build: object, phrase: str
) -> None:
    reply = build()  # type: ignore[operator]

    assert phrase in reply.text
    assert REF in reply.text
    assert reply.text.startswith(replies.AI_DISCLOSURE)


def test_booking_without_an_appointment_number_is_still_confirmed() -> None:
    reply = visit_replies.visit_booked(REF, MORNING, appointment_id=None)

    assert "is booked for Mon 16 Nov, 09:00 to 13:00." in reply.text
    assert "appointment" not in reply.text


def test_no_message_promises_what_is_not_built() -> None:
    """Nothing here cancels or moves a visit yet: a person does that."""
    booked = visit_replies.visit_booked(REF, MORNING, appointment_id="AP-9").text

    assert "a member of our team will help" in booked
    assert "I'll cancel" not in booked
