"""Folding identification claims across messages, and making model-written summaries safe."""

from __future__ import annotations

from typing import Any

import pytest

from skylink_care.agents.triage import Claim, merge_claim
from skylink_care.orchestrator.handover import MAX_SUMMARY_CHARS, clean_summary, handoff_summary


def test_customer_id_and_phone_in_one_message_fill_the_whole_claim() -> None:
    claim = merge_claim(Claim(), "id C0001234, phone +91 98450 12345", awaiting_digits=False)

    assert claim == Claim(customer_id="C0001234", phone_last4="2345")


def test_bare_digits_are_taken_only_when_they_were_asked_for() -> None:
    started = Claim(customer_id="C0001234")

    unprompted = merge_claim(started, "it broke at 1030 today", awaiting_digits=False)
    prompted = merge_claim(started, "the digits are 2345", awaiting_digits=True)

    assert unprompted == started
    assert prompted == Claim(customer_id="C0001234", phone_last4="2345")


def test_digits_inside_the_customer_id_are_not_mistaken_for_the_factor() -> None:
    claim = merge_claim(Claim(), "my id is C0001234", awaiting_digits=True)

    assert claim == Claim(customer_id="C0001234", phone_last4=None)


def test_a_new_customer_id_discards_digits_given_for_the_old_one() -> None:
    earlier = Claim(customer_id="C0001234", phone_last4="2345")

    claim = merge_claim(earlier, "sorry, the id is C0005678", awaiting_digits=True)

    assert claim == Claim(customer_id="C0005678", phone_last4=None)


def test_repeating_the_same_customer_id_keeps_the_digits() -> None:
    earlier = Claim(customer_id="C0001234", phone_last4="2345")

    assert merge_claim(earlier, "yes C0001234", awaiting_digits=False) == earlier


def test_a_message_with_nothing_to_offer_changes_nothing() -> None:
    earlier = Claim(customer_id="C0001234")

    assert merge_claim(earlier, "is anyone there?", awaiting_digits=False) == earlier


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Customer reports no internet.", "Customer reports no internet."),
        ("Call 9845012345 or a@example.com", "Call (PHONE) or (EMAIL)"),
        ("<script>alert(1)</script> down", "(script)alert(1)(/script) down"),
        ("line one\nline two\ttabbed\x00null", "line one line two tabbed null"),
    ],
)
def test_summary_is_redacted_and_stripped_of_markup_and_control_characters(
    raw: str, expected: str
) -> None:
    assert clean_summary(raw) == expected


def test_summary_length_is_capped() -> None:
    assert len(clean_summary("word " * 500)) <= MAX_SUMMARY_CHARS


def test_the_person_who_takes_over_is_told_when_the_reading_and_the_words_disagree() -> None:
    """A message read as no request of ours whose plain words report a fault goes to a
    person. The summary must send that person to the words, not to the reading."""
    state: Any = {
        "intent": {
            "intent": "other",
            "confidence": "high",
            "summary": "Customer message is off-topic.",
            "rules": {"plain_fault": True},
        },
        "identity": {"status": "IDENTIFIED", "method": "channel_auth", "verified": True},
    }

    summary = handoff_summary(state, "LOW_CONFIDENCE")
    agreed = handoff_summary(
        {**state, "intent": {**state["intent"], "rules": {"plain_fault": False}}},
        "LOW_CONFIDENCE",
    )

    assert "its plain words report a fault: read what the customer wrote" in summary
    assert "plain words" not in agreed


def test_the_person_who_takes_over_is_told_of_a_locked_identity_check() -> None:
    state: Any = {
        "intent": {"intent": "connectivity", "confidence": "high", "summary": "No internet."},
        "identity": {"status": "VERIFICATION_FAILED", "locked": True},
        "claim": {"customer_id": "C0000042", "phone_last4": None},
    }

    summary = handoff_summary(state, "IDENTITY_UNRESOLVED")
    not_locked = handoff_summary(
        {**state, "identity": {"status": "VERIFICATION_FAILED"}}, "IDENTITY_UNRESOLVED"
    )

    assert "The identity check for customer ID C0000042 is locked" in summary
    assert "locked" not in not_locked
