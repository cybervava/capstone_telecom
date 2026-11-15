"""Dispatch Agent: which visit slots can be offered, and what the customer answered.

It reads Field Service (free slots, a visit already booked for the service) and changes
nothing. A booking is a proposal in the action ledger, made once the customer has chosen a slot
and the evidence for a visit holds (``domain.dispatch``), and carried out by the executor after
it has checked that evidence again itself.

The customer's answer to an offer is read by rules, not by a model, and narrowly: a slot counts
as chosen only when the reply names exactly one of the slots that were offered, or plainly says
yes. Anything else is asked again. Consent to a visit is never inferred.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from skylink_care.adapters.clients import FieldReader
from skylink_care.adapters.models import AppointmentDto
from skylink_care.adapters.result import Ok
from skylink_care.clock import IST, Clock
from skylink_care.domain.dispatch import VisitLimits
from skylink_care.observability.audit import Auditor

ACTOR = "agent:dispatch"
BOOKED = "BOOKED"
NOON = 12


@dataclass(frozen=True)
class OfferedSlot:
    slot_id: str
    start: datetime
    end: datetime

    @property
    def day(self) -> date:
        return self.start.astimezone(IST).date()

    @property
    def morning(self) -> bool:
        return self.start.astimezone(IST).hour < NOON


@dataclass(frozen=True)
class Booking:
    appointment_id: str
    slot: OfferedSlot


@dataclass(frozen=True)
class FieldView:
    """What Field Service says about one service right now."""

    available: bool
    booked: Booking | None = None
    slots: tuple[OfferedSlot, ...] = ()


def is_open_visit(appointment: AppointmentDto, now: datetime) -> bool:
    """Booked and still to come. Field Service keeps a visit BOOKED after its time has passed;
    such a visit is over, and must not stand in the way of the next one."""
    return appointment.status == BOOKED and appointment.end > now


class DispatchAgent:
    def __init__(
        self, field: FieldReader, limits: VisitLimits, *, slots_offered: int, now: Clock
    ) -> None:
        self._field = field
        self._slots_offered = slots_offered
        self._now = now
        self.limits = limits

    async def view(self, service_id: str, pincode: str, *, audit: Auditor) -> FieldView:
        """A visit already booked for the service, or else the earliest slots that are free."""
        booked = await audit.tool(
            actor=ACTOR,
            tool="field.booked_for_service",
            inputs={"service_id": service_id},
            call=self._field.booked_for_service(service_id, correlation_id=audit.correlation_id),
        )
        if not isinstance(booked, Ok):
            return FieldView(available=False)
        now = self._now()
        for item in booked.value.appointments:
            if is_open_visit(item, now):
                slot = OfferedSlot(item.slot_id, item.start, item.end)
                return FieldView(True, booked=Booking(item.appointment_id, slot))
        listed = await audit.tool(
            actor=ACTOR,
            tool="field.slots",
            inputs={"pincode": pincode},
            call=self._field.slots(
                pincode=pincode,
                from_date=now.astimezone(IST).date().isoformat(),
                correlation_id=audit.correlation_id,
            ),
        )
        if not isinstance(listed, Ok):
            return FieldView(available=False)
        free = sorted(
            (slot for slot in listed.value.slots if slot.remaining > 0 and slot.start > now),
            key=lambda slot: slot.start,
        )
        offered = tuple(OfferedSlot(s.slot_id, s.start, s.end) for s in free)
        return FieldView(True, slots=offered[: self._slots_offered])


# --- reading the customer's answer -------------------------------------------------------------
#
# The reply is read as a whole or not at all. It is split into plain words; every word must be
# either part of naming a slot ("2", "second", "tomorrow", "morning"), a plain yes, or one of a
# short list of words that carry nothing ("please", "the", "one"). One word outside those, a
# negation anywhere, or a question mark, and nothing is chosen: the customer is asked again.
# Asking twice costs a message. Reading consent into "I can't do 2" costs a wasted visit.


class AnswerKind(StrEnum):
    CHOSEN = "CHOSEN"
    DECLINED = "DECLINED"
    UNCLEAR = "UNCLEAR"


@dataclass(frozen=True)
class Answer:
    kind: AnswerKind
    slot: OfferedSlot | None = None


_UNCLEAR = Answer(AnswerKind.UNCLEAR)
_DECLINED = Answer(AnswerKind.DECLINED)

_REFUSAL = re.compile(
    r"\bno (?:visit|technician|engineer|need|thanks?|thank you)\b"
    r"|\b(?:don'?t|do not|dont) (?:want|need|send|book|come)\b"
    r"|\bnot now\b|\bcancel\b|\bnahi chahiye\b|\bmat bhejo\b",
    re.ASCII,
)
_BARE_NO = re.compile(r"^[^a-z0-9]*(?:no|nope|nah|nahi|nahin)[^a-z0-9]*$", re.ASCII)
_WORD = re.compile(r"[a-z0-9']+", re.ASCII)
_NUMBERED = re.compile(r"^([1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?$", re.ASCII)
_NEGATIONS = frozenset(
    [
        "not",
        "no",
        "never",
        "cannot",
        "cant",
        "can't",
        "wont",
        "won't",
        "dont",
        "don't",
        "except",
        "but",
        "instead",
        "or",
        "either",
        "neither",
        "nor",
        "unless",
        "if",
        "maybe",
        "perhaps",
    ]
)
_YES = frozenset(
    [
        "yes",
        "yeah",
        "yep",
        "yup",
        "ok",
        "okay",
        "sure",
        "haan",
        "book",
        "confirm",
        "confirmed",
        "ahead",
    ]
)
# Words that carry nothing on their own.
_FILLER = frozenset(
    [
        "please",
        "pls",
        "plz",
        "thanks",
        "thank",
        "you",
        "the",
        "a",
        "one",
        "slot",
        "option",
        "number",
        "for",
        "me",
        "i",
        "i'll",
        "i'd",
        "will",
        "would",
        "like",
        "want",
        "take",
        "prefer",
        "choose",
        "pick",
        "it",
        "that",
        "this",
        "on",
        "in",
        "go",
        "with",
        "let's",
        "lets",
        "sir",
        "madam",
        "ji",
        "theek",
        "hai",
        "then",
        "do",
    ]
)
# Words that carry nothing next to a slot that is named, and too much without one ("it works").
_FILLER_WITH_A_SLOT = frozenset(
    ["works", "work", "fine", "good", "great", "suits", "better", "best", "is", "be"]
)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "jan january",
            "feb february",
            "mar march",
            "apr april",
            "may",
            "jun june",
            "jul july",
            "aug august",
            "sep sept september",
            "oct october",
            "nov november",
            "dec december",
        ),
        start=1,
    )
    for name in names.split()
}
_WEEKDAYS = {
    name: number
    for number, names in enumerate(
        (
            "mon monday",
            "tue tues tuesday",
            "wed weds wednesday",
            "thu thur thurs thursday",
            "fri friday",
            "sat saturday",
            "sun sunday",
        )
    )
    for name in names.split()
}
_MORNING = frozenset({"morning", "forenoon"})
_AFTERNOON = frozenset({"afternoon", "evening"})


@dataclass
class _Reading:
    """What the words of a reply amount to."""

    numbers: set[int]
    days: set[date]
    weekdays: set[int]
    morning: bool = False
    afternoon: bool = False
    said_yes: bool = False
    # Words that are harmless only next to a named slot, and words that are not understood.
    conditional: int = 0
    unread: int = 0

    @property
    def names_a_slot(self) -> bool:
        return bool(self.numbers or self.days or self.weekdays or self.morning or self.afternoon)


def read_answer(text: str, offered: Sequence[OfferedSlot], *, now: datetime) -> Answer:
    """What a reply to a visit offer means, given the slots that were offered."""
    lowered = text.lower()
    if not offered or not lowered.strip():
        return _UNCLEAR
    if _REFUSAL.search(lowered) or _BARE_NO.match(lowered):
        return _DECLINED
    words = _WORD.findall(lowered)
    foreign = any(char.isalnum() and not char.isascii() for char in lowered)
    if foreign or "?" in lowered or not words or any(word in _NEGATIONS for word in words):
        return _UNCLEAR
    reading = _read(words, now.astimezone(IST).date())
    if reading.unread or (reading.conditional and not reading.names_a_slot):
        return _UNCLEAR
    if not reading.names_a_slot:
        return Answer(AnswerKind.CHOSEN, offered[0]) if reading.said_yes else _UNCLEAR
    fitting = _fitting(reading, offered)
    return Answer(AnswerKind.CHOSEN, fitting[0]) if len(fitting) == 1 else _UNCLEAR


def _read(words: list[str], today: date) -> _Reading:
    reading = _Reading(numbers=set(), days=set(), weekdays=set())
    position = 0
    while position < len(words):
        position += _take(words, position, today, reading)
    return reading


def _take(words: list[str], position: int, today: date, reading: _Reading) -> int:
    """Read the word at ``position`` (and those that belong to it). Returns how many it was."""
    word = words[position]
    following = words[position + 1 : position + 3]
    if [word, *following] == ["day", "after", "tomorrow"]:
        reading.days.add(today + timedelta(days=2))
        return 3
    named = _named_date(word, following, today)
    if named is not None:
        reading.days.add(named[0])
        return named[1]
    if word in {"today", "tomorrow"}:
        reading.days.add(today + timedelta(days=1 if word == "tomorrow" else 0))
    elif word in _WEEKDAYS:
        reading.weekdays.add(_WEEKDAYS[word])
    elif word in _MORNING or word in _AFTERNOON:
        reading.morning = reading.morning or word in _MORNING
        reading.afternoon = reading.afternoon or word in _AFTERNOON
    elif word in _ORDINALS:
        reading.numbers.add(_ORDINALS[word])
    elif _NUMBERED.match(word) and len(word.rstrip("stndrh")) == 1:
        reading.numbers.add(int(word[0]))  # "2", "2nd": an option; "16" is not one
    elif word in _YES:
        reading.said_yes = True
    elif word in _FILLER_WITH_A_SLOT:
        reading.conditional += 1
    elif word not in _FILLER:
        reading.unread += 1
    return 1


def _named_date(word: str, following: list[str], today: date) -> tuple[date, int] | None:
    """A date written out at this position ("17 nov", "17th of november", "nov 17"), and how
    many words it took. The year is the one that puts the date nearest ahead of today."""
    if _NUMBERED.match(word) and following:
        skip = 1 if following[0] == "of" and len(following) > 1 else 0
        month = _MONTHS.get(following[skip])
        day, length = int(word.rstrip("stndrh")), 2 + skip
    elif word in _MONTHS and following and _NUMBERED.match(following[0]):
        month, day, length = _MONTHS[word], int(following[0].rstrip("stndrh")), 2
    else:
        return None
    if month is None:
        return None
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return candidate, length
    return None


def _fitting(reading: _Reading, offered: Sequence[OfferedSlot]) -> list[OfferedSlot]:
    """The offered slots that fit everything the reply names: number, day, half of the day."""
    if len(reading.numbers) > 1:
        return []
    named_a_day = bool(reading.days or reading.weekdays)
    return [
        slot
        for number, slot in enumerate(offered, start=1)
        if (not reading.numbers or number in reading.numbers)
        and (not named_a_day or slot.day in reading.days or slot.day.weekday() in reading.weekdays)
        and (reading.morning == reading.afternoon or slot.morning == reading.morning)
    ]
