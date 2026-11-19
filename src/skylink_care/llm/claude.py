"""Claude provider, through the official Anthropic SDK.

One request shape is used: a short system prompt, the masked customer text wrapped in tags so it
reads as data, and a JSON-schema output format so the answer always parses. Effort is ``low``
because this is classification. Any failure is raised as ``LLMProviderError``; the gateway then
answers from the offline rules, so a model problem can slow the platform down but never stop it.
"""

from __future__ import annotations

from typing import Any

import anthropic
from pydantic import ValidationError

from skylink_care.llm.base import TRIAGE_SCHEMA, LLMProviderError, TriageReading, Usage
from skylink_care.llm.prompts import customer_message

__all__ = ["DEFAULT_MAX_TOKENS", "REFUSAL_FALLBACK_BETA", "TRIAGE_SCHEMA", "ClaudeProvider"]

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 2048  # covers adaptive thinking at low effort plus a small JSON answer


class ClaudeProvider:
    name = "anthropic"

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str,
        refusal_fallback: bool = True,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = client
        self.model = model
        self._refusal_fallback = refusal_fallback
        self._max_tokens = max_tokens

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": customer_message(masked_text)}],
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA},
            },
        }
        response = await self._send(request)
        if response.stop_reason == "refusal":
            raise LLMProviderError("refusal")
        if response.stop_reason == "max_tokens":
            raise LLMProviderError("truncated")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise LLMProviderError("protocol", "no text block in the response")
        try:
            reading = TriageReading.model_validate_json(text)
        except ValidationError as exc:
            raise LLMProviderError("protocol", str(exc)[:200]) from exc
        usage = getattr(response, "usage", None)
        return reading, Usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=getattr(response, "model", None),
        )

    async def _send(self, request: dict[str, Any]) -> Any:
        # The request is assembled as a plain dict, so the SDK's overloads cannot be checked here;
        # the unit tests pin its exact shape instead.
        api: Any
        if self._refusal_fallback:
            # If a safety classifier declines the request, the API re-runs it on a fallback model
            # inside the same call instead of returning a refusal.
            api = self._client.beta.messages
            request = {**request, "betas": [REFUSAL_FALLBACK_BETA], "fallbacks": "default"}
        else:
            api = self._client.messages
        try:
            return await api.create(**request)
        except anthropic.APITimeoutError as exc:
            raise LLMProviderError("timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError("connection") from exc
        except anthropic.RateLimitError as exc:
            raise LLMProviderError("rate_limited") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMProviderError("authentication") from exc
        except anthropic.BadRequestError as exc:
            raise LLMProviderError("bad_request", str(exc)[:200]) from exc
        except anthropic.APIStatusError as exc:
            raise LLMProviderError(f"http_{exc.status_code}") from exc
        except anthropic.APIError as exc:
            # Anything else the SDK raises, e.g. a response it could not validate.
            raise LLMProviderError("api_error", type(exc).__name__) from exc
