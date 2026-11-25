"""What a live model was asked during an evaluation, and what came of it.

A run with a real model shows only what the model itself read. ``Tallied`` wraps the configured
provider and counts each reading the model made, each it could not make and why, and how long
the readings took. ``shortfall`` says why such a run has not shown the model throughout: every
reading the model was asked for must be its own, or the rules read part of the run in its place.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from skylink_care.llm.base import (
    GuideAnswer,
    GuideProvider,
    LLMProviderError,
    TriageProvider,
    TriageReading,
    Usage,
)


@dataclass
class ModelTally:
    """The readings of one run, over every attack or case that the model was asked to read."""

    model: str = ""
    answered: int = 0
    # The model that answered, as the provider reported it (a dated version of the model asked).
    served: Counter[str] = field(default_factory=Counter)
    # Why a reading fell to the rules instead: the provider's error kind, or "timeout".
    failed: Counter[str] = field(default_factory=Counter)
    # How long each answered reading took, in seconds.
    seconds: list[float] = field(default_factory=list)

    @property
    def fell(self) -> int:
        return sum(self.failed.values())

    def summary(self) -> str:
        """How many messages the model read itself, how fast, and how many the rules read."""
        line = (
            f"Readings by the model: {self.answered} of {self.answered + self.fell}"
            f" (served by {', '.join(sorted(self.served)) or 'none'}"
        )
        if self.seconds:
            line += (
                f"; {_seconds(statistics.median, self.seconds)} s median,"
                f" {_seconds(max, self.seconds)} s the slowest"
            )
        line += ")."
        if self.fell:
            line += f" Read by the rules because the model failed: {self.fell} ({self._why()})."
        return line

    def numbers(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "answered_by_the_model": self.answered,
            "served_by": dict(self.served),
            "read_by_the_rules_instead": self.fell,
            "why": dict(self.failed),
            "seconds_median": _seconds(statistics.median, self.seconds),
            "seconds_slowest": _seconds(max, self.seconds),
        }

    def shortfall(self, *, runs_for_the_model: int) -> str | None:
        """Why the run has not shown the model throughout; None when it has, or when no run
        was the model's to read."""
        if runs_for_the_model == 0:
            return None
        if self.answered == 0:
            return "the model read none of the messages; the rules read them all"
        if self.fell:
            return (
                f"{self.fell} of {self.answered + self.fell} readings fell to the rules because"
                f" the model failed ({self._why()})"
            )
        return None

    def _why(self) -> str:
        return ", ".join(f"{kind} {count}" for kind, count in self.failed.most_common())


class Tallied:
    """A provider, counted: each reading it made, and each it could not make. A question
    about guide sections is a reading like any other, where the provider can be asked one."""

    def __init__(self, provider: TriageProvider, tally: ModelTally) -> None:
        self._provider = provider
        self._tally = tally
        self.name = provider.name
        self.model = provider.model
        tally.model = provider.model
        if isinstance(provider, GuideProvider):
            # Only then is this a provider that can be asked: the gateway looks for the method.
            self.answer_from_guide = self._answer_from_guide

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        return await self._counted(
            self._provider.read_contact(masked_text, system_prompt=system_prompt)
        )

    async def _answer_from_guide(
        self, question: str, *, system_prompt: str
    ) -> tuple[GuideAnswer, Usage]:
        provider = self._provider
        if not isinstance(provider, GuideProvider):  # pragma: no cover - set only when it is
            raise LLMProviderError("not_supported")
        return await self._counted(
            provider.answer_from_guide(question, system_prompt=system_prompt)
        )

    async def _counted[Answer](
        self, asked: Awaitable[tuple[Answer, Usage]]
    ) -> tuple[Answer, Usage]:
        began = time.monotonic()
        try:
            answer, usage = await asked
        except LLMProviderError as error:
            self._tally.failed[error.kind] += 1
            raise
        except asyncio.CancelledError:
            # The gateway's time limit cut the call off.
            self._tally.failed["timeout"] += 1
            raise
        except Exception as error:
            self._tally.failed[f"unexpected:{type(error).__name__}"] += 1
            raise
        self._tally.answered += 1
        self._tally.served[usage.model or self.model] += 1
        self._tally.seconds.append(time.monotonic() - began)
        return answer, usage


def _seconds(measure: Callable[[list[float]], float], seconds: list[float]) -> float | None:
    """A measure of the readings' durations, to a tenth of a second; None without readings."""
    return round(measure(seconds), 1) if seconds else None
