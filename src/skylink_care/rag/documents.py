"""Device guides as the platform reads them: front matter, sections and their numbered steps.

A guide is a Markdown file with YAML front matter and ``## Title {#section-id}`` headings. The
section id is the unit of citation: an answer names the guide, its version and the section, so a
person can open exactly what the customer was told.

Only documents of type ``device_guide`` are accepted. Anything else in the folder (in particular
material marked ``untrusted``) is refused here, before it can reach the index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from skylink_care.guardrails.pii import normalise
from skylink_care.hashing import canonical_hash

DEVICE_GUIDE = "device_guide"
# Sections an automated answer may quote to a customer. A factory reset erases the customer's
# settings and the guides themselves say to do it only when support asks; the rest of a guide is
# reference material or advice about calling us. Those stay with a person.
SELF_SERVICE_SECTIONS = ("no-internet", "wifi", "slow-speed", "power-cycle")
MAX_GUIDE_BYTES = 256 * 1024
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_SECTION = re.compile(r"^## (?P<title>.+?) \{#(?P<id>[a-z0-9-]+)\}\s*$", re.MULTILINE)
_STEP_START = re.compile(r"^\d+\.\s+(.+)$")
_REQUIRED = ("doc_id", "doc_type", "title", "device_model", "version", "access_roles")
# These identifiers are shown to customers in the citation, so their shape is fixed.
_DOC_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERSION = re.compile(r"^\d+(?:\.\d+){0,3}$")
_MODEL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")
# What a customer must never be told to do by an automated message. A step may mention these
# only to forbid them ("do not unplug the fibre cable").
_HAZARDS = re.compile(
    r"factory reset|paperclip|reset (?:button|hole)|open(?:ing)? the (?:casing|case|unit|router)"
    r"|unscrew|look(?:ing)? into|(?:unplug|disconnect|remove|pull out) (?:the |your )?"
    r"(?:green )?fib(?:re|er)|fib(?:re|er) connector",
    re.IGNORECASE,
)
_FORBIDS = re.compile(r"\b(?:do not|don't|never|rather than|not)\b[^.;:]*$", re.IGNORECASE)
# What has no place in a step a customer is told to follow at home. Steps are quoted word for
# word, so a guide that has been tampered with speaks to the customer directly: an instruction
# for "the assistant" is read by nobody but the customer, and a number to call is called.
_OUT_OF_PLACE: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "speaks to an assistant, not to the customer",
        re.compile(
            r"\b(?:assistant|chatbot|language model|system prompt|a\.?i\.?)\b"
            r"|\b(?:ignore|disregard|forget|override)\b[^.]{0,60}"
            r"\b(?:instructions?|rules|prompts?|guidelines)\b"
            r"|\binstructions? (?:to|for) (?:the|any|an)\b|\byou are now\b",
            re.IGNORECASE,
        ),
    ),
    (
        "speaks of money",
        re.compile(
            r"\binr\b|₹|\brs\.?\s*\d|\brupees?\b|\bcredits?\b|\brefunds?\b|\bcompensat"
            r"|\bowes?\b|\bbank\b|\bupi\b|\bcard number\b|\bcvv\b|\bpay(?:ments?|ing)?\b"
            r"|\bvouchers?\b|\bwallet\b",
            re.IGNORECASE,
        ),
    ),
    (
        "asks the customer to hand something over",
        re.compile(
            r"\bone[- ]time (?:password|passcode|code|pin)\b|\botp\b|\bverification code\b"
            r"|\b(?:share|send us|tell us|reply with|read out|give (?:us|them|him|her))\b"
            r"|\b(?:provide|submit)\b[^.]{0,40}\b(?:code|password|pin|number)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sends the customer to a number or a channel",
        re.compile(
            r"\b(?:call|dial|whatsapp|telegram|sms|e-?mail|text us|message us|phone us)\b"
            r"|\d[\d\s()-]{6,}\d",
            re.IGNORECASE,
        ),
    ),
)
# The one address a step may name: the router's own admin page, on the home network.
_URL = re.compile(r"(?:(?:https?|ftp)://|www\.|//)([^\s/`'\")>]*)", re.IGNORECASE)
_HOME_NETWORK = re.compile(
    r"^(?:192\.168|10|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?::\d+)?$"
)
# An address written without its scheme: a name with a top-level domain, or four numbers.
_BARE_HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_BARE_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_AFTER_AN_ADDRESS = ".,;:!?*)]"
MAX_TITLE_CHARS = 120


class GuideError(ValueError):
    """The file is not a device guide the platform can index."""


@dataclass(frozen=True)
class Section:
    section_id: str
    title: str
    body: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class Guide:
    doc_id: str
    doc_type: str
    title: str
    device_model: str
    version: str
    access_roles: tuple[str, ...]
    sections: tuple[Section, ...]

    @property
    def content_hash(self) -> str:
        return canonical_hash(
            {
                "doc_id": self.doc_id,
                "version": self.version,
                "sections": [[s.section_id, s.title, s.body] for s in self.sections],
            }
        )


def parse_guide(text: str, *, source: str = "<text>") -> Guide:
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise GuideError(f"{source}: no front matter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise GuideError(f"{source}: front matter is not valid YAML") from exc
    if not isinstance(meta, dict):
        raise GuideError(f"{source}: front matter is not a mapping")
    missing = [name for name in _REQUIRED if not meta.get(name)]
    if missing:
        raise GuideError(f"{source}: front matter lacks {', '.join(missing)}")
    if meta["doc_type"] != DEVICE_GUIDE:
        raise GuideError(f"{source}: doc_type {meta['doc_type']!r} is not indexed")
    for name, shape in (("doc_id", _DOC_ID), ("version", _VERSION), ("device_model", _MODEL)):
        if not shape.match(str(meta[name])):
            raise GuideError(f"{source}: {name} {str(meta[name])[:40]!r} has an unexpected form")
    sections = _sections(match.group(2))
    if not sections:
        raise GuideError(f"{source}: no sections with an id")
    problem = not_a_title(str(meta["title"]))
    if problem is None and ":" in str(meta["title"]):
        # The index keeps "<guide>: <section>" as one title and the answer splits it there.
        problem = "contains a colon"
    if problem is not None:
        raise GuideError(f"{source}: the title of the guide {problem}")
    _check_self_service_steps(sections, source)
    return Guide(
        doc_id=str(meta["doc_id"]),
        doc_type=str(meta["doc_type"]),
        title=str(meta["title"]),
        device_model=str(meta["device_model"]),
        version=str(meta["version"]),
        access_roles=tuple(str(role) for role in meta["access_roles"]),
        sections=sections,
    )


def load_guides(folder: Path) -> list[Guide]:
    """Every guide in the folder, in file-name order. One bad file fails the whole load."""
    guides = []
    for path in sorted(folder.glob("*.md")):
        if path.stat().st_size > MAX_GUIDE_BYTES:
            raise GuideError(f"{path.name}: larger than {MAX_GUIDE_BYTES} bytes")
        guides.append(parse_guide(path.read_text(encoding="utf-8"), source=path.name))
    models = [guide.device_model for guide in guides]
    twice = sorted({model for model in models if models.count(model) > 1})
    if twice:
        # Two guides for one model would let a superseded one be quoted.
        raise GuideError(f"more than one guide for model {', '.join(twice)}")
    return guides


def _sections(body: str) -> tuple[Section, ...]:
    headings = list(_SECTION.finditer(body))
    sections = []
    for position, heading in enumerate(headings):
        end = headings[position + 1].start() if position + 1 < len(headings) else len(body)
        text = body[heading.end() : end].strip()
        sections.append(
            Section(
                section_id=heading.group("id"),
                title=heading.group("title").strip(),
                body=text,
                steps=_steps(text),
            )
        )
    return tuple(sections)


def _steps(text: str) -> tuple[str, ...]:
    """The numbered steps of a section, each with any indented lines that continue it.

    A continuation often carries the caution ("   Do not unplug the fibre cable."), so dropping
    it would change what the step tells the customer to do.
    """
    steps: list[str] = []
    in_step = False
    for line in text.splitlines():
        start = _STEP_START.match(line)
        if start:
            steps.append(start.group(1).strip())
            in_step = True
        elif in_step and line.startswith((" ", "\t")) and line.strip():
            steps[-1] = f"{steps[-1]} {line.strip()}"
        else:
            in_step = False
    return tuple(steps)


def _check_self_service_steps(sections: tuple[Section, ...], source: str) -> None:
    """Refuse a guide whose quotable sections tell the customer to do something hazardous.

    The steps are sent to customers verbatim, so the guide is the control. An edit that adds a
    reset or a fibre instruction to a quotable section stops the index build instead of
    reaching a customer.
    """
    for section in sections:
        if section.section_id not in SELF_SERVICE_SECTIONS:
            continue
        heading = not_a_title(section.title)
        if heading is not None:
            raise GuideError(f"{source}: the heading of section {section.section_id!r} {heading}")
        for number, step in enumerate(section.steps, start=1):
            problem = not_a_step(step)
            if problem is not None:
                raise GuideError(
                    f"{source}: step {number} of section {section.section_id!r} {problem}"
                )


def not_a_step(step: str) -> str | None:
    """Why this text may not be quoted to a customer as a step to follow, or None.

    Asked where a guide comes in (the index build) and again where a step goes out (the
    answer): what is stored between the two is not taken on trust. It is a list of what has
    no place in a step, not a proof that a step is sound: whoever writes the guides is
    trusted for their wording.
    """
    # Look-alike and invisible characters are how a word is hidden from a pattern.
    text = normalise(step)
    for hazard in _HAZARDS.finditer(text):
        if not _FORBIDS.search(text[: hazard.start()]):
            return f"tells the customer to do something unsafe ({hazard.group(0)!r})"
    for address in _URL.finditer(text):
        if not _HOME_NETWORK.match(address.group(1).rstrip(_AFTER_AN_ADDRESS)):
            return f"sends the customer to an address outside their home ({address.group(0)!r})"
    # The numbers of an address are not a phone number: addresses are judged on their own.
    without_addresses = _URL.sub(" ", text)
    for address in _BARE_IP.finditer(without_addresses):
        if not _HOME_NETWORK.match(address.group(0)):
            return f"sends the customer to an address outside their home ({address.group(0)!r})"
    without_addresses = _BARE_IP.sub(" ", without_addresses)
    named = _BARE_HOST.search(without_addresses)
    if named is not None:
        return f"sends the customer to an address outside their home ({named.group(0)!r})"
    for problem, pattern in _OUT_OF_PLACE:
        found = pattern.search(without_addresses)
        if found is not None:
            return f"{problem} ({found.group(0).strip()!r})"
    return None


def not_a_title(title: str) -> str | None:
    """Why this text may not be sent to a customer as the name of a guide or of a section.

    The answer names both ("from the <guide>, section <section>"), so they are words to the
    customer as much as the steps are.
    """
    if not title.strip() or len(title) > MAX_TITLE_CHARS:
        return f"is empty or longer than {MAX_TITLE_CHARS} characters"
    if any(not char.isprintable() for char in title):
        return "has characters that cannot be printed"
    return not_a_step(title)
