"""Prompts are Markdown files shipped with the package and versioned by their content.

Changing a prompt changes its version string, and that string is written to the audit trail with
every call, so any answer can be traced to the exact prompt text that produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


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
