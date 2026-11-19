"""Prompts are Markdown files shipped with the package and versioned by their content.

Changing a prompt changes its version string, and that string is written to the audit trail with
every call, so any answer can be traced to the exact prompt text that produced it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

# "<" and ">" that are not part of a masking token such as <PHONE_1> or <NAME>.
_ANGLE = re.compile(r"<(?![A-Z_]+(?:_\d+)?>)|(?<![A-Z\d])>")
_LOOKALIKE = {"<": "\u2039", ">": "\u203a"}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str


@lru_cache(maxsize=32)
def load_prompt(name: str) -> Prompt:
    """Load ``llm/prompts/<name>.md``. The version is ``<name>@<first 12 hex of SHA-256>``."""
    source = resources.files("skylink_care.llm") / "prompts" / f"{name}.md"
    if not source.is_file():
        raise FileNotFoundError(f"prompt not found: {name}")
    text = source.read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return Prompt(name=name, text=text, version=f"{name}@{digest}")


def customer_message(masked_text: str) -> str:
    """The customer's masked text as a model is given it: wrapped in tags, so it reads as data."""
    return f"<customer_message>\n{as_data(masked_text)}\n</customer_message>"


def as_data(masked_text: str) -> str:
    """Make the customer's text unable to close the tag it is wrapped in.

    Angle brackets are swapped for look-alike characters, except around the masking tokens
    (``<PHONE_1>``), which the prompt refers to.
    """
    return _ANGLE.sub(lambda match: _LOOKALIKE[match.group(0)], masked_text)
