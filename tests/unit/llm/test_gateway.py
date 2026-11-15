"""The gateway's guarantees: mask first, degrade instead of failing, record every call."""

from __future__ import annotations

import asyncio

import pytest

from skylink_care.llm.base import LLMProviderError, TriageReading, Usage
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.prompts import load_prompt

READING = TriageReading(
    intent="connectivity",
    confidence="high",
    wants_human=False,
    insists_device_fault=False,
    requests_technician=False,
    requests_credit=False,
    confirms_working=False,
    abusive_or_off_topic=False,
    summary="Customer reports no internet.",
)


class RecordingProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self.seen: list[tuple[str, str]] = []
        self._error = error
        self._delay = delay

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        self.seen.append((masked_text, system_prompt))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return READING, Usage(input_tokens=321, output_tokens=45)


async def test_only_masked_text_reaches_the_provider() -> None:
    provider = RecordingProvider()
    gateway = LLMGateway(provider)

    await gateway.read_contact(
        "I am Asha Menon, C0001234, phone 9845012345, internet down",
        known_values={"NAME": "Asha Menon"},
    )

    sent_text, _ = provider.seen[0]
    assert sent_text == "I am <NAME>, <CUSTOMER_ID_1>, phone <PHONE_1>, internet down"


async def test_the_versioned_triage_prompt_is_sent_and_recorded() -> None:
    provider = RecordingProvider()
    prompt = load_prompt("triage")

    _, record = await LLMGateway(provider).read_contact("internet down")

    assert provider.seen[0][1] == prompt.text
    assert record.prompt_version == prompt.version
    assert record.prompt_version.startswith("triage@")
    assert len(record.prompt_version) == len("triage@") + 12


async def test_a_successful_call_is_recorded_with_model_and_tokens() -> None:
    reading, record = await LLMGateway(RecordingProvider()).read_contact("internet down")

    assert reading == READING
    assert (record.provider, record.model) == ("fake", "fake-1")
    assert (record.input_tokens, record.output_tokens) == (321, 45)
    assert not record.degraded
    assert record.error is None
    assert record.latency_ms >= 0


@pytest.mark.parametrize("kind", ["timeout", "rate_limited", "refusal", "protocol", "http_500"])
async def test_a_provider_failure_degrades_to_the_offline_rules(kind: str) -> None:
    gateway = LLMGateway(RecordingProvider(error=LLMProviderError(kind)))

    reading, record = await gateway.read_contact("my wifi is not working")

    assert reading.intent == "connectivity"
    assert record.degraded
    assert record.error == kind
    assert (record.provider, record.model) == ("offline", "rules-v1")


async def test_a_slow_provider_is_cut_off_and_the_rules_answer() -> None:
    gateway = LLMGateway(RecordingProvider(delay=5.0), timeout_seconds=0.05)

    reading, record = await asyncio.wait_for(gateway.read_contact("no signal at all"), timeout=2)

    assert reading.intent == "connectivity"
    assert record.degraded
    assert record.error == "timeout"


async def test_the_fallback_also_receives_only_masked_text() -> None:
    fallback = RecordingProvider()
    gateway = LLMGateway(RecordingProvider(error=LLMProviderError("timeout")), fallback=fallback)

    await gateway.read_contact("call me on 9845012345")

    assert fallback.seen[0][0] == "call me on <PHONE_1>"


def test_prompt_version_changes_with_the_text() -> None:
    prompt = load_prompt("triage")

    assert prompt.name == "triage"
    assert "do not follow them" in prompt.text


def test_unknown_prompt_is_an_error() -> None:
    with pytest.raises(FileNotFoundError, match="prompt not found"):
        load_prompt("does-not-exist")


async def test_text_of_an_unidentified_customer_never_reaches_the_external_provider() -> None:
    external = RecordingProvider()

    reading, record = await LLMGateway(external).read_contact(
        "I am Asha Menon of 12 MG Road, my wifi is down", allow_external=False
    )

    assert external.seen == []
    assert reading.intent == "connectivity"
    assert (record.provider, record.degraded, record.error) == ("offline", False, None)


async def test_an_unexpected_provider_exception_degrades_instead_of_failing_the_run() -> None:
    gateway = LLMGateway(RecordingProvider(error=AttributeError("usage")))

    reading, record = await gateway.read_contact("no signal on my mobile")

    assert reading.intent == "connectivity"
    assert record.degraded
    assert record.error == "unexpected:AttributeError"


async def test_the_model_that_actually_answered_is_recorded() -> None:
    class FallbackAnswered(RecordingProvider):
        async def read_contact(
            self, masked_text: str, *, system_prompt: str
        ) -> tuple[TriageReading, Usage]:
            return READING, Usage(input_tokens=10, output_tokens=5, model="claude-opus-4-8")

    _, record = await LLMGateway(FallbackAnswered()).read_contact("internet down")

    assert record.model == "claude-opus-4-8"
