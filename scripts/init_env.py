"""Create .env from .env.example, replacing every `change-me` with a fresh random value.

Run through `make init`. An existing .env is never rewritten: no value in it is changed or
removed. If .env.example has since gained a secret that the .env does not have, that one line
is added with a freshly generated value, and its name is printed.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

PLACEHOLDER = "change-me"
ROOT = Path(__file__).resolve().parent.parent


def secret_keys(template: str) -> list[str]:
    """The names in the template whose value is the placeholder: the generated secrets."""
    return [
        line.split("=", 1)[0]
        for line in template.splitlines()
        if not line.lstrip().startswith("#") and line.rstrip().endswith(f"={PLACEHOLDER}")
    ]


def render(template: str) -> str:
    """Return the template with each placeholder replaced by its own random token."""
    generated = set(secret_keys(template))
    lines = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        if not line.lstrip().startswith("#") and key in generated:
            lines.append(f"{key}={secrets.token_urlsafe(24)}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def missing_secrets(template: str, existing: str) -> list[str]:
    """Generated secrets the template names and the existing file does not set at all."""
    present = {
        line.split("=", 1)[0].strip().removeprefix("export ").strip()
        for line in existing.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    return [key for key in secret_keys(template) if key not in present]


def main() -> int:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    target = ROOT / ".env"
    if target.exists():
        missing = missing_secrets(template, target.read_text(encoding="utf-8"))
        if not missing:
            sys.stderr.write(".env already exists; leaving it untouched.\n")
            return 0
        with target.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n# Added by `make init`: new in .env.example since this file was made.\n"
            )
            for key in missing:
                handle.write(f"{key}={secrets.token_urlsafe(24)}\n")
        sys.stdout.write(f".env kept as it was; added {', '.join(missing)}.\n")
        return 0
    # Created owner-only in one step: the secrets are never on disk with wider permissions.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(render(template))
    sys.stdout.write("Wrote .env with generated local secrets.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
