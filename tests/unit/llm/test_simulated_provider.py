"""The stand-in for a language model in a load test: the rules' answer, at a model's pace."""

from __future__ import annotations

import time

from skylink_care.config import Settings
from skylink_care.llm.gateway import LOAD_SHED, build_gateway
from skylink_care.llm.offline import read_with_rules
from skylink_care.llm.simulated import SimulatedModelProvider

TEXT = "my wifi is down since morning"


def settings(**changed: object) -> Settings:
    return Settings(env="test", db_password="unused-by-tests", **changed)  # type: ignore[arg-type]


async def test_it_answers_as_the_rules_do_after_the_time_a_model_takes() -> None:
    provider = SimulatedModelProvider(latency_seconds=0.05)

    began = time.monotonic()
    reading, usage = await provider.read_contact(TEXT, system_prompt="")

    assert time.monotonic() - began >= 0.05
    assert reading == read_with_rules(TEXT)
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


def test_it_never_passes_for_a_model() -> None:
    """Its name and the model it records say what it is, wherever a call is written down."""
    provider = SimulatedModelProvider(latency_seconds=1.5)

    assert provider.name == "simulated"
    assert provider.model == "simulated-1.5s"


async def test_it_is_what_the_gateway_calls_when_it_is_configured() -> None:
    gateway = build_gateway(settings(llm_provider="simulated", llm_simulated_latency_seconds=0.02))

    _, record = await gateway.read_contact(TEXT)

    assert (record.provider, record.model, record.degraded) == (
        "simulated",
        "simulated-0.02s",
        False,
    )
    assert record.latency_ms >= 20


async def test_under_load_it_is_spared_like_a_model_and_the_turn_costs_what_the_rules_cost() -> (
    None
):
    gateway = build_gateway(settings(llm_provider="simulated", llm_simulated_latency_seconds=5.0))

    began = time.monotonic()
    _, record = await gateway.read_contact(TEXT, shed_load=True)

    assert time.monotonic() - began < 1.0
    assert (record.provider, record.degraded, record.error) == ("offline", True, LOAD_SHED)
