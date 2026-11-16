"""The load test's report: built from what the runs hand in, and writable whatever they found."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loadtest.outcome import Offered, Targets
from loadtest.population import Cast
from loadtest.profile import QUICK
from loadtest.report import build_report, criteria_of
from loadtest.run import Plan, Result
from loadtest.stack import LoadSample, Outage

STARTED = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)
CONTEXT = {"machine": "one test machine", "customers": "400,000", "api_pool": "8"}


def facts(**changed: Any) -> dict[str, Any]:
    recorded: dict[str, Any] = {
        "contacts": 4,
        "replied": 4,
        "first_reply_seconds": {"p50": 0.41, "p95": 0.9, "p99": 1.2, "max": 1.3},
        "reference_ms_at_the_server": {"p50": 4.0, "p99": 9.0, "max": 12.0},
        "by_status": {"AWAITING_NETWORK": 3, "HANDOFF": 1},
        "first_reply": {"outage_active.v1": 3, "handed_to_human.v1": 1},
        "handoffs": {"CUSTOMER_REQUESTED_HUMAN": 1},
        "read_by": {"rules-v1": 4},
        "writes": {"COMPLAINT_REGISTER SUCCEEDED": 4},
        "notifications": {"SUCCEEDED": 120},
        "ops_items": {},
        "dead_lettered": 0,
        "said_clear": 0,
        "turn_steps": [
            {"step": "crm.profile", "calls": 4, "p50_ms": 3, "p95_ms": 7, "max_ms": 9},
        ],
    }
    return {**recorded, **changed}


def wave(**changed: Any) -> dict[str, Any]:
    """What followed the end of the outage: three customers waited, and all were told."""
    measured: dict[str, Any] = {
        "waiting_on_the_outage": 3,
        "told_it_is_over": 3,
        "seconds": 6.0,
        "allowed_seconds": 1800.0,
        "not_told_stand_as": {},
        "told_unverified_meanwhile": 0,
        "lines_tested": 3,
        "lines_failing": 1,
        "suspected_tickets": 0,
        "most_waiting_for_a_turn": 3,
        "longest_wait_seconds": 2.0,
    }
    return {**measured, **changed}


def result(name: str, fault: str | None, offered: list[Offered], **more: Any) -> Result:
    fields: dict[str, Any] = {
        "plan": Plan(
            name=name,
            title="Network Monitoring API down" if fault else "Every dependency up",
            fault=fault,
            due=(0.5, 1.0),
            cast=Cast(roots=("BLR-AGG-0001",), callers=()),
        ),
        "started_at": STARTED,
        "ended_at": STARTED,
        "outages": (Outage("OUT-1", "BLR-AGG-0001", ("BLR-OLT-0001", "BLR-OLT-0002")),),
        "offered": tuple(offered),
        "samples": (
            LoadSample(at=0.0, waiting=2, oldest_seconds=0.0, behind=False),
            LoadSample(at=5.0, waiting=60, oldest_seconds=4.0, behind=True),
            LoadSample(at=10.0, waiting=3, oldest_seconds=1.0, behind=False),
        ),
        "missed_readings": 0,
        "generator_lag": 0.004,
        "drained_after": 4.0,
        "facts": facts(),
        "restoration": wave(),
    }
    return Result(**{**fields, **more})


def good(seconds: float = 0.03, **more: Any) -> Offered:
    fields: dict[str, Any] = {
        "due": 0.5,
        "route": "web",
        "affected": True,
        "accepted": True,
        "reference_seconds": seconds,
        "attempts": 1,
        "reply_seconds": 0.8,
        "reply_template": "outage_active.v1",
    }
    return Offered(**{**fields, **more})


def test_two_good_runs_make_a_report_that_says_so_and_can_be_written(tmp_path: Path) -> None:
    runs = [
        result("baseline", None, [good()] * 4),
        result("network-down", "down", [good(route="channel", reply_seconds=None)] * 4),
    ]

    report = build_report("surge-test", runs, profile=QUICK, targets=Targets(), context=CONTEXT)
    path = report.write(tmp_path)

    assert report.met is True
    assert "every target met" in report.headline
    text = path.read_text(encoding="utf-8")
    assert "| Offered contacts accepted | at least 99.5% | 100.00% (4 of 4) (met) |" in text
    assert "Messages saying the network is clear while its status could not be read" in text
    assert "does not apply" in text  # that target has no meaning with the API up
    assert "one test machine" in text
    assert "Language-model provider: offline (keyword rules stand in" in text
    assert "surge mode had no model to spare" in text
    stored = json.loads((tmp_path / "surge-test.json").read_text(encoding="utf-8"))
    assert stored["met"] is True
    assert stored["numbers"]["baseline"]["backlog"] == {
        "readings": 3,
        "readings_missed": 0,
        "generator_lag_seconds": 0.004,
        "most_waiting_for_a_turn": 60,
        "longest_wait_seconds": 4.0,
        "seconds_behind": 5,
        "drained_after_seconds": 4.0,
    }
    assert stored["numbers"]["network-down"]["fault"] == "down"


def test_one_missed_target_in_one_run_is_a_report_that_says_not_met() -> None:
    runs = [
        result("baseline", None, [good()] * 4),
        result("network-down", "down", [good()] * 3 + [Offered(0.5, "web", True, error="x")]),
    ]

    report = build_report("surge-test", runs, profile=QUICK, targets=Targets(), context=CONTEXT)

    assert report.met is False
    assert "NOT met: Offered contacts accepted" in report.headline


def test_a_message_that_said_clear_with_the_api_down_fails_the_run_whatever_else_held() -> None:
    unsafe = result("network-down", "down", [good()] * 4, facts=facts(said_clear=1))

    criteria = criteria_of(unsafe, Targets())

    assert [criterion.met for criterion in criteria] == [True] * 5 + [False]
    assert criteria[-1].name.startswith("Messages saying the network is clear")
    assert len(criteria_of(result("baseline", None, [good()] * 4), Targets())) == 5


def test_contacts_left_unanswered_fail_the_run_though_every_reference_was_issued() -> None:
    """A reference in two minutes is the regulator's rule, not the whole of being available:
    an orchestrator that answers nobody must not come out of a load test as a pass. Nor may
    the customers it never reached go missing from the count of those told of the end."""
    stuck = facts(contacts=4, replied=1, first_reply={"outage_active.v1": 1, "(none yet)": 3})
    run = result(
        "baseline",
        None,
        [good()] * 4,
        facts=stuck,
        restoration=wave(waiting_on_the_outage=1, told_it_is_over=1),
    )

    report = build_report("surge-test", [run], profile=QUICK, targets=Targets(), context=CONTEXT)

    assert report.met is False
    assert "NOT met: Contacts answered when the surge was over" in report.headline
    (answered,) = [c for c in criteria_of(run, Targets()) if c.name.startswith("Contacts answered")]
    assert (answered.target, answered.measured, answered.met) == ("all", "1 of 4", False)


def test_customers_left_untold_when_the_outage_ended_fail_the_run_and_are_accounted_for(
    tmp_path: Path,
) -> None:
    """Every reference was issued in time, and the run is still not a good one: when the outage
    ended, some who waited on it were not told. The report says so first, and where they stand."""
    left = wave(
        waiting_on_the_outage=9759,
        told_it_is_over=9515,
        seconds=None,
        not_told_stand_as={"network_unknown.v1": 172, "handed_to_human.v1": 72},
        told_unverified_meanwhile=482,
        lines_tested=9759,
        lines_failing=133,
        suspected_tickets=13,
    )
    run = result("baseline", None, [good()] * 4, restoration=left)

    report = build_report("surge-test", [run], profile=QUICK, targets=Targets(), context=CONTEXT)
    text = report.write(tmp_path).read_text(encoding="utf-8")

    assert report.met is False
    assert "NOT met: Waiting customers told that the outage is over" in report.headline
    assert (
        "| Waiting customers told that the outage is over | all, within 30 minutes of its end"
        " | 9,515 of 9,759 (NOT met) |"
    ) in text
    assert "| Those not told stood at | network_unknown.v1 172 · handed_to_human.v1 72 |" in text
    assert "| Told meanwhile that the status cannot be verified | 482 |" in text
    assert "| Their lines tested / found failing | 9,759 / 133 |" in text
    assert "| Tickets raised for a suspected fault on the restored nodes | 13 |" in text


def test_everyone_told_is_a_target_met_and_the_time_it_took_is_given(tmp_path: Path) -> None:
    run = result("baseline", None, [good()] * 4)

    report = build_report("surge-test", [run], profile=QUICK, targets=Targets(), context=CONTEXT)
    text = report.write(tmp_path).read_text(encoding="utf-8")

    assert report.met is True
    assert "every customer who waited on it is told within 30 minutes" in report.target
    assert (
        "| Waiting customers told that the outage is over | all, within 30 minutes of its end"
        " | 3 of 3 (met) |"
    ) in text
    assert "| All of them told after | 6 s |" in text
    assert "| Those not told stood at | — |" in text


def test_a_run_in_which_nothing_went_right_can_still_be_reported(tmp_path: Path) -> None:
    """The report must not be what fails when the platform did: no replies, no readings."""
    nothing = facts(
        replied=0,
        first_reply_seconds={"p50": None, "p95": None, "p99": None, "max": None},
        first_reply={"(none yet)": 4},
        turn_steps=[],
    )
    lost = [Offered(0.5, "web", True, attempts=9, error="ConnectError")] * 4
    failed = result(
        "baseline",
        None,
        lost,
        facts=nothing,
        samples=(),
        drained_after=None,
        restoration=wave(
            waiting_on_the_outage=0,
            told_it_is_over=0,
            seconds=None,
            lines_tested=0,
            lines_failing=0,
            most_waiting_for_a_turn=0,
            longest_wait_seconds=0.0,
        ),
    )

    report = build_report("surge-test", [failed], profile=QUICK, targets=Targets(), context={})
    text = report.write(tmp_path).read_text(encoding="utf-8")

    assert report.met is False
    assert "0.00% (0 of 4) (NOT met)" in text
    assert "none issued (NOT met)" in text
    assert "not within the time allowed" in text
    assert "ConnectError 4" in text
    # Nobody waited on the outage, so nothing can be said about telling them of its end.
    assert "| Waiting customers told that the outage is over |" not in text
    assert "| All of them told after | nobody waited |" in text


def test_a_run_with_the_simulated_model_names_who_read_each_message() -> None:
    """A report never passes a stand-in off as a model, nor a spared message as a read one."""
    shed = facts(read_by={"simulated-1.5s": 1, "rules-v1 (without the model: load_shed)": 3})
    run = result("baseline", None, [good(told_delayed=True)] * 4, facts=shed)

    report = build_report("surge-test", [run], profile=QUICK, targets=Targets(), context=CONTEXT)

    assert report.provider == (
        "as recorded for each message read: simulated-1.5s 1 · rules-v1 (without the model:"
        " load_shed) 3. No model was called: `simulated` is the rules' answer after the time a"
        " model call takes"
    )
    assert not any("had no model to spare" in note for note in report.assumptions)


def test_a_run_with_a_real_model_is_not_said_to_have_called_none() -> None:
    real = facts(read_by={"claude-opus-5-5": 3, "rules-v1": 1})
    run = result("baseline", None, [good()] * 4, facts=real)

    report = build_report("surge-test", [run], profile=QUICK, targets=Targets(), context=CONTEXT)

    assert report.provider == "as recorded for each message read: claude-opus-5-5 3 · rules-v1 1"


def test_the_test_runs_only_against_a_stack_on_this_machine() -> None:
    """It resets the simulator, makes a dependency fail and signs in with the local staff key."""
    from loadtest.__main__ import is_local

    assert is_local("http://127.0.0.1:8080")
    assert is_local("http://localhost:8081")
    assert is_local("http://[::1]:8080")
    assert not is_local("http://care.internal.example:8080")
    assert not is_local("https://10.0.0.7")
