"""What the platform asks of a language model, and how a provider reports back.

The model's job here is narrow: read what a customer wrote and describe it. It never decides
network status, eligibility or amounts, and it never sees unmasked personal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

CONFIDENCE_SCORE = {"high": 0.9, "medium": 0.6, "low": 0.3}


class TriageReading(BaseModel):
    """A structured reading of one customer message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: Literal["connectivity", "billing", "other"]
    confidence: Literal["high", "medium", "low"]
    wants_human: bool
    insists_device_fault: bool
    requests_technician: bool
    requests_credit: bool
    # The customer says the service works again. Only acted on after we said it was restored.
    confirms_working: bool
    abusive_or_off_topic: bool
    summary: str

    @property
    def confidence_score(self) -> float:
        return CONFIDENCE_SCORE[self.confidence]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # The model that produced the answer, when the provider reports it. It can differ from the
    # configured one (a server-side fallback answered).
    model: str | None = None


@dataclass(frozen=True)
class CallRecord:
    """Facts about one model call, kept for the audit trail and the cost dashboard."""

    provider: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    # True when the configured provider failed and the offline rules answered instead.
    degraded: bool = False
    error: str | None = None


class LLMProviderError(Exception):
    """A provider could not produce a usable answer. ``kind`` is a short machine-readable cause."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class TriageProvider(Protocol):
    name: str
    model: str

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]: ...
