"""Create .env from .env.example, replacing every `change-me` with a fresh random value.

Run through `make init`. Refuses to overwrite an existing .env so local secrets are never lost.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

PLACEHOLDER = "change-me"
ROOT = Path(__file__).resolve().parent.parent


def render(template: str) -> str:
    """Return the template with each placeholder replaced by its own random token."""
    lines = []
    for line in template.splitlines():
        if not line.lstrip().startswith("#") and line.rstrip().endswith(f"={PLACEHOLDER}"):
            key = line.split("=", 1)[0]
            lines.append(f"{key}={secrets.token_urlsafe(24)}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def main() -> int:
    example = ROOT / ".env.example"
    target = ROOT / ".env"
    if target.exists():
        sys.stderr.write(".env already exists; leaving it untouched.\n")
        return 0
    # Created owner-only in one step: the secrets are never on disk with wider permissions.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(render(example.read_text(encoding="utf-8")))
    sys.stdout.write("Wrote .env with generated local secrets.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
