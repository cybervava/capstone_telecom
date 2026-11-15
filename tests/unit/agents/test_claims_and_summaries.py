"""Folding identification claims across messages, and making model-written summaries safe."""

from __future__ import annotations

import pytest

from skylink_care.agents.triage import Claim, merge_claim
from skylink_care.orchestrator.handover import MAX_SUMMARY_CHARS, clean_summary


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
