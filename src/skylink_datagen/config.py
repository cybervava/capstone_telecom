"""Shared constants, run configuration, seeded RNG derivation and formatting helpers."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Changes whenever a file of the pack changes its columns: 1.1.0 names the monthly fee
# monthly_fee_usd (amounts are in US dollars). A pack is read only by the version that wrote
# it; the container set-up keeps one pack per version (scripts/container_setup.sh).
GENERATOR_VERSION = "1.1.0"
IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_START = datetime(2026, 5, 1, 0, 0, 0, tzinfo=IST)
WINDOW_END = datetime(2026, 10, 31, 23, 59, 59, tzinfo=IST)
WINDOW_DAYS = (WINDOW_END.date() - WINDOW_START.date()).days + 1
MAP_VERSION = "2026-11-01"
FULL_SCALE_CUSTOMERS = 400_000
MIN_CUSTOMERS = 1_000
MAX_CUSTOMERS = 5_000_000
MAX_DEFECT_RATE = 0.25


class ConfigError(ValueError):
    """Raised when a run configuration violates the contract's bounds."""


@dataclass(frozen=True)
class DefectRates:
    missing: float = 0.015
    ambiguous: float = 0.010
    stale: float = 0.007

    def __post_init__(self) -> None:
        for name in ("missing", "ambiguous", "stale"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= MAX_DEFECT_RATE:
                msg = f"{name} rate must be between 0 and {MAX_DEFECT_RATE}, got {value}"
                raise ConfigError(msg)

    def as_dict(self) -> dict[str, float]:
        return {"missing": self.missing, "ambiguous": self.ambiguous, "stale": self.stale}


@dataclass(frozen=True)
class GenerateConfig:
    out: Path
    customers: int
    seed: int
    rates: DefectRates

    def __post_init__(self) -> None:
        if not MIN_CUSTOMERS <= self.customers <= MAX_CUSTOMERS:
            msg = f"--customers must be between {MIN_CUSTOMERS} and {MAX_CUSTOMERS}"
            raise ConfigError(f"{msg}, got {self.customers}")


def derive_rng(seed: int, stage: str) -> random.Random:
    """Independent, reproducible RNG per stage; never depends on hash() or wall-clock."""
    digest = hashlib.sha256(f"{seed}:{stage}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def scaled(full_scale_value: float, customers: int, minimum: int) -> int:
    return max(minimum, round(full_scale_value * customers / FULL_SCALE_CUSTOMERS))


def fmt_ts(moment: datetime) -> str:
    return moment.astimezone(IST).isoformat(timespec="seconds")


def fmt_bool(value: bool) -> str:
    return "true" if value else "false"


def random_moment(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(span, 0)))


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))
