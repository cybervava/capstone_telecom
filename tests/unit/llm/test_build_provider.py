"""Which provider reads the messages, as configured, and what it needs to start."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from skylink_care.config import MissingSecretError, Settings
from skylink_care.llm.claude import ClaudeProvider
from skylink_care.llm.gateway import DEFAULT_TIMEOUT_SECONDS, build_provider
from skylink_care.llm.offline import OfflineProvider
from skylink_care.llm.openai_provider import OpenAIProvider


def settings(**changed: object) -> Settings:
    return Settings(env="test", db_password="unused-by-tests", **changed)  # type: ignore[arg-type]


def test_the_rules_read_by_default() -> None:
    assert isinstance(build_provider(settings()), OfflineProvider)


def test_claude_reads_with_the_claude_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    provider = build_provider(settings(llm_provider="anthropic", llm_model="claude-test"))

    assert isinstance(provider, ClaudeProvider)
    assert (provider.name, provider.model) == ("anthropic", "claude-test")


def test_openai_reads_with_the_openai_model_and_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    provider = build_provider(
        settings(
            llm_provider="openai",
            llm_model="claude-opus-5-5",
            llm_openai_model="gpt-test",
            llm_openai_reasoning_effort="omit",
        )
    )

    assert isinstance(provider, OpenAIProvider)
    # The Claude model setting is not the OpenAI one: switching provider is one variable.
    assert (provider.name, provider.model) == ("openai", "gpt-test")
    assert provider._reasoning_effort == "omit"
    # The SDK gives up within the gateway's own limit and tries once more at most.
    assert provider._client.timeout == DEFAULT_TIMEOUT_SECONDS
    assert provider._client.max_retries == 1


@pytest.mark.parametrize("key", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_openai_without_a_key_stops_the_process_at_start(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)

    with pytest.raises(MissingSecretError, match="OPENAI_API_KEY"):
        build_provider(settings(llm_provider="openai"))


def test_an_admin_key_does_not_stand_in_for_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK would start with it, and every call would then be refused."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "admin-key-not-real")

    with pytest.raises(MissingSecretError, match="OPENAI_API_KEY"):
        build_provider(settings(llm_provider="openai"))


def test_the_key_given_to_the_sdk_is_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", " test-key-not-real ")

    provider = build_provider(settings(llm_provider="openai"))

    assert isinstance(provider, OpenAIProvider)
    assert provider._client.api_key == "test-key-not-real"


def test_the_default_openai_model_is_the_dated_snapshot_that_was_evaluated() -> None:
    configured = settings()

    # The snapshot that served the attack suite's live run (evals/reports/attack_suite_openai.md),
    # not an alias that can move to another model without notice (decision D2).
    assert configured.llm_openai_model == "gpt-5.4-mini-2026-03-17"
    assert re.search(r"-\d{4}-\d{2}-\d{2}$", configured.llm_openai_model)
    assert configured.llm_openai_reasoning_effort == "low"


def test_an_unknown_reasoning_effort_is_refused() -> None:
    with pytest.raises(ValidationError):
        settings(llm_openai_reasoning_effort="extreme")
