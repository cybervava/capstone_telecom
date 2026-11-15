from datetime import UTC, datetime

import pytest

from skylink_care.domain.reference import (
    InvalidReferenceError,
    format_reference,
    is_valid_reference,
    parse_reference,
)

RECEIVED = datetime(2026, 11, 15, 4, 30, tzinfo=UTC)  # 10:00 IST on 15 Nov


def test_reference_has_prefix_ist_date_padded_sequence_and_check_digit() -> None:
    ref = format_reference(123, RECEIVED)

    assert ref.startswith("SKL-261115-000123-")
    assert len(ref) == len("SKL-261115-000123-0")
    assert ref[-1].isdigit()


def test_date_part_uses_indian_standard_time_not_utc() -> None:
    late_utc = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)  # already 16 Nov 01:30 in IST

    assert format_reference(1, late_utc).startswith("SKL-261116-")


def test_sequence_wider_than_six_digits_is_not_truncated() -> None:
    ref = format_reference(12_345_678, RECEIVED)

    assert "-12345678-" in ref
    assert is_valid_reference(ref)


def test_same_inputs_give_same_reference() -> None:
    assert format_reference(77, RECEIVED) == format_reference(77, RECEIVED)


def test_different_sequences_give_different_references() -> None:
    refs = {format_reference(seq, RECEIVED) for seq in range(1, 2001)}

    assert len(refs) == 2000


@pytest.mark.parametrize("position", range(4, 10))
def test_any_single_digit_typo_in_the_date_is_detected(position: int) -> None:
    ref = format_reference(4821, RECEIVED)
    wrong_digit = "5" if ref[position] != "5" else "6"
    typo = ref[:position] + wrong_digit + ref[position + 1 :]

    assert is_valid_reference(ref)
    assert not is_valid_reference(typo)


def test_single_digit_typo_in_the_sequence_is_detected() -> None:
    ref = format_reference(4821, RECEIVED)

    assert not is_valid_reference(ref.replace("004821", "004831"))


def test_adjacent_transposition_in_the_sequence_is_detected() -> None:
    ref = format_reference(4821, RECEIVED)

    assert not is_valid_reference(ref.replace("004821", "004281"))


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "SKL",
        "SKL-261115-000123",
        "ABC-261115-000123-4",
        "SKL-2611-000123-4",
        "SKL-261115-12x456-4",
    ],
)
def test_malformed_references_are_invalid(candidate: str) -> None:
    assert not is_valid_reference(candidate)


def test_parse_returns_sequence_and_date_digits() -> None:
    ref = format_reference(4821, RECEIVED)

    parsed = parse_reference(ref)

    assert parsed.sequence == 4821
    assert parsed.date_digits == "261115"


def test_parse_rejects_a_reference_with_a_bad_check_digit() -> None:
    ref = format_reference(4821, RECEIVED)
    bad = ref[:-1] + ("0" if ref[-1] != "0" else "1")

    with pytest.raises(InvalidReferenceError):
        parse_reference(bad)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        format_reference(1, datetime(2026, 11, 15, 4, 30))  # noqa: DTZ001 - the point of the test


@pytest.mark.parametrize("sequence", [0, -1])
def test_non_positive_sequence_is_rejected(sequence: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        format_reference(sequence, RECEIVED)
