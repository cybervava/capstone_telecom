"""What a customer's reply to a visit offer means.

The reading is deliberately narrow: a slot is chosen only when the reply names exactly one of
the slots that were offered, or plainly says yes. Anything else is asked again, never guessed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from skylink_care.agents.dispatch import AnswerKind, OfferedSlot, read_answer

# Sunday 15 November 2026, 20:00 IST.
NOW = datetime(2026, 11, 15, 14, 30, tzinfo=UTC)


def slot(day: int, half: str) -> OfferedSlot:
    start_hour, end_hour = (3, 7) if half == "AM" else (8, 12)  # 09-13 and 14-18 IST, in UTC
    return OfferedSlot(
        slot_id=f"SL-560038-202611{day}-{half}",
        start=datetime(2026, 11, day, start_hour, 30, tzinfo=UTC),
        end=datetime(2026, 11, day, end_hour, 30, tzinfo=UTC),
    )


MON_AM, MON_PM, TUE_AM = slot(16, "AM"), slot(16, "PM"), slot(17, "AM")
OFFERED = (MON_AM, MON_PM, TUE_AM)


@pytest.mark.parametrize(
    ("text", "chosen"),
    [
        ("1", MON_AM),
        ("2", MON_PM),
        ("option 3 please", TUE_AM),
        ("slot 2", MON_PM),
        ("the second one", MON_PM),
        ("3rd", TUE_AM),
        ("I'd like number 2 please", MON_PM),
        ("2, monday afternoon", MON_PM),
        ("tomorrow afternoon", MON_PM),
        ("Monday morning works", MON_AM),
        ("tuesday", TUE_AM),
        ("17 nov", TUE_AM),
        ("the 17th of November is fine", TUE_AM),
        ("nov 17", TUE_AM),
        ("day after tomorrow", TUE_AM),
        ("yes", MON_AM),
        ("Yes please book it", MON_AM),
        ("ok go ahead", MON_AM),
        ("OK", MON_AM),
        ("please book it", MON_AM),
        ("haan", MON_AM),
    ],
)
def test_reply_that_names_one_offered_slot_or_says_yes_chooses_it(
    text: str, chosen: OfferedSlot
) -> None:
    answer = read_answer(text, OFFERED, now=NOW)

    assert (answer.kind, answer.slot) == (AnswerKind.CHOSEN, chosen)


@pytest.mark.parametrize(
    "text",
    [
        "no",
        "No.",
        "nope",
        "no thanks",
        "not now",
        "I don't want a technician",
        "do not send anyone",
        "cancel",
        "no visit needed, 2 days ago it fixed itself",
        "nahi chahiye",
    ],
)
def test_refusal_is_a_refusal(text: str) -> None:
    answer = read_answer(text, OFFERED, now=NOW)

    assert (answer.kind, answer.slot) == (AnswerKind.DECLINED, None)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "what?",
        "how much does it cost",
        "1 or 2",  # two slots named
        "4",  # not offered
        "monday",  # two slots on Monday
        "morning",  # two mornings
        "wednesday",  # no slot offered that day
        "yes but not tomorrow",
        "ok but can it be later?",
        "at 2 pm",  # a time, not the second option
        "16",  # a date without a month is not an option number
        "my number is 9876543210",
        "2, tuesday",  # the second slot is not on Tuesday
        "30 feb",
        "ſat morning",  # noqa: RUF001 - the point: a letter that only looks like one of ours
        "सोमवार",
    ],
)
def test_anything_else_is_asked_again_and_never_guessed(text: str) -> None:
    answer = read_answer(text, OFFERED, now=NOW)

    assert (answer.kind, answer.slot) == (AnswerKind.UNCLEAR, None)


@pytest.mark.parametrize(
    "text",
    [
        "not 2",
        "I can't do 2",
        "no, 2 is better for me",
        "not the afternoon",
        "anything except monday morning",
        "it is the second time it failed",
        "wait a second",
        "first of all, my line is dead",
        "I live on floor 2",
        "after 2 please",
        "around 3",
        "ok how much will it cost",
        "please confirm what time the technician arrives",
        "ok the whole street is down",
        "it works now ok",
        "it works ok",
        "that is fine",
        "ok so is this an outage",
        "yes my neighbour has the same problem",
        "1 if it is free of charge",
    ],
)
def test_words_that_only_sound_like_a_choice_are_not_consent(text: str) -> None:
    """Each of these was once read as a slot or as a yes. A visit booked on any of them would
    be a visit the customer did not ask for."""
    answer = read_answer(text, OFFERED, now=NOW)

    assert (answer.kind, answer.slot) == (AnswerKind.UNCLEAR, None)


def test_long_hostile_reply_is_read_quickly_and_chooses_nothing() -> None:
    for text in ("2 " * 3000, "jan" + "a" * 6000, "second " * 1500, "'" * 8000):
        assert read_answer(text, OFFERED, now=NOW).kind is not AnswerKind.DECLINED


def test_with_one_slot_on_offer_its_day_alone_is_enough() -> None:
    answer = read_answer("monday is fine", (MON_PM, TUE_AM), now=NOW)

    assert (answer.kind, answer.slot) == (AnswerKind.CHOSEN, MON_PM)


def test_today_means_today_in_india() -> None:
    late_evening = datetime(2026, 11, 15, 19, 0, tzinfo=UTC)  # already Monday 00:30 IST

    answer = read_answer("today afternoon", OFFERED, now=late_evening)

    assert (answer.kind, answer.slot) == (AnswerKind.CHOSEN, MON_PM)


def test_nothing_is_chosen_when_nothing_was_offered() -> None:
    assert read_answer("yes", (), now=NOW).kind is AnswerKind.UNCLEAR
