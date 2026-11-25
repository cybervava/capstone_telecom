"""OpenAI provider, through the official OpenAI SDK (Chat Completions).

The request is the Claude provider's in OpenAI's shape: the system prompt, the masked customer
text wrapped in tags so it reads as data, and a strict JSON-schema response format so the answer
always parses. Two things are asked this way: what a message says (``read_contact``), and
which of the guide sections found for a customer answers them (``answer_from_guide``).
``store`` is false, so OpenAI keeps no copy for its dashboards or evals, and nothing that tells
customers apart (no ``user``, ``safety_identifier`` or ``metadata``) is sent beside the masked
text. Reasoning effort is ``low`` by default because both are classification. Any failure is
raised as ``LLMProviderError``; the gateway then answers from the offline rules, so a model
problem can slow the platform down but never stop it.
"""

from __future__ import annotations

from typing import Any

import openai

from skylink_care.llm.base import (
    GUIDE_SCHEMA,
    TRIAGE_SCHEMA,
    GuideAnswer,
    LLMProviderError,
    TriageReading,
    Usage,
    parse_answer,
)
from skylink_care.llm.prompts import customer_message

DEFAULT_MAX_COMPLETION_TOKENS = 2048  # reasoning at low effort plus a small JSON answer
# The reasoning effort that leaves the parameter out, for a model without reasoning
# (gpt-4.1, gpt-4o): those reject it.
OMIT_REASONING = "omit"
SCHEMA_NAME = "triage_reading"
GUIDE_SCHEMA_NAME = "guide_answer"


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
        text, usage = await self._ask(
            system_prompt, customer_message(masked_text), SCHEMA_NAME, TRIAGE_SCHEMA
        )
        return parse_answer(TriageReading, text), usage

    async def answer_from_guide(
        self, question: str, *, system_prompt: str
    ) -> tuple[GuideAnswer, Usage]:
        """``question`` is the masked customer text and the sections found, already wrapped
        as data (``prompts.guide_question``)."""
        text, usage = await self._ask(system_prompt, question, GUIDE_SCHEMA_NAME, GUIDE_SCHEMA)
        return parse_answer(GuideAnswer, text), usage

    async def _ask(
        self, system_prompt: str, content: str, schema_name: str, schema: dict[str, Any]
    ) -> tuple[str, Usage]:
        """One question, answered in one closed shape. Returns the answer's text."""
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
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
        usage = getattr(response, "usage", None)
        return str(text), Usage(
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
