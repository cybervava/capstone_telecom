"""Complaint reference numbers.

A reference is issued from the intake ledger's own sequence, so it never depends on another
system being up. Format: ``SKL-YYMMDD-NNNNNN-C``

* ``YYMMDD``  date the contact was received, in Indian Standard Time
* ``NNNNNN``  ledger sequence, zero-padded to six digits (wider when it grows)
* ``C``       Luhn check digit over the date and sequence digits, so a misread digit is caught
              before anyone searches for the wrong complaint

The sequence alone makes the reference unique. Possession of a reference grants nothing: account
data is released only after separate authentication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

PREFIX = "SKL"
BUSINESS_TZ = ZoneInfo("Asia/Kolkata")
MIN_SEQUENCE_WIDTH = 6

_PATTERN = re.compile(rf"^{PREFIX}-(\d{{6}})-(\d{{{MIN_SEQUENCE_WIDTH},}})-(\d)$")


class InvalidReferenceError(ValueError):
    """Raised when a string is not a well-formed reference with a correct check digit."""


@dataclass(frozen=True)
class ParsedReference:
    date_digits: str
    sequence: int


def format_reference(sequence: int, received_at: datetime) -> str:
    """Build the reference for a ledger sequence value and the time the contact was received."""
    if sequence <= 0:
        raise ValueError("sequence must be a positive integer")
    if received_at.tzinfo is None:
        raise ValueError("received_at must carry a timezone")
    date_digits = received_at.astimezone(BUSINESS_TZ).strftime("%y%m%d")
    sequence_digits = f"{sequence:0{MIN_SEQUENCE_WIDTH}d}"
    check = _luhn_check_digit(date_digits + sequence_digits)
    return f"{PREFIX}-{date_digits}-{sequence_digits}-{check}"


def is_valid_reference(candidate: str) -> bool:
    """True when the string has the reference shape and its check digit is correct."""
    match = _PATTERN.match(candidate)
    if match is None:
        return False
    date_digits, sequence_digits, check = match.groups()
    return _luhn_check_digit(date_digits + sequence_digits) == int(check)


def parse_reference(candidate: str) -> ParsedReference:
    """Split a valid reference into its parts, or raise ``InvalidReferenceError``."""
    if not is_valid_reference(candidate):
        raise InvalidReferenceError("not a valid complaint reference")
    _, date_digits, sequence_digits, _ = candidate.split("-")
    return ParsedReference(date_digits=date_digits, sequence=int(sequence_digits))


def _luhn_check_digit(digits: str) -> int:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10
