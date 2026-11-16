"""Personal-data masking and identifier extraction.

``mask_pii`` runs on every piece of free text before it is sent to a language model or written to
a log. It is deliberately pattern-based: deterministic, fast enough for a surge, and biased toward
masking too much (any long digit run goes) rather than too little. Values the platform already
knows for the customer (the name and its parts, where their service is, the registered mobile
and its last digits) are passed in and masked by value, in the spellings people use, which a
pattern cannot do.

What it cannot catch is said here and pinned by a test: digits spelled as words, a name in
another script or with accents the record does not have, and anything the platform was never
told, such as a street or a relative's name.

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
# What the platform's own records are made of, and must be readable in them: the time of a
# piece of evidence, and the identifiers that tie an entry to the gateway's side of a call.
# Recognised only in logs and the audit trail. In a customer's own text nothing is read as a
# record: a number written behind "AP-" is masked for a model like any other.
_RECORD_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "KEEP_TIME",
        r"(?<![\d-])(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
        r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?![\d-])",
    ),
    ("KEEP_OUTAGE", r"\bOUT-\d{8}-\d{4}\b"),
    # The three below are random hexadecimal. One that happens to be all digits is not told
    # apart from a number and is masked like one: rare, and the safe way round. One with a
    # single letter among digits is kept: to get a number into a record that way a customer
    # would have to dress up their own.
    ("KEEP_SYSTEM_ID", r"\b(?:INC|AP|CR|MSG|ALM|LT)-(?=\d*[A-Fa-f])[0-9A-Fa-f]{10,12}\b"),
    (
        "KEEP_UUID",
        r"(?<![0-9A-Za-z-])(?=[0-9-]*[a-f])"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9A-Za-z-])",
    ),
    ("KEEP_REQUEST_ID", r"(?<![0-9A-Za-z])(?=\d*[a-f])[0-9a-f]{32}(?![0-9A-Za-z])"),
)
# None of them is kept where it is the beginning of an email address: the address is masked.
_NOT_AN_ADDRESS = r"(?![A-Za-z0-9._%+-]{0,64}@)"
_FOR_RECORDS = re.compile(
    "|".join(
        f"(?P<{kind}>{pattern})"
        for kind, pattern in (
            _PATTERNS[0],
            *((kind, pattern + _NOT_AN_ADDRESS) for kind, pattern in _RECORD_PATTERNS),
            *_PATTERNS[1:],
        )
    )
)
# A field is a secret when one of the words of its name is one of these ...
_SECRET_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "authorization",
        "auth",
        "apikey",
        "cookie",
        "bearer",
        "credential",
        "credentials",
        "dsn",
        "conninfo",
    }
)
# ... or when the name is "key" or ends with it. Except for the keys that tie an action to its
# effect: they are what an operator searches the records for, and no secret.
_TRACE_KEYS = frozenset(
    {"action_key", "dedupe_key", "idempotency_key", "close_key", "recheck_key", "last_action_key"}
)
REDACTED = "<redacted>"
# A secret in the middle of a sentence: a bearer token, a signed token, a value given to a
# parameter with a telling name, the user and password of an address.
_SECRET_VALUES = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r"|(?P<name>\b(?:[A-Za-z0-9]{1,20}[_-]){0,3}"
    r"(?:token|api[_-]?key|apikey|password|passwd|secret|key)=)[^&\s\"']{1,512}"
    r"|(?P<scheme>\b[a-z][a-z0-9+.-]{1,20}://)[^/\s:@]{1,128}:[^/\s@]{1,256}(?=@)",
    re.IGNORECASE,
)
# A whole number that is a mobile number by its shape is masked like one written as text.
_MOBILE_NUMBER = re.compile(r"(?:91)?[6-9]\d{9}")
_NON_DIGITS = re.compile(r"\D")
# A known value shorter than this is not masked: an initial is not a name.
MIN_KNOWN_CHARS = 2
# A known number has at least this many digits. Each of its digits may be set apart from the
# next by a few other characters: more for a whole phone number, which people break up with
# brackets and dashes, than for a short run, where a wide gap would join unrelated digits.
MIN_KNOWN_DIGITS = 4
_GAP_IN_A_LONG_NUMBER, _GAP_IN_A_SHORT_NUMBER, _LONG_NUMBER = r"\D{0,4}", r"\D{0,2}", 10
_LETTER = r"[^\W\d_]"
# Between the parts of one known value (a first and a last name, the words of an area).
_JOIN = r"[\W_]{0,5}"
# A name or a place of this many letters is also masked with a short ending of the customer's
# own ("Ashaji", "Menons"). A shorter one begins too many other words for that ("Sen" begins
# "send"): it is masked with its last letter drawn out and with the honorific, and no more.
_TAKES_AN_ENDING, _ENDING, _HONORIFIC = 4, rf"{_LETTER}{{0,3}}", r"(?:ji|jee)?"
_TOKEN = re.compile(r"<[A-Z][A-Z0-9_]*>")


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

    Compatibility characters are folded (full-width digits become ASCII), the digits of every
    script become ASCII digits, and invisible format characters such as zero-width spaces are
    removed, so ``98765<ZWSP>43210`` is seen as a phone number and so is one in Devanagari.
    """
    return "".join(
        _plain(char)
        for char in unicodedata.normalize("NFKC", text)
        if unicodedata.category(char) != "Cf"
    )


def _plain(char: str) -> str:
    if char.isascii() or unicodedata.category(char) != "Nd":
        return char
    return str(unicodedata.digit(char))


