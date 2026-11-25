"""What the platform asks of a language model, and how a provider reports back.

The model's job here is narrow. It reads what a customer wrote and describes it; and, when the
guide sections for a customer's router have been found, it says which of them answers what
the customer describes. It never decides network status, eligibility or amounts, it writes
nothing a customer is sent, and it never sees unmasked personal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

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


class GuideAnswer(BaseModel):
    """What a model answers when it is shown a customer's words beside the guide sections
    found for them. Only the first field can change what the customer is sent, and only to
    another of the sections the model was shown; the other two are passages it copied, used
    only if they are found where it says they are (``guardrails.grounding``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The id of the section, of those shown, that answers what the customer describes.
    section_id: str
    # The customer's own description of the fault, copied from their message. May be empty.
    customer_words: str
    # The sentence of that section that says what it is for, copied from it. May be empty.
    guide_words: str


def _closed_schema(shape: type[BaseModel]) -> dict[str, Any]:
    """JSON schema of an answer in the form structured outputs expect: closed, and every
    field required. Both model providers send it as it is."""
    schema = shape.model_json_schema()
    properties = {
        name: {key: value for key, value in spec.items() if key != "title"}
        for name, spec in schema["properties"].items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TRIAGE_SCHEMA = _closed_schema(TriageReading)
GUIDE_SCHEMA = _closed_schema(GuideAnswer)


@dataclass(frozen=True)
class GuideSection:
    """One section of a router guide, as a model is shown it."""

    section_id: str
    title: str
    body: str


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


def parse_answer[Shape: BaseModel](shape: type[Shape], text: str) -> Shape:
    """A model's answer in the shape that was asked for, or a ``protocol`` error."""
    try:
        return shape.model_validate_json(text)
    except ValidationError as exc:
        raise LLMProviderError("protocol", str(exc)[:200]) from exc


class TriageProvider(Protocol):
    name: str
    model: str

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]: ...


@runtime_checkable
class GuideProvider(Protocol):
    """A provider that can also be asked which guide section answers a customer. The keyword
    rules are not one: they choose a section inside the search itself (``rag.symptoms``)."""

    name: str
    model: str

    async def answer_from_guide(
        self, question: str, *, system_prompt: str
    ) -> tuple[GuideAnswer, Usage]: ...
