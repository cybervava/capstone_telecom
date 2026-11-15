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
        for number, step in enumerate(section.steps, start=1):
            for hazard in _HAZARDS.finditer(step):
                if not _FORBIDS.search(step[: hazard.start()]):
                    raise GuideError(
                        f"{source}: step {number} of section {section.section_id!r} tells the"
                        f" customer to do something unsafe ({hazard.group(0)!r})"
                    )