def mask_pii(text: str, *, known_values: Mapping[str, str] | None = None) -> Masked:
    """Replace personal data with stable tokens such as ``<PHONE_1>``.

    ``known_values`` maps a label to a value the platform knows belongs to the customer, for
    example ``{"NAME": "Asha Menon"}``; it is replaced by ``<NAME>`` wherever it stands as a
    word of its own, in any case, however its parts are joined, and with a short ending of
    the customer's own. A value that is a number is found with its digits set apart. The
    longest value is masked first, so a full name is one token and not two.

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
    for label, pattern in _known_patterns(known_values or {}):
        masked, spelling = _mask_outside_tokens(masked, pattern, f"<{label}>")
        if spelling is not None:
            replacements.append((f"<{label}>", spelling))
            kinds.add(label)
    return Masked(text=masked, replacements=tuple(replacements), found=tuple(sorted(kinds)))


def _mask_outside_tokens(text: str, pattern: re.Pattern[str], token: str) -> tuple[str, str | None]:
    """Replace what the pattern finds with the token, except inside a token that is already
    there: a customer whose area is called Phone Bazaar must not break ``<PHONE_1>``.
    Returns the text and the first spelling that was replaced, if any."""
    taken = [found.span() for found in _TOKEN.finditer(text)]
    spellings: list[str] = []

    def replace(found: re.Match[str]) -> str:
        if any(start < found.end() and found.start() < end for start, end in taken):
            return found.group(0)
        spellings.append(found.group(0))
        return token

    replaced = pattern.sub(replace, text)
    return replaced, spellings[0] if spellings else None


def _known_patterns(known_values: Mapping[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    """One pattern for each known value worth masking, the longest value first."""
    patterns = []
    for label, value in sorted(known_values.items(), key=lambda item: (-len(item[1]), item[0])):
        literal = normalise(value)
        pattern = _as_a_number(literal) or _as_words(literal)
        if pattern is not None:
            patterns.append((label, re.compile(pattern, re.IGNORECASE)))
    return patterns


def _as_a_number(literal: str) -> str | None:
    """A value made of digits: found as a number of its own, its digits set apart or not."""
    digits = _NON_DIGITS.sub("", literal)
    if len(digits) < MIN_KNOWN_DIGITS or re.search(_LETTER, literal):
        return None
    gap = _GAP_IN_A_LONG_NUMBER if len(digits) >= _LONG_NUMBER else _GAP_IN_A_SHORT_NUMBER
    return rf"(?<!\d){gap.join(digits)}(?!\d)"


def _as_words(literal: str) -> str | None:
    """A value made of words: found where no letter stands before or after it, its parts
    joined in any way, and for a long enough last part with a short ending added."""
    parts = [part for part in re.split(r"[\W_]+", literal) if part]
    if sum(len(part) for part in parts) < MIN_KNOWN_CHARS:
        return None
    last, ending = parts[-1], ""
    if last[-1].isalpha():
        drawn_out = rf"{re.escape(last[-1])}{{0,3}}{_HONORIFIC}"
        ending = _ENDING if len(last) >= _TAKES_AN_ENDING else drawn_out
    spelled = _JOIN.join(re.escape(part) for part in parts)
    return rf"(?<!{_LETTER}){spelled}{ending}(?!{_LETTER})"


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


def is_secret_name(name: str) -> bool:
    """Whether a field of this name holds a secret, whatever its value looks like."""
    lowered = re.sub(r"[-\s.]+", "_", name.strip().lower())
    if lowered in _TRACE_KEYS:
        return False
    return (
        lowered == "key"
        or lowered.endswith("_key")
        or not _SECRET_WORDS.isdisjoint(lowered.split("_"))
    )


def redact_for_log(value: object) -> object:
    """Return ``value`` as it may be written to a log or to the audit trail.

    Phones, emails and long numbers are masked in every string, and a secret is taken out of
    a sentence that carries one. A field named like a secret loses its value at any depth.
    Customer and service ids are kept: the audit trail must say whose case it was. So are
    dates and times and the identifiers of other systems (an outage, an incident, a request),
    without which an entry cannot be followed anywhere.

    Dicts, lists, tuples and sets are walked. Anything else that is not a plain value (an
    exception, an object) is redacted as the text it would print: a logger prints whatever it
    is given, so nothing passes for not being a string.
    """
    if value is None or isinstance(value, bool | float):
        return value
    if isinstance(value, str):
        scrubbed = _SECRET_VALUES.sub(_without_the_secret, normalise(value))
        return _FOR_RECORDS.sub(_log_substitute, scrubbed)
    if isinstance(value, int):
        return "<PHONE>" if _MOBILE_NUMBER.fullmatch(str(value)) else value
    if isinstance(value, dict):
        return {
            key: REDACTED if isinstance(key, str) and is_secret_name(key) else redact_for_log(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_for_log(item) for item in value]
    if isinstance(value, set | frozenset):
        return [redact_for_log(item) for item in sorted(value, key=repr)]
    return redact_for_log(repr(value))


def _without_the_secret(match: re.Match[str]) -> str:
    """Keep what says a secret was there (the parameter's name, the scheme of the address)."""
    return f"{match.group('name') or match.group('scheme') or ''}{REDACTED}"


def _log_substitute(match: re.Match[str]) -> str:
    kind = match.lastgroup
    return f"<{kind}>" if kind in _LOG_KINDS else match.group(0)
