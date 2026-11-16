"""The verdict of a load run: computed from what the callers saw, and from nothing else."""

from __future__ import annotations

import pytest
from loadtest.outcome import Offered, Targets, judge, percentile, summarise

TARGETS = Targets()


def accepted(seconds: float, **more: object) -> Offered:
    fields: dict[str, object] = {
        "due": 0.0,
        "route": "web",
        "affected": True,
        "accepted": True,
        "reference_seconds": seconds,
        "attempts": 1,
    }
    return Offered(**{**fields, **more})  # type: ignore[arg-type]


def lost(error: str = "ConnectError", **more: object) -> Offered:
    fields: dict[str, object] = {
        "due": 0.0,
        "route": "web",
        "affected": True,
        "attempts": 9,
        "error": error,
    }
    return Offered(**{**fields, **more})  # type: ignore[arg-type]


def verdicts(offered: list[Offered]) -> list[bool]:
    return [criterion.met for criterion in judge(offered, TARGETS)]


def test_every_contact_accepted_quickly_and_nobody_limited_meets_all_three_targets() -> None:
    assert verdicts([accepted(0.02)] * 1000) == [True, True, True]


def test_five_lost_in_a_thousand_is_the_most_the_availability_target_allows() -> None:
    at_the_limit = [accepted(0.02)] * 995 + [lost()] * 5
    one_more = [accepted(0.02)] * 994 + [lost()] * 6

    assert verdicts(at_the_limit)[0] is True
    assert verdicts(one_more)[0] is False


def test_one_reference_later_than_two_minutes_fails_the_reference_target() -> None:
    assert verdicts([accepted(0.02)] * 999 + [accepted(120.0)])[1] is True
    assert verdicts([accepted(0.02)] * 999 + [accepted(120.01)])[1] is False


def test_a_reference_that_came_late_counts_against_both_targets() -> None:
    """Not accepted, as the caller stood without it when the time was up; and the slowest
    reference of the run, whether or not it was in time."""
    late = lost("reference after the time allowed", reference_seconds=127.0)

    criteria = judge([accepted(0.02)] * 999 + [late], TARGETS)

    assert criteria[0].measured == "99.90% (999 of 1,000)"
    assert (criteria[1].measured, criteria[1].met) == ("127.00 s", False)


def test_one_first_contact_turned_away_by_a_rate_limit_fails_its_target() -> None:
    offered = [accepted(0.02)] * 999 + [lost("HTTP 429", rate_limited=True)]

    assert verdicts(offered) == [True, True, False]


def test_a_run_that_offered_nothing_proves_nothing() -> None:
    assert verdicts([])[:2] == [False, False]


def test_the_measured_figures_are_given_in_words_a_reader_can_check() -> None:
    criteria = judge([accepted(0.5)] * 199 + [lost()], TARGETS)

    assert criteria[0].measured == "99.50% (199 of 200)"
    assert criteria[0].target == "at least 99.5%"
    assert criteria[1].measured == "0.50 s"
    assert criteria[2].measured == "0"


@pytest.mark.parametrize(
    ("share", "expected"), [(0.5, 5.0), (0.95, 10.0), (0.9, 9.0), (1.0, 10.0), (0.01, 1.0)]
)
def test_a_percentile_is_a_value_that_was_measured(share: float, expected: float) -> None:
    values = [float(n) for n in range(10, 0, -1)]

    assert percentile(values, share) == expected


def test_there_is_no_percentile_of_nothing() -> None:
    assert percentile([], 0.5) is None


def test_the_summary_counts_what_the_strict_reading_of_the_rule_counts() -> None:
    """Nobody may be left without a reference after two minutes: the lost and the late."""
    offered = [
        accepted(0.02, told_delayed=True, reply_seconds=1.5, reply_template="outage_active.v1"),
        accepted(0.04, route="channel", attempts=3),
        accepted(121.0),
        lost(),
        lost("HTTP 429", rate_limited=True, affected=False),
    ]

    summary = summarise(offered, TARGETS)

    assert summary["offered"] == 5
    assert summary["accepted"] == 3
    assert summary["without_reference_in_time"] == 3
    assert summary["rate_limited"] == 1
    assert summary["sent_more_than_once"] == 3  # whether or not it helped
    assert summary["told_delayed"] == 1
    assert summary["by_route"] == {"channel": 1, "web": 4}
    assert summary["affected"] == 4
    assert summary["reference_seconds"]["max"] == 121.0
    assert (summary["stayed_for_the_reply"], summary["saw_a_reply"]) == (2, 1)
    assert summary["reply_seconds"]["p50"] == 1.5
    assert summary["not_accepted_because"] == {"ConnectError": 1, "HTTP 429": 1}
