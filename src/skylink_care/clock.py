"""The clock every process reads. It is passed in wherever time matters, so tests can move it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

Clock = Callable[[], datetime]
# The zone customers are told times in and their billing cycles are counted in.
IST = ZoneInfo("Asia/Kolkata")


def utc_now() -> datetime:
    return datetime.now(UTC)
