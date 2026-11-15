"""The one door through which the platform reaches a language model.

The gateway, not its callers, guarantees three things: text is masked before it leaves, a slow or
failing model is replaced by the offline rules within a fixed time, and every call leaves a
record of provider, model, prompt version and token counts.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

import structlog

from skylink_care.config import Settings
from skylink_care.guardrails.pii import mask_pii
from skylink_care.llm.base import (
    CallRecord,
    LLMProviderError,
    TriageProvider,
    TriageReading,
    Usage,
)
from skylink_care.llm.offline import OfflineProvider
from skylink_care.llm.prompts import load_prompt

log = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 8.0


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
        correlation_id: str | None = None,
    ) -> tuple[TriageReading, CallRecord]:
        """Describe what the customer is asking for. Never raises: it degrades to the rules.

        ``allow_external`` is False while the customer is not identified. Without the customer's
        profile there is no name to mask, so the text is read by the offline rules and does not
        leave the platform. That is a routing choice, not a degradation.
        """
        prompt = load_prompt("triage")
        masked = mask_pii(text, known_values=known_values)
        started = time.monotonic()
        if not allow_external:
            reading, usage = await self._fallback.read_contact(
                masked.text, system_prompt=prompt.text
            )
            return reading, self._record(self._fallback, prompt.version, usage, started)
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
            reading, usage = await self._fallback.read_contact(
                masked.text, system_prompt=prompt.text
            )
            return reading, self._record(
                self._fallback, prompt.version, usage, started, degraded=True, error=error
            )
        return reading, self._record(self._primary, prompt.version, usage, started)

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
    if settings.llm_provider == "offline":
        return LLMGateway(OfflineProvider())
    # Imported here so that offline deployments never load the SDK.
    import anthropic

    from skylink_care.llm.claude import ClaudeProvider

    # Credentials are resolved by the SDK (ANTHROPIC_API_KEY or an `ant auth login` profile).
    client = anthropic.AsyncAnthropic(timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=1)
    return LLMGateway(ClaudeProvider(client, model=settings.llm_model))
