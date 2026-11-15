"""What the customer is told about a technician visit.

A visit is offered only on evidence, and each message says what that evidence is: the line was
tested, when, and that no outage has been reported for the area. Slots are listed by number so
that the answer can be read without guessing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from skylink_care.agents.dispatch import OfferedSlot
from skylink_care.agents.replies import AI_DISCLOSURE, Reply, clock
from skylink_care.clock import IST

_HOW_TO_ANSWER = (
    'Reply with the number of the slot you want (or "yes" for the first one), or "no" if you'
    " don't want a visit."
)


def slot_label(slot: OfferedSlot) -> str:
    """``Mon 16 Nov, 09:00 to 13:00``"""
    start, end = slot.start.astimezone(IST), slot.end.astimezone(IST)
    return f"{start:%a} {start.day} {start:%b}, {start:%H:%M} to {end:%H:%M}"


def _numbered(slots: Sequence[OfferedSlot]) -> str:
    return "\n".join(f"{number}. {slot_label(slot)}" for number, slot in enumerate(slots, 1))


def _facts(slots: Sequence[OfferedSlot]) -> dict[str, object]:
    return {"slots": [slot.slot_id for slot in slots]}


def visit_offer(
    complaint_ref: str,
    slots: Sequence[OfferedSlot],
    *,
    tested_at: datetime,
    evidence_at: datetime,
) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} The fault on your line is still there (tested again at"
        f" {clock(tested_at)}) and no network outage has been reported for your area as of"
        f" {clock(evidence_at)}, so the line needs a technician at your address. I can book a"
        f" visit in one of these slots:\n{_numbered(slots)}\n{_HOW_TO_ANSWER}"
        f" Your complaint reference is {complaint_ref}.",
        "visit_offer.v1",
        _facts(slots) | {"line_tested_at": tested_at.isoformat()},
    )


def visit_choice_unclear(complaint_ref: str, slots: Sequence[OfferedSlot]) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} I couldn't tell which slot you would like, and I won't book a visit"
        f" on a guess. These are the slots:\n{_numbered(slots)}\n{_HOW_TO_ANSWER}"
        f" Your complaint reference is {complaint_ref}.",
        "visit_choice_unclear.v1",
        _facts(slots),
    )


def visit_slot_taken(complaint_ref: str, slots: Sequence[OfferedSlot]) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} The slot you chose has just been taken, so nothing is booked yet."
        f" These slots are free:\n{_numbered(slots)}\n{_HOW_TO_ANSWER}"
        f" Your complaint reference is {complaint_ref}.",
        "visit_slot_taken.v1",
        _facts(slots),
    )


def visit_being_booked(complaint_ref: str, slot: OfferedSlot) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Thank you. I'm booking a technician visit for {slot_label(slot)} and"
        f" will confirm here as soon as it is booked. Your complaint reference is"
        f" {complaint_ref}.",
        "visit_being_booked.v1",
        {"slot_id": slot.slot_id},
    )


def visit_booked(complaint_ref: str, slot: OfferedSlot, *, appointment_id: str | None) -> Reply:
    reference = f" (appointment {appointment_id})" if appointment_id else ""
    return Reply(
        f"{AI_DISCLOSURE} Your technician visit is booked for {slot_label(slot)}{reference}."
        " Please make sure an adult is at the address during that time. If you need to change"
        " it, reply here and a member of our team will help. Your complaint reference is"
        f" {complaint_ref}.",
        "visit_booked.v1",
        {"slot_id": slot.slot_id, "appointment_id": appointment_id},
    )


def visit_already_booked(complaint_ref: str, slot: OfferedSlot | None) -> Reply:
    when = f" for {slot_label(slot)}" if slot else ""
    return Reply(
        f"{AI_DISCLOSURE} A technician visit is already booked for this service{when}, so I"
        " haven't booked another one. If you need to change it, reply here and a member of our"
        f" team will help. Your complaint reference is {complaint_ref}.",
        "visit_already_booked.v1",
        {"slot_id": slot.slot_id if slot else None},
    )


def visit_declined(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Understood, I haven't booked a visit. Your line still fails our"
        " test, so if you change your mind, reply here and I'll look for a slot again."
        f" Your complaint reference is {complaint_ref}.",
        "visit_declined.v1",
    )


def visit_not_booked(complaint_ref: str) -> Reply:
    """The booking could not be made, or no slot is free. A person arranges it."""
    return Reply(
        f"{AI_DISCLOSURE} I couldn't book a technician visit for you just now. A member of our"
        " team will arrange it and contact you; you don't need to do anything."
        f" Your complaint reference is {complaint_ref}.",
        "visit_not_booked.v1",
    )


def visit_still_being_booked(complaint_ref: str, slot: OfferedSlot) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Your technician visit for {slot_label(slot)} is still being booked."
        f" I'll confirm here as soon as it is done. Your complaint reference is {complaint_ref}.",
        "visit_still_being_booked.v1",
        {"slot_id": slot.slot_id},
    )


def visit_being_arranged_elsewhere(complaint_ref: str) -> Reply:
    """Another conversation of the same customer is booking a visit at this moment. Nothing is
    said to be booked, because nothing is yet."""
    return Reply(
        f"{AI_DISCLOSURE} A technician visit for this service is being arranged at this moment"
        " through another conversation with us, so I haven't started a second booking. Reply"
        " here in a few minutes and I'll tell you where it stands. Your complaint reference is"
        f" {complaint_ref}.",
        "visit_being_arranged_elsewhere.v1",
    )


def visit_needs_a_person(complaint_ref: str) -> Reply:
    """The customer's replies to the offer could not be read as a choice, twice."""
    return Reply(
        f"{AI_DISCLOSURE} I still couldn't tell whether you would like a technician visit, and"
        " your line does need a repair. I'm passing you to a member of our team, who will"
        f" arrange it with you. Your complaint reference is {complaint_ref}.",
        "visit_needs_a_person.v1",
    )
