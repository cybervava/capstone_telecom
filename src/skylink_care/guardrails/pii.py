"""Personal-data masking and identifier extraction.

``mask_pii`` runs on every piece of free text before it is sent to a language model or written to
a log. It is deliberately pattern-based: deterministic, fast enough for a surge, and biased toward
masking too much (any long digit run goes) rather than too little. Values the platform already
knows for the customer (name, area) are passed in and masked literally, which a pattern cannot do.

``extract_identifiers`` is the other direction: triage uses it to find what the customer offered
as identification. Extracted values stay inside the platform and go only to the CRM adapter.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

_KEEP = "KEEP"
# Order matters: the first alternative that matches at a position wins.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (_KEEP, r"SKL-\d{6}-\d{6,}-\d"),  # complaint reference: not personal data, must survive
    # Bounded and anchored on its left edge: without that, a long run of letters with no "@"
    # is re-scanned from every position.
    ("EMAIL", r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}"),
    ("CUSTOMER_ID", r"\b[Cc]\d{7}\b"),
    ("SERVICE_ID", r"\b[Ss]\d{7}\b"),
    ("PHONE", r"(?<![\d-])(?:\+91[\s.-]?|0)?[6-9]\d{4}[\s.-]?\d{5}(?!\d)"),
    ("NUMBER", r"(?<!\d)\d(?:[\s.-]?\d){7,}(?!\d)"),  # any other run of eight or more digits
)
_COMBINED = re.compile("|".join(f"(?P<{kind}>{pattern})" for kind, pattern in _PATTERNS))
# For logs and the audit trail: internal ids stay readable, contact details and numbers do not.
_LOG_KINDS = frozenset({"EMAIL", "PHONE", "NUMBER"})
_NON_DIGITS = re.compile(r"\D")


@dataclass(frozen=True)
class Masked:
    """Masked text plus what is needed to describe or undo the masking inside the platform."""

    text: str
    replacements: tuple[tuple[str, str], ...]  # (token, original spelling)
    # The kinds of personal data that were masked, sorted. Never the values.
    found: tuple[str, ...] = ()

    def restore(self, text: str) -> str:
        """Put original values back into text that uses this masking's tokens."""
        for token, original in self.replacements:
            text = text.replace(token, original)
        return text


@dataclass(frozen=True)
class Identifiers:
    phones: tuple[str, ...]
    customer_ids: tuple[str, ...]
    emails: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.phones or self.customer_ids or self.emails)


def normalise(text: str) -> str:
    """Undo the cheap ways of hiding a number or address from a pattern.

    Compatibility characters are folded (full-width digits become ASCII) and invisible format
    characters such as zero-width spaces are removed, so ``98765<ZWSP>43210`` is seen as a
    phone number.
    """
    folded = unicodedata.normalize("NFKC", text)
    return "".join(char for char in folded if unicodedata.category(char) != "Cf")


def mask_pii(text: str, *, known_values: Mapping[str, str] | None = None) -> Masked:
    """Replace personal data with stable tokens such as ``<PHONE_1>``.

    ``known_values`` maps a label to a literal the platform knows belongs to the customer, for
    example ``{"NAME": "Asha Menon"}``; it is replaced case-insensitively by ``<NAME>``.

    Pattern-based masking cannot recognise a free-text name or street address it was not told
    about. That is why unidentified customers' text is never sent to an external model at all
    (see ``LLMGateway.read_contact``).
    """
    text = normalise(text)
    tokens: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    replacements: list[tuple[str, str]] = []
    kinds: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        kind = match.lastgroup or _KEEP
        original = match.group(0)
        if kind == _KEEP:
            return original
        key = (kind, _normalise(kind, original))
        if key not in tokens:
            counters[kind] = counters.get(kind, 0) + 1
            tokens[key] = f"<{kind}_{counters[kind]}>"
            replacements.append((tokens[key], original))
            kinds.add(kind)
        return tokens[key]

    masked = _COMBINED.sub(substitute, text)
    for label, value in (known_values or {}).items():
        if not value.strip():
            continue
        token = f"<{label}>"
        pattern = re.compile(re.escape(value), re.IGNORECASE)
        first = pattern.search(masked)
        if first is not None:
            replacements.append((token, first.group(0)))
            kinds.add(label)
            masked = pattern.sub(token, masked)
    return Masked(text=masked, replacements=tuple(replacements), found=tuple(sorted(kinds)))


def extract_identifiers(text: str) -> Identifiers:
    """Find what the customer offered as identification, normalised and de-duplicated."""
    found: dict[str, list[str]] = {"PHONE": [], "CUSTOMER_ID": [], "EMAIL": []}
    for match in _COMBINED.finditer(normalise(text)):
        kind = match.lastgroup
        if kind in found:
            value = _normalise(kind, match.group(0))
            if value not in found[kind]:
                found[kind].append(value)
    return Identifiers(
        phones=tuple(found["PHONE"]),
        customer_ids=tuple(found["CUSTOMER_ID"]),
        emails=tuple(found["EMAIL"]),
    )


def _normalise(kind: str, value: str) -> str:
    if kind == "PHONE":
        return "+91" + _NON_DIGITS.sub("", value)[-10:]
    if kind in {"CUSTOMER_ID", "SERVICE_ID"}:
        return value.upper()
    if kind == "EMAIL":
        return value.lower()
    return _NON_DIGITS.sub("", value)


def redact_for_log(value: object) -> object:
    """Return ``value`` with phones, emails and long numbers masked in every string it contains.

    Customer and service ids are kept: the audit trail must say whose case it was. Dicts, lists
    and tuples are walked recursively; other types are returned unchanged.
    """
    if isinstance(value, str):
        return _COMBINED.sub(_log_substitute, normalise(value))
    if isinstance(value, dict):
        return {key: redact_for_log(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact_for_log(item) for item in value]
    return value


def _log_substitute(match: re.Match[str]) -> str:
    kind = match.lastgroup
    return f"<{kind}>" if kind in _LOG_KINDS else match.group(0)
