"""Injectable time source. Every "now" and every simulated wait goes through a Clock."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

IST_OFFSET = timedelta(hours=5, minutes=30)


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ManualClock:
    """Test clock: time moves only when advanced; ``sleep`` advances it instantly."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock needs a timezone-aware start")
        self._now = start.astimezone(UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)


def iso(moment: datetime) -> str:
    """UTC ISO 8601 with ``Z``, second precision (the wire and storage format)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    """Parse an aware ISO 8601 timestamp (``Z`` or offset) into UTC."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"timestamp without offset: {text!r}")
    return moment.astimezone(UTC)


def ist_date(moment: datetime) -> date:
    return (moment.astimezone(UTC) + IST_OFFSET).date()
