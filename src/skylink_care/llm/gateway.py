"""The one door through which the platform reaches a language model.

The gateway, not its callers, guarantees three things: text is masked before it leaves, a slow or
failing model is replaced by the offline rules within a fixed time, and every call leaves a
record of provider, model, prompt version and token counts.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping

import structlog

from skylink_care.config import MissingSecretError, Settings
from skylink_care.guardrails.pii import mask_pii
from skylink_care.llm.base import (
    CallRecord,
    LLMProviderError,
    TriageProvider,
    TriageReading,
    Usage,
)
from skylink_care.llm.offline import OfflineProvider
from skylink_care.llm.prompts import Prompt, load_prompt

log = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 8.0
# Recorded on a call the model was spared because the platform was behind.
LOAD_SHED = "load_shed"


class LLMGateway:
    def __init__(
        self,
        primary: TriageProvider,
        *,
        fallback: TriageProvider | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or OfflineProvider()
        self._timeout_seconds = timeout_seconds

    async def read_contact(
        self,
        text: str,
        *,
        known_values: Mapping[str, str] | None = None,
        allow_external: bool = True,
        shed_load: bool = False,
        correlation_id: str | None = None,
    ) -> tuple[TriageReading, CallRecord]:
        """Describe what the customer is asking for. Never raises: it degrades to the rules.

        ``allow_external`` is False while the customer is not identified. Without the customer's
        profile there is no name to mask, so the text is read by the offline rules and does not
        leave the platform. That is a routing choice, not a degradation.

        ``shed_load`` is True while the orchestrator is behind. The rules then read the message
        and the model is not called, so that a turn costs what the rules cost. That is recorded
        as a degraded reading: a request the rules cannot place is read by a person, as it is
        when the model fails, and not refused as out of scope.
        """
        prompt = load_prompt("triage")
        masked = mask_pii(text, known_values=known_values)
        started = time.monotonic()
        if not allow_external:
            return await self._by_the_rules(masked.text, prompt, started)
        if shed_load and self._primary.name != self._fallback.name:
            return await self._by_the_rules(masked.text, prompt, started, lost=LOAD_SHED)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                reading, usage = await self._primary.read_contact(
                    masked.text, system_prompt=prompt.text
                )
        except Exception as exc:
            # A provider problem of any kind must slow the platform down, never stop it. The
            # failure is recorded on the call, logged with the contact's correlation id, and
            # carried into routing as ``degraded``.
            if isinstance(exc, LLMProviderError):
                error = exc.kind
            elif isinstance(exc, TimeoutError):
                error = "timeout"
            else:
                error = f"unexpected:{type(exc).__name__}"
            log.warning(
                "llm_call_degraded",
                provider=self._primary.name,
                error=error,
                correlation_id=correlation_id,
            )
            return await self._by_the_rules(masked.text, prompt, started, lost=error)
        return reading, self._record(self._primary, prompt.version, usage, started)

    async def _by_the_rules(
        self, masked_text: str, prompt: Prompt, started: float, *, lost: str | None = None
    ) -> tuple[TriageReading, CallRecord]:
        """Read with the offline rules. ``lost`` says why the model did not read it, when it
        would have: the call is then recorded as degraded."""
        reading, usage = await self._fallback.read_contact(masked_text, system_prompt=prompt.text)
        record = self._record(
            self._fallback, prompt.version, usage, started, degraded=lost is not None, error=lost
        )
        return reading, record

    @staticmethod
    def _record(
        provider: TriageProvider,
        prompt_version: str,
        usage: Usage,
        started: float,
        *,
        degraded: bool = False,
        error: str | None = None,
    ) -> CallRecord:
        return CallRecord(
            provider=provider.name,
            model=usage.model or provider.model,
            prompt_version=prompt_version,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            degraded=degraded,
            error=error,
        )


def build_gateway(settings: Settings) -> LLMGateway:
    """Construct the gateway for the configured provider."""
    provider = build_provider(settings)
    if settings.llm_provider == "simulated":
        # The stand-in's own pace must not be cut off as a model that hangs would be.
        return LLMGateway(
            provider,
            timeout_seconds=max(
                DEFAULT_TIMEOUT_SECONDS, settings.llm_simulated_latency_seconds * 2
            ),
        )
    return LLMGateway(provider)


def build_provider(settings: Settings) -> TriageProvider:
    """The configured provider. A model's SDK is imported only when that model is configured,
    so that offline deployments never load one."""
    if settings.llm_provider == "offline":
        return OfflineProvider()
    if settings.llm_provider == "simulated":
        from skylink_care.llm.simulated import SimulatedModelProvider

        return SimulatedModelProvider(latency_seconds=settings.llm_simulated_latency_seconds)
    if settings.llm_provider == "openai":
        return _openai(settings)
    return _claude(settings)


def _openai(settings: Settings) -> TriageProvider:
    import openai

    from skylink_care.llm.openai_provider import OpenAIProvider

    # Read here, not left to the SDK: it would also start with an admin key or a workload
    # identity in the environment, and every call would then fail instead of the start.
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise MissingSecretError("OPENAI_API_KEY is not set")
    client = openai.AsyncOpenAI(api_key=key, timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=1)
    return OpenAIProvider(
        client,
        model=settings.llm_openai_model,
        reasoning_effort=settings.llm_openai_reasoning_effort,
    )


def _claude(settings: Settings) -> TriageProvider:
    import anthropic

    from skylink_care.llm.claude import ClaudeProvider

    # Credentials are resolved by the SDK (ANTHROPIC_API_KEY or an `ant auth login` profile).
    client = anthropic.AsyncAnthropic(timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=1)
    return ClaudeProvider(client, model=settings.llm_model)
