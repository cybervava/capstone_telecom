"""Canonical hashing of JSON-like values.

Used wherever two requests must be recognised as "the same": idempotent intake, frozen action
requests, and approvals that are bound to exactly what the approver saw.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialise deterministically: sorted keys, no insignificant whitespace, UTF-8 kept as is."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    """SHA-256 of the canonical JSON form."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
