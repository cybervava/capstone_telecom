"""PII masking before any text leaves for a model, and identifier extraction for triage."""

from __future__ import annotations

import re
import time

import pytest

from skylink_care.guardrails.pii import (
    extract_identifiers,
    mask_pii,
    normalise,
    redact_for_log,
)


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


# --- values the platform knows belong to the customer ---------------------------------------------


def test_a_known_value_is_masked_as_a_word_not_inside_other_words() -> None:
    """A customer called Das or Sen must not turn "send" into a token, nor Raj "Rajajinagar"."""
    masked = mask_pii(
        "Please send someone, das here from Rajajinagar. Raj's wifi is down",
        known_values={"NAME": "Raj Das", "NAME_1": "Raj", "NAME_2": "Das"},
    )

    assert (
        masked.text
        == "Please send someone, <NAME_2> here from Rajajinagar. <NAME_1>'s wifi is down"
    )


def test_the_longest_known_value_is_masked_first_and_spacing_does_not_hide_it() -> None:
    masked = mask_pii(
        "This is asha   menon, flat near HSR\nLayout",
        known_values={"NAME_1": "Asha", "NAME": "Asha Menon", "AREA": "HSR Layout"},
    )

    assert masked.text == "This is <NAME>, flat near <AREA>"


def test_a_known_number_is_masked_only_as_a_number_of_its_own() -> None:
    """The last four digits of the registered mobile are half of the identity check. They are
    masked wherever they stand alone, and not inside a longer number that is something else."""
    masked = mask_pii(
        "digits 4321, pincode 560034, ticket 143215",
        known_values={"PHONE_LAST_4": "4321", "PINCODE": "560034"},
    )

    assert masked.text == "digits <PHONE_LAST_4>, pincode <PINCODE>, ticket 143215"


def test_a_known_value_too_short_to_be_one_is_ignored() -> None:
    masked = mask_pii("I am A. Rao", known_values={"NAME_1": "A", "NAME_2": "Rao", "BLANK": " "})

    assert masked.text == "I am A. <NAME_2>"
    assert masked.found == ("NAME_2",)


# --- the platform's own records stay readable -----------------------------------------------------


def test_times_and_system_identifiers_survive_redaction_and_contact_details_do_not() -> None:
    """An audit entry is made of times and of the identifiers that tie it to other systems. A
    rule that hides every long run of digits hid the date of the evidence, the outage it was
    about and, for one request id in ten, the id by which the gateway's log is searched."""
    entry = {
        "evidence_at": "2026-11-16T06:32:52+00:00",
        "resolved_at": "2026-11-16T06:06:57.334439Z",
        "map_version": "2026-11-01",
        "outage_id": "OUT-20261116-0007",
        "request_id": "2fa2bde123456789e5a4b071a7f6dc00",
        "contact_id": "cf5826ab-6eb8-4555-8c30-b6cd76a2faff",
        "incident": "INC-1234567890AB",
        "appointment": "AP-91F244150003",
        "line_test": "LT-0a23456789",
        "action_key": "incident-link:cf5826ab-6eb8-4555-8c30-b6cd76a2faff:SKL-261116-003105-2",
        "note": "called 98450 12345 on 2026-11-16, card 4111 1111 1111 1111, asha@example.com",
    }

    redacted = redact_for_log(entry)

    kept = {name: value for name, value in entry.items() if name != "note"}
    assert redacted == {
        **kept,
        "note": "called <PHONE> on 2026-11-16, card <NUMBER>, <EMAIL>",
    }


@pytest.mark.parametrize(
    "text",
    [
        "9845-01-2345",  # a phone number written like a date
        "2026-11-169845012345",  # a number glued to a date
        "2099-13-45 98450",  # not a date at all
        "0000000000000000000000000000000a9845012345",  # digits behind a letter, too long for an id
        "41111111111111114111111111111111",  # thirty-two digits are not a request id
        "AP-9845012345",  # nor is a phone number an appointment
        "98450123-4598-4501-2345-984501234598",  # nor digits in the shape of a UUID
    ],
)
def test_a_number_dressed_as_a_date_or_an_id_is_still_redacted(text: str) -> None:
    assert not re.search(r"\d{8}", re.sub(r"[\s.-]", "", str(redact_for_log(text))))


