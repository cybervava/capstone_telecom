"""The rule for failing lines, held to what it is for on simulated waves of line tests.

After an outage the platform tests every waiting line on a node, one after another, and the
rule is asked after each. On a healthy node a few of a hundred lines fail for reasons of their
own, and the node must almost never be suspected for them. On a node part of which did not
come back, it nearly always must be.

The load test of 16 November found the first of these broken: a count of three failing lines
suspected one restored node in seven. Each wave here is a hundred lines, failing by themselves
at the rate the synthetic pack has (one in about seventy); the settings assume one in fifty.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from skylink_care.domain.models import MappingStatus, NodeMapping
from skylink_care.domain.network import (
    DecisionLimits,
    DecisionReason,
    FeedView,
    LiveStatus,
    decide_network_state,
)

NOW = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
NODE = "BLR-OLT-0012"
MAPPED = NodeMapping(MappingStatus.RESOLVED, NODE, (NODE,), "2026-11-01")
LIVE_CLEAR = LiveStatus.operational(observed_at=NOW - timedelta(seconds=5))
FEED_QUIET = FeedView(heartbeat_at=NOW - timedelta(seconds=10), active_outage=None)
AS_SET = DecisionLimits(
    status_max_age=timedelta(seconds=60),
    feed_max_age=timedelta(seconds=90),
    line_test_cluster_threshold=3,
    line_own_fault_rate=0.02,
    line_test_cluster_chance=0.001,
)
LINES = 100
OWN_FAULTS = 0.0136  # what the load test's wave found in the synthetic pack: 133 of 9,773 lines
SEED = 20261116

Rule = Callable[[int, int], bool]


def rule_with(limits: DecisionLimits) -> Rule:
    """The platform's decision as a yes or no on the lines alone: live status clear, no alarm."""
    answers: dict[tuple[int, int], bool] = {}

    def suspect(failing: int, passing: int) -> bool:
        if (failing, passing) not in answers:
            decision = decide_network_state(
                mapping=MAPPED,
                live=LIVE_CLEAR,
                feed=FEED_QUIET,
                now=NOW,
                limits=limits,
                line_test_failures_on_node=failing,
                line_tests_passing_on_node=passing,
            )
            answers[failing, passing] = decision.reason is DecisionReason.SUSPECTED_NODE_FAULT
        return answers[failing, passing]

    return suspect


def suspected_in(waves: int, rule: Rule, *, still_down: float = 0.0) -> int:
    """How many of these waves made the node suspect at some point. ``still_down`` is the share
    of the node's lines that did not come back, on top of the faults lines have anyway."""
    rng = random.Random(SEED)
    fails = OWN_FAULTS + still_down * (1 - OWN_FAULTS)
    count = 0
    for _ in range(waves):
        failing = passing = 0
        for _ in range(LINES):
            if rng.random() < fails:
                failing += 1
            else:
                passing += 1
            if rule(failing, passing):
                count += 1
                break
    return count


def test_a_healthy_restored_node_is_almost_never_suspected() -> None:
    waves = 20_000

    suspected = suspected_in(waves, rule_with(AS_SET))

    assert suspected <= waves * 2 / 1000  # measured: 0.8 in a thousand


def test_a_node_with_a_tenth_of_its_lines_still_down_is_usually_caught() -> None:
    waves = 5_000

    caught = suspected_in(waves, rule_with(AS_SET), still_down=0.10)

    assert caught >= waves * 85 / 100  # measured: about 90 in a hundred


def test_a_node_with_a_fifth_of_its_lines_still_down_is_caught() -> None:
    waves = 5_000

    caught = suspected_in(waves, rule_with(AS_SET), still_down=0.20)

    assert caught >= waves * 995 / 1000


def test_the_count_alone_suspects_a_healthy_node_far_too_often() -> None:
    """What the load test found. With the chance set so that anything is 'more than
    coincidence', the rule is the count of three it used to be."""
    count_alone = DecisionLimits(
        status_max_age=AS_SET.status_max_age,
        feed_max_age=AS_SET.feed_max_age,
        line_test_cluster_threshold=3,
        line_own_fault_rate=0.02,
        line_test_cluster_chance=1.0,
    )
    waves = 5_000

    suspected = suspected_in(waves, rule_with(count_alone))

    assert suspected >= waves * 10 / 100  # measured: about 15 in a hundred, one node in seven
