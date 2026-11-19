"""OpenAI provider, through the official OpenAI SDK (Chat Completions).

The request is the Claude provider's in OpenAI's shape: the system prompt, the masked customer
text wrapped in tags so it reads as data, and a strict JSON-schema response format so the answer
always parses. ``store`` is false, so OpenAI keeps no copy for its dashboards or evals, and
nothing that tells customers apart (no ``user``, ``safety_identifier`` or ``metadata``) is sent
beside the masked text. Reasoning effort is ``low`` by default because this is classification.
Any failure is raised as ``LLMProviderError``; the gateway then answers from the offline rules,
so a model problem can slow the platform down but never stop it.
"""

from __future__ import annotations

from typing import Any

import openai
from pydantic import ValidationError

from skylink_care.llm.base import TRIAGE_SCHEMA, LLMProviderError, TriageReading, Usage
from skylink_care.llm.prompts import customer_message

DEFAULT_MAX_COMPLETION_TOKENS = 2048  # reasoning at low effort plus a small JSON answer
# The reasoning effort that leaves the parameter out, for a model without reasoning
# (gpt-4.1, gpt-4o): those reject it.
OMIT_REASONING = "omit"
SCHEMA_NAME = "triage_reading"


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        reasoning_effort: str = "low",
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        self._client = client
        self.model = model
        self._reasoning_effort = reasoning_effort
        self._max_completion_tokens = max_completion_tokens

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": customer_message(masked_text)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": SCHEMA_NAME, "schema": TRIAGE_SCHEMA, "strict": True},
            },
            "max_completion_tokens": self._max_completion_tokens,
            "store": False,
        }
        if self._reasoning_effort != OMIT_REASONING:
            request["reasoning_effort"] = self._reasoning_effort
        response = await self._send(request)
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMProviderError("protocol", "no choice in the response")
        choice = choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMProviderError("refusal")
        if choice.finish_reason == "content_filter":
            raise LLMProviderError("refusal", "content_filter")
        if choice.finish_reason == "length":
            raise LLMProviderError("truncated")
        text = getattr(choice.message, "content", None)
        if text is None:
            raise LLMProviderError("protocol", "no content in the response")
        try:
            reading = TriageReading.model_validate_json(text)
        except ValidationError as exc:
            raise LLMProviderError("protocol", str(exc)[:200]) from exc
        usage = getattr(response, "usage", None)
        return reading, Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=getattr(response, "model", None),
        )

    async def _send(self, request: dict[str, Any]) -> Any:
        # The request is assembled as a plain dict, so the SDK's overloads cannot be checked here;
        # the unit tests pin its exact shape instead.
        api: Any = self._client.chat.completions
        try:
            return await api.create(**request)
        except openai.APITimeoutError as exc:
            raise LLMProviderError("timeout") from exc
        except openai.APIConnectionError as exc:
            raise LLMProviderError("connection") from exc
        except openai.RateLimitError as exc:
            raise LLMProviderError("rate_limited") from exc
        except openai.AuthenticationError as exc:
            raise LLMProviderError("authentication") from exc
        except openai.BadRequestError as exc:
            raise LLMProviderError("bad_request", str(exc)[:200]) from exc
        except openai.APIStatusError as exc:
            raise LLMProviderError(f"http_{exc.status_code}") from exc
        except openai.APIError as exc:
            # Anything else the SDK raises, e.g. a response it could not validate.
            raise LLMProviderError("api_error", type(exc).__name__) from exc
