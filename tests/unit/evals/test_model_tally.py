"""The count of a live model's readings, which decides whether a run with it shows anything."""

from __future__ import annotations

import asyncio
from collections import Counter

from evals.model_tally import ModelTally, Tallied

from skylink_care.llm.base import LLMProviderError, TriageReading, Usage
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.offline import read_with_rules

TEXT = "my wifi is down since morning"


class Model:
    """A stand-in model that answers as told: with a reading, an error, or not in time."""

    name = "openai"
    model = "gpt-test"

    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self._error = error
        self._delay = delay

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return read_with_rules(masked_text), Usage(1, 1, model="gpt-test-2026-03-17")


async def test_a_reading_the_model_made_is_counted_with_the_model_that_served_it() -> None:
    tally = ModelTally()

    await LLMGateway(Tallied(Model(), tally)).read_contact(TEXT)

    assert (tally.model, tally.answered, tally.fell) == ("gpt-test", 1, 0)
    assert tally.served == {"gpt-test-2026-03-17": 1}
    assert len(tally.seconds) == 1


async def test_a_failed_reading_is_counted_by_its_cause_and_the_rules_answer() -> None:
    tally = ModelTally()

    _, record = await LLMGateway(
        Tallied(Model(error=LLMProviderError("rate_limited")), tally)
    ).read_contact(TEXT)

    assert record.degraded
    assert (tally.answered, tally.failed) == (0, {"rate_limited": 1})


async def test_a_reading_cut_off_by_the_gateway_is_counted_as_a_timeout() -> None:
    tally = ModelTally()

    _, record = await LLMGateway(
        Tallied(Model(delay=1.0), tally), timeout_seconds=0.05
    ).read_contact(TEXT)

    assert (record.degraded, record.error) == (True, "timeout")
    assert (tally.answered, tally.failed) == (0, {"timeout": 1})


async def test_an_unexpected_error_is_counted_apart() -> None:
    tally = ModelTally()

    await LLMGateway(Tallied(Model(error=KeyError("x")), tally)).read_contact(TEXT)

    assert tally.failed == {"unexpected:KeyError": 1}


def test_a_run_shows_the_model_only_if_every_reading_was_its_own() -> None:
    every = ModelTally(answered=24)
    most = ModelTally(answered=23, failed=Counter({"rate_limited": 1}))
    none = ModelTally(failed=Counter({"authentication": 24}))

    assert every.shortfall(runs_for_the_model=18) is None
    assert most.shortfall(runs_for_the_model=18) == (
        "1 of 24 readings fell to the rules because the model failed (rate_limited 1)"
    )
    assert none.shortfall(runs_for_the_model=18) == (
        "the model read none of the messages; the rules read them all"
    )
    # A run with nothing for the model to read asked nothing of it.
    assert ModelTally().shortfall(runs_for_the_model=0) is None


def test_a_run_for_the_model_in_which_it_was_never_asked_shows_nothing() -> None:
    assert ModelTally().shortfall(runs_for_the_model=3) == (
        "the model read none of the messages; the rules read them all"
    )


def test_the_summary_names_the_counts_the_pace_and_the_causes() -> None:
    tally = ModelTally(model="gpt-test", answered=3, seconds=[1.0, 1.2, 2.5])
    tally.served["gpt-test-2026-03-17"] = 3
    tally.failed["timeout"] = 1

    assert tally.summary() == (
        "Readings by the model: 3 of 4 (served by gpt-test-2026-03-17; 1.2 s median, 2.5 s the"
        " slowest). Read by the rules because the model failed: 1 (timeout 1)."
    )
    assert tally.numbers() == {
        "model": "gpt-test",
        "answered_by_the_model": 3,
        "served_by": {"gpt-test-2026-03-17": 3},
        "read_by_the_rules_instead": 1,
        "why": {"timeout": 1},
        "seconds_median": 1.2,
        "seconds_slowest": 2.5,
    }


def test_without_readings_there_is_no_pace_to_report() -> None:
    tally = ModelTally()

    assert tally.summary() == "Readings by the model: 0 of 0 (served by none)."
    assert (tally.numbers()["seconds_median"], tally.numbers()["seconds_slowest"]) == (None, None)
