"""A stand-in for a language model in a load test.

It answers as the keyword rules do, after the time a model call takes. No model is called and
nothing leaves the platform. It exists so that the cost of a model (seconds a message, where
the rules take none) can be put on the orchestrator without paying for one: that cost is what
surge mode sheds. Its name and the model it records say what it is wherever a call is written
down, so that a run made with it is never taken for a run with a model.
"""

from __future__ import annotations

import asyncio

from skylink_care.llm.base import TriageReading, Usage
from skylink_care.llm.offline import read_with_rules


class SimulatedModelProvider:
    name = "simulated"

    def __init__(self, *, latency_seconds: float) -> None:
        self._latency_seconds = latency_seconds
        self.model = f"simulated-{latency_seconds:g}s"

    async def read_contact(
        self, masked_text: str, *, system_prompt: str = ""
    ) -> tuple[TriageReading, Usage]:
        await asyncio.sleep(self._latency_seconds)
        return read_with_rules(masked_text), Usage()