def test_for_a_model_a_system_prefix_does_not_excuse_a_phone_number() -> None:
    """What the customer writes is theirs to shape. A number is masked for the model whatever
    it is written behind; only the platform's own records are read as records."""
    masked = mask_pii("my appointment AP-9845012345 and ref OUT-20261116-0007 on 2026-11-16")

    assert "9845012345" not in masked.text
    assert "20261116" not in masked.text


# --- the spellings people use, for what is known of the customer ----------------------------------

KNOWN = {
    "NAME": "Asha Menon",
    "NAME_1": "Asha",
    "NAME_2": "Menon",
    "PHONE": "9876543210",
    "PHONE_LAST_6": "543210",
    "PHONE_LAST_5": "43210",
    "PHONE_LAST_4": "3210",
    "EMAIL_NAME": "asha.menon42",
    "CUSTOMER_NUMBER": "0001234",
    "AREA": "Bandra West",
    "AREA_WORD_1": "Bandra",
    "PINCODE": "560034",
}


@pytest.mark.parametrize(
    "written",
    [
        "98765  43210",  # two spaces
        "98765 - 43210",
        "(98765) 43210",
        "98765/43210",
        "98765_43210",
        "+91 (98765) 43210",
        "98765, 43210",
        "9 8 7 6 5 4 3 2 1 0",
        "\u096f\u096e\u096d\u096c\u096b\u096a\u0969\u0968\u0967\u0966",  # Devanagari digits
    ],
)
def test_the_registered_number_is_masked_however_its_digits_are_set_apart(written: str) -> None:
    masked = mask_pii(f"call me on {written} please", known_values=KNOWN)

    assert "43210" not in re.sub(r"\D", "", masked.text)
    assert masked.text.startswith("call me on ")
    assert masked.text.endswith(" please")


@pytest.mark.parametrize(
    "written",
    [
        "ending 3210",
        "ending 43210",
        "last six 543210",
        "XXXXXX3210",
        "3 2 1 0",
        "32-10",
        "\u0969\u0968\u0967\u0966",  # 3210 in Devanagari digits
    ],
)
def test_the_last_digits_of_the_registered_number_are_masked_as_people_quote_them(
    written: str,
) -> None:
    masked = mask_pii(f"my number is {written}, wifi down", known_values=KNOWN)

    assert "3210" not in re.sub(r"\D", "", masked.text)


@pytest.mark.parametrize(
    "written",
    ["Ashaji", "ASHAJI", "Ashaaa", "asha_menon", "Asha-Menon", "AshaMenon", "menon42", "Menons"],
)
def test_a_name_is_masked_with_the_endings_and_joins_people_give_it(written: str) -> None:
    masked = mask_pii(f"this is {written} here, net is down", known_values=KNOWN)

    assert "asha" not in masked.text.lower()
    assert "menon" not in masked.text.lower()


@pytest.mark.parametrize(
    "written",
    [
        "560 034",
        "560-034",
        "Bandra-West",
        "Bandra (W)",
        "bandra",
        "C 0001234",
        "c-0001234",
        "cust_C0001234",
        "asha.menon42 at gmail dot com",
        "asha menon42",
    ],
)
def test_addresses_ids_and_the_mail_name_are_masked_in_their_other_spellings(written: str) -> None:
    masked = mask_pii(f"I am at {written}, please check", known_values=KNOWN)

    lowered = masked.text.lower()
    assert "bandra" not in lowered
    assert "menon" not in lowered
    assert "0001234" not in re.sub(r"[\s-]", "", masked.text)
    assert "560034" not in re.sub(r"\D", "", masked.text)


def test_a_short_name_is_masked_as_a_word_and_does_not_eat_the_words_it_begins() -> None:
    """Three letters are too few to guess an ending for: Rao is masked, a road is not. Drawn
    out or with the honorific it is still the name."""
    known = {"N1": "Rao", "N2": "Sen"}

    masked = mask_pii(
        "Rao here on MG road, please send someone. Raooo! Senji speaking", known_values=known
    )

    assert masked.text == "<N1> here on MG road, please send someone. <N1>! <N2> speaking"


def test_what_is_already_a_token_is_not_masked_a_second_time() -> None:
    """A customer whose area is called Phone Bazaar must not break the token of their number."""
    masked = mask_pii(
        "9845012345, near Phone Bazaar",
        known_values={"AREA": "Phone Bazaar", "AREA_WORD_1": "Phone"},
    )

    assert masked.text == "<PHONE_1>, near <AREA>"


