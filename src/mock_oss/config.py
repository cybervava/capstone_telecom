"""Settings, read from the environment only. There are no default API keys anywhere."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

CLIENT_ENV_VARS: Mapping[str, str] = {
    "triage": "OSS_KEY_TRIAGE",
    "diagnostics": "OSS_KEY_DIAGNOSTICS",
    "resolution": "OSS_KEY_RESOLUTION",
    "dispatch": "OSS_KEY_DISPATCH",
    "notification": "OSS_KEY_NOTIFICATION",
    "ingest": "OSS_KEY_INGEST",
    "executor": "OSS_KEY_EXECUTOR",
    "admin": "OSS_KEY_ADMIN",
}

DEFAULT_DB = "var/mock_oss.sqlite"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8081
DEFAULT_LINE_TEST_DELAY = "300-1500"
_DELAY_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_delay(raw: str) -> tuple[int, int]:
    """Parse ``MOCK_OSS_LINE_TEST_DELAY_MS``: ``"0"``, ``"500"`` or ``"300-1500"``."""
    match = _DELAY_RE.match(raw.strip())
    if match is None:
        raise ValueError(f"MOCK_OSS_LINE_TEST_DELAY_MS must be N or MIN-MAX, got {raw!r}")
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) is not None else low
    if high < low:
        raise ValueError(f"MOCK_OSS_LINE_TEST_DELAY_MS range is inverted: {raw!r}")
    return low, high


def _parse_port(raw: str) -> int:
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise ValueError(f"MOCK_OSS_PORT must be an integer in 1..65535, got {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    line_test_delay_ms: tuple[int, int] = (300, 1500)
    api_keys: Mapping[str, str] = field(default_factory=dict)
    """client name -> key; clients whose env var is unset or empty are absent."""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        keys = {
            client: env[var] for client, var in CLIENT_ENV_VARS.items() if env.get(var, "") != ""
        }
        return cls(
            db_path=Path(env.get("MOCK_OSS_DB", DEFAULT_DB)),
            host=env.get("MOCK_OSS_HOST", DEFAULT_HOST),
            port=_parse_port(env.get("MOCK_OSS_PORT", str(DEFAULT_PORT))),
            line_test_delay_ms=parse_delay(
                env.get("MOCK_OSS_LINE_TEST_DELAY_MS", DEFAULT_LINE_TEST_DELAY)
            ),
            api_keys=keys,
        )
