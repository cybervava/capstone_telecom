"""PII masking before any text leaves for a model, and identifier extraction for triage."""

from __future__ import annotations

import time

import pytest

from skylink_care.guardrails.pii import extract_identifiers, mask_pii


def test_phone_numbers_are_masked_in_every_common_spelling() -> None:
    text = "Call me on +91 98450 12345 or 9845012345, office 098450-12345."

    masked = mask_pii(text)

    assert "98450" not in masked.text
    assert masked.text.count("<PHONE_") == 3
    assert masked.text.startswith("Call me on <PHONE_1> or ")


def test_the_same_value_gets_the_same_token() -> None:
    masked = mask_pii("My number is 9845012345. Yes, 9845012345.")

    assert masked.text == "My number is <PHONE_1>. Yes, <PHONE_1>."


def test_email_customer_id_and_service_id_are_masked() -> None:
    masked = mask_pii("I am C0001234 (asha.menon17@example.com), service S0005678 is down")

    assert masked.text == "I am <CUSTOMER_ID_1> (<EMAIL_1>), service <SERVICE_ID_1> is down"


def test_long_digit_runs_are_masked_even_when_not_recognised() -> None:
    masked = mask_pii("my card 4111111111111111 and aadhaar 1234 5678 9012")

    assert "4111111111111111" not in masked.text
    assert "1234 5678 9012" not in masked.text
    assert masked.text.count("<NUMBER_") == 2


def test_known_values_such_as_the_customer_name_are_masked_case_insensitively() -> None:
    masked = mask_pii(
        "This is asha menon from Indiranagar, my wifi is down",
        known_values={"NAME": "Asha Menon", "AREA": "Indiranagar"},
    )

    assert masked.text == "This is <NAME> from <AREA>, my wifi is down"


def test_complaint_reference_router_model_and_times_are_left_alone() -> None:
    text = "Ref SKL-261115-000123-7, router AL-AX18, down since 10:30 for 3 hours, plan 999"

    assert mask_pii(text).text == text


def test_masking_can_be_reversed_for_internal_use() -> None:
    original = "Reach me at asha@example.com or 9845012345"
    masked = mask_pii(original)

    assert masked.restore(masked.text) == original
    assert masked.restore("We will write to <EMAIL_1>.") == "We will write to asha@example.com."


def test_text_without_pii_is_unchanged_and_reports_nothing() -> None:
    masked = mask_pii("my internet has been down since morning")

    assert masked.text == "my internet has been down since morning"
    assert masked.found == ()


def test_found_lists_the_kinds_of_pii_but_never_the_values() -> None:
    masked = mask_pii("9845012345 / asha@example.com")

    assert sorted(masked.found) == ["EMAIL", "PHONE"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my number is 98450 12345", "+919845012345"),
        ("+91-9845012345", "+919845012345"),
        ("09845012345 please", "+919845012345"),
    ],
)
def test_extracted_phones_are_normalised_to_e164(text: str, expected: str) -> None:
    assert extract_identifiers(text).phones == (expected,)


def test_identifiers_are_extracted_once_each_in_order() -> None:
    found = extract_identifiers(
        "customer id C0001234, again c0001234, mail Asha@Example.com, phone 9845012345"
    )

    assert found.customer_ids == ("C0001234",)
    assert found.emails == ("asha@example.com",)
    assert found.phones == ("+919845012345",)


def test_a_complaint_reference_is_not_mistaken_for_a_phone_number() -> None:
    found = extract_identifiers("my ref is SKL-261115-9845012345-7")

    assert found.phones == ()


def test_no_identifiers_gives_empty_result() -> None:
    found = extract_identifiers("wifi not working")

    assert (found.phones, found.customer_ids, found.emails) == ((), (), ())
    assert found.is_empty


def test_log_redaction_masks_contact_details_but_keeps_internal_ids() -> None:
    from skylink_care.guardrails.pii import redact_for_log

    redacted = redact_for_log(
        {
            "customer_id": "C0001234",
            "note": "call 9845012345 or mail asha@example.com, card 4111111111111111",
            "nested": [{"phone": "+91 98450 12345"}, 42, None],
            "ref": "SKL-261115-000123-7",
        }
    )

    assert redacted == {
        "customer_id": "C0001234",
        "note": "call <PHONE> or mail <EMAIL>, card <NUMBER>",
        "nested": [{"phone": "<PHONE>"}, 42, None],
        "ref": "SKL-261115-000123-7",
    }


@pytest.mark.parametrize(
    "disguised",
    [
        "98765.43210",
        "98765\u200b43210",  # zero-width space
        "+91.98765.43210",
        "\uff19\uff18\uff17\uff16\uff15\uff14\uff13\uff12\uff11\uff10",  # full-width digits
    ],
    ids=["dotted", "zero-width", "dotted-prefix", "full-width"],
)
def test_common_disguises_of_a_phone_number_are_still_masked(disguised: str) -> None:
    masked = mask_pii(f"call me on {disguised} please")

    assert masked.found == ("PHONE",)
    assert "43210" not in masked.text


def test_an_email_split_by_an_invisible_character_is_masked_whole() -> None:
    masked = mask_pii("write to j\u200bohn@example.com")

    assert masked.text == "write to <EMAIL_1>"


def test_long_text_without_an_address_is_masked_in_linear_time() -> None:
    """A long run of letters used to be re-scanned from every position by the e-mail pattern."""
    text = "a" * 20_000 + " reach me at asha.rao@example.com or 9845012345"

    started = time.perf_counter()
    masked = mask_pii(text)
    elapsed = time.perf_counter() - started

    assert masked.text.endswith("reach me at <EMAIL_1> or <PHONE_1>")
    assert elapsed < 0.25


def test_address_inside_other_text_is_still_found_by_the_bounded_pattern() -> None:
    masked = mask_pii("mail:asha@example.com, <asha.rao+care@mail.example.co.in>")

    assert "example" not in masked.text
    assert masked.text.count("<EMAIL_") == 2