def test_what_the_masking_is_known_not_to_catch_stays_known() -> None:
    """Said in the threat model, pinned here: digits spelled as words, a name in another
    script or with accents the record does not have, and anything the platform was never told
    (a street, a relative). If one of these starts being caught, this test says so."""
    for written in (
        "nine eight seven six five four three two one zero",
        "\u0906\u0936\u093e",  # the name in Devanagari
        "\u00c1sha",  # with an accent the record does not have
        "Flat 12, Rose Residency, MG Road",
    ):
        assert mask_pii(written, known_values=KNOWN).text == normalise(written)


def test_masking_by_value_stays_linear_on_text_built_against_it() -> None:
    hostile = ("asha" + "!" * 40 + " ") * 4000 + "9 " * 20000
    started = time.perf_counter()

    mask_pii(hostile, known_values=KNOWN)

    assert time.perf_counter() - started < 2.0


# --- secrets, and values that are not text ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "db_password",
        "jwt_secret",
        "access_token",
        "token",
        "Authorization",
        "api_key",
        "apiKey".lower(),
        "x-api-key",
        "X-Staff-Key",
        "staff_key",
        "signing_key",
        "hmac_key",
        "key",
        "cookie",
        "credentials",
        "dsn",
    ],
)
def test_a_field_named_like_a_secret_is_one_at_any_depth(name: str) -> None:
    redacted = redact_for_log({"request": {"headers": {name: "not-a-real-value-123"}}})

    assert redacted == {"request": {"headers": {name: "<redacted>"}}}


@pytest.mark.parametrize(
    "name",
    [
        "action_key",
        "dedupe_key",
        "idempotency_key",
        "close_key",
        "recheck_key",
        "last_action_key",
        "author",
        "input_tokens",
        "keyboard",
    ],
)
def test_a_field_that_only_sounds_like_one_is_not(name: str) -> None:
    """The key of an action is what an operator searches for; the author of a message and the
    number of tokens a model used are not secrets either."""
    assert redact_for_log({name: "incident:8f1c"}) == {name: "incident:8f1c"}


@pytest.mark.parametrize(
    ("text", "kept", "gone"),
    [
        (
            "GET /v1/x failed: Authorization: Bearer abc123def456ghi789 was refused",
            "was refused",
            "abc123def456ghi789",
        ),
        (
            "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJlLXZhbHVl expired",
            "expired",
            "eyJzdWIiOiJ0ZXN0In0",
        ),
        (
            "POST http://gateway/notify?api_key=sk-live-12345&to=asha failed",
            "to=asha failed",
            "sk-live-12345",
        ),
        ("connect postgresql://care:hunter2-not-real@db:5432/care refused", "@db:5432", "hunter2"),
        ("password=correct-horse-battery in settings", "in settings", "correct-horse"),
    ],
)
def test_a_secret_inside_a_sentence_is_taken_out_of_it(text: str, kept: str, gone: str) -> None:
    redacted = str(redact_for_log(text))

    assert gone not in redacted
    assert kept in redacted
    assert "<redacted>" in redacted


def test_values_that_are_not_text_are_redacted_as_what_they_would_print() -> None:
    """A logger prints whatever it is given. An exception, a set or a number is redacted as
    the text it would have printed, not let through because it was not a string."""
    error = ValueError("no customer with phone +91 98450 12345")

    redacted = redact_for_log(
        {
            "error": error,
            "seen": {"asha@example.com"},
            "raw": b"call 9845012345",
            "phone": 9845012345,
            "count": 12,
            "took_ms": 1794757004490,
            "ratio": 0.25,
            "ok": True,
            "nothing": None,
        }
    )

    assert redacted == {
        "error": "ValueError('no customer with phone <PHONE>')",
        "seen": ["<EMAIL>"],
        "raw": "b'call <PHONE>'",
        "phone": "<PHONE>",
        "count": 12,
        "took_ms": 1794757004490,
        "ratio": 0.25,
        "ok": True,
        "nothing": None,
    }


@pytest.mark.parametrize(
    "text",
    [
        "1990-05-12.john@gmail.com",
        "2024-01-15+john@gmail.com",
        "1990-05-12@gmail.com",
        "OUT-20240115-0001.john@gmail.com",
        "AP-abcdef1234@example.com",
        "12345678-1234-1234-1234-1234567890ab.x@y.com",
    ],
)
def test_an_address_that_begins_like_a_date_or_an_id_is_still_an_address(text: str) -> None:
    assert redact_for_log(f"mail {text} bounced") == "mail <EMAIL> bounced"
