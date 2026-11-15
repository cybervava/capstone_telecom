"""Routing of turns the platform starts itself: outage declared, outage resolved, timers.

The rule being specified: a customer who writes always gets an answer; a turn the platform starts
speaks only when there is news, and otherwise waits, bounded by the re-check limits.
"""

from __future__ import annotations

from typing import Any

import pytest

from skylink_care.orchestrator.routing import NETWORK_STATUS_UNVERIFIED, RoutingLimits, decide_route
from skylink_care.orchestrator.state import ContactState, Phase, Route, Trigger

LIMITS = RoutingLimits(unverified_rechecks=3, suspected_rechecks=6)
TOLD = "OUT-1"


def event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {"event_type": event_type, "seq": 1, "payload": payload}


def new_turn(*events: dict[str, Any], **state: Any) -> ContactState:
    return ContactState(phase=Phase.NEW_TURN.value, events=list(events), **state)  # type: ignore[typeddict-item]


def diagnosed(
    network_state: str,
    *,
    trigger: Trigger,
    reason: str = "LIVE_OUTAGE",
    outage_id: str | None = None,
    **state: Any,
) -> ContactState:
    network = {
        "state": network_state,
        "reason": reason,
        "outage": {"outage_id": outage_id} if outage_id else None,
    }
    return ContactState(  # type: ignore[typeddict-item]
        phase=Phase.DIAGNOSED.value, network=network, trigger=trigger.value, steps=2, **state
    )


# --- what starts a turn --------------------------------------------------------------------------


def test_resolution_of_the_outage_the_customer_waits_on_starts_a_check() -> None:
    decision = decide_route(new_turn(event("outage.resolved", outage_id=TOLD), told_outage_id=TOLD))

    assert decision.route is Route.DIAGNOSE
    assert decision.updates == {"trigger": "resolved", "resolved_outage_id": TOLD}


def test_resolution_of_another_outage_is_checked_but_owes_no_restored_message() -> None:
    decision = decide_route(
        new_turn(event("outage.resolved", outage_id="OUT-2"), told_outage_id=TOLD)
    )

    assert decision.route is Route.DIAGNOSE
    assert "resolved_outage_id" not in decision.updates


def test_customer_who_was_never_told_of_an_outage_is_owed_news_of_a_resolution() -> None:
    decision = decide_route(
        new_turn(event("outage.resolved", outage_id="OUT-2"), recheck_key="recheck:1")
    )

    assert decision.updates["resolved_outage_id"] == "OUT-2"


def test_declaration_of_a_new_outage_starts_a_check() -> None:
    decision = decide_route(
        new_turn(event("outage.declared", outage_id="OUT-2"), recheck_key="recheck:1")
    )

    assert (decision.route, decision.updates) == (Route.DIAGNOSE, {"trigger": "declared"})


def test_declaration_of_the_outage_the_customer_already_knows_is_ignored() -> None:
    decision = decide_route(new_turn(event("outage.declared", outage_id=TOLD), told_outage_id=TOLD))

    assert decision.route is Route.AWAIT


@pytest.mark.parametrize("event_type", ["outage.declared", "outage.resolved"])
def test_network_news_is_not_for_a_conversation_that_is_not_waiting_on_it(event_type: str) -> None:
    decision = decide_route(new_turn(event(event_type, outage_id="OUT-2")))

    assert decision.route is Route.AWAIT


def test_due_recheck_timer_starts_a_check_and_is_counted() -> None:
    decision = decide_route(
        new_turn(
            event("timer.fired", kind="recheck_network", key="recheck:4"),
            recheck_key="recheck:4",
            network_rechecks=1,
        )
    )

    assert decision.route is Route.DIAGNOSE
    assert decision.updates == {"trigger": "recheck", "recheck_key": None, "network_rechecks": 2}


def test_stale_timer_is_ignored() -> None:
    decision = decide_route(
        new_turn(
            event("timer.fired", kind="recheck_network", key="recheck:2"),
            recheck_key="recheck:4",
        )
    )

    assert (decision.route, decision.updates) == (Route.AWAIT, {})


def test_quiet_close_timer_closes_the_contact() -> None:
    decision = decide_route(
        new_turn(event("timer.fired", kind="close_quiet", key="close:3"), close_key="close:3")
    )

    assert decision.route is Route.CLOSE
    assert decision.updates == {"close_key": None, "trigger": "quiet"}


def test_customer_message_wins_over_timers_that_fired_with_it_and_spends_them() -> None:
    decision = decide_route(
        new_turn(
            event("timer.fired", kind="close_quiet", key="close:3"),
            event("timer.fired", kind="recheck_network", key="recheck:3"),
            event("customer.message"),
            close_key="close:3",
            recheck_key="recheck:3",
        )
    )

    assert decision.route is Route.TRIAGE
    # A spent key left in place would leave the contact waiting on a timer that never comes.
    assert decision.updates == {
        "close_key": None,
        "recheck_key": None,
        "network_rechecks": 1,
        "trigger": "customer",
    }


def test_resolution_takes_precedence_over_a_recheck_in_the_same_batch() -> None:
    decision = decide_route(
        new_turn(
            event("timer.fired", kind="recheck_network", key="recheck:2"),
            event("outage.resolved", outage_id=TOLD),
            told_outage_id=TOLD,
            recheck_key="recheck:2",
        )
    )

    assert decision.updates["trigger"] == "resolved"
    assert decision.updates["recheck_key"] is None


# --- after the evidence is in --------------------------------------------------------------------

AFTER_DIAGNOSIS = [
    (
        "customer asks during the outage they know about: answered again",
        diagnosed("ACTIVE", trigger=Trigger.CUSTOMER, outage_id=TOLD, told_outage_id=TOLD),
        Route.OUTAGE_REPLY,
    ),
    (
        "declared outage is news",
        diagnosed("ACTIVE", trigger=Trigger.DECLARED, outage_id="OUT-2", recheck_key="r"),
        Route.OUTAGE_REPLY,
    ),
    (
        "same outage on a system turn: nothing to say, keep watching",
        diagnosed("ACTIVE", trigger=Trigger.DECLARED, outage_id=TOLD, told_outage_id=TOLD),
        Route.KEEP_WAITING,
    ),
    (
        "NOC says resolved, live status still shows it: look again",
        diagnosed(
            "ACTIVE",
            trigger=Trigger.RESOLVED,
            outage_id=TOLD,
            told_outage_id=TOLD,
            resolved_outage_id=TOLD,
        ),
        Route.KEEP_WAITING,
    ),
    (
        "a different outage is active after theirs resolved",
        diagnosed(
            "ACTIVE",
            trigger=Trigger.RESOLVED,
            outage_id="OUT-2",
            told_outage_id=TOLD,
            resolved_outage_id=TOLD,
        ),
        Route.OUTAGE_REPLY,
    ),
    (
        "clear after an outage they waited on",
        diagnosed(
            "CONFIRMED_CLEAR", trigger=Trigger.RESOLVED, reason="LIVE_CLEAR", told_outage_id=TOLD
        ),
        Route.RESTORED,
    ),
    (
        "customer asks and their outage is gone",
        diagnosed(
            "CONFIRMED_CLEAR", trigger=Trigger.CUSTOMER, reason="LIVE_CLEAR", told_outage_id=TOLD
        ),
        Route.RESTORED,
    ),
    (
        "clear with no outage in the story",
        diagnosed("CONFIRMED_CLEAR", trigger=Trigger.RECHECK, reason="LIVE_CLEAR"),
        Route.CLEAR_REPLY,
    ),
    (
        "customer asks, status unknown: told so",
        diagnosed("UNKNOWN", trigger=Trigger.CUSTOMER, reason="NO_FRESH_EVIDENCE"),
        Route.UNKNOWN_REPLY,
    ),
    (
        "mapping lost on a re-check: a person is needed",
        diagnosed("UNKNOWN", trigger=Trigger.RECHECK, reason="MAPPING_UNRESOLVED"),
        Route.UNKNOWN_REPLY,
    ),
    (
        "NOC resolution with a fresh, quiet feed and no live status",
        diagnosed(
            "UNKNOWN",
            trigger=Trigger.RESOLVED,
            reason="CLEAR_NOT_CONFIRMED",
            told_outage_id=TOLD,
            resolved_outage_id=TOLD,
        ),
        Route.RESTORED,
    ),
    (
        "NOC resolution but an alarm still stands: not restored",
        diagnosed(
            "UNKNOWN",
            trigger=Trigger.RESOLVED,
            reason="SUSPECTED_NODE_FAULT",
            told_outage_id=TOLD,
            resolved_outage_id=TOLD,
            suspected_told=True,
        ),
        Route.KEEP_WAITING,
    ),
    (
        "quiet feed without a resolution is not a restoration",
        diagnosed("UNKNOWN", trigger=Trigger.RECHECK, reason="CLEAR_NOT_CONFIRMED"),
        Route.KEEP_WAITING,
    ),
    (
        "suspicion found on a re-check is said once",
        diagnosed("UNKNOWN", trigger=Trigger.RECHECK, reason="SUSPECTED_NODE_FAULT"),
        Route.UNKNOWN_REPLY,
    ),
    (
        "suspicion already said: wait",
        diagnosed(
            "UNKNOWN", trigger=Trigger.RECHECK, reason="SUSPECTED_NODE_FAULT", suspected_told=True
        ),
        Route.KEEP_WAITING,
    ),
    (
        "unrecognised state is never the device flow",
        diagnosed("WHO_KNOWS", trigger=Trigger.RECHECK, reason="NO_FRESH_EVIDENCE"),
        Route.KEEP_WAITING,
    ),
]


@pytest.mark.parametrize(
    ("state", "route"),
    [(state, route) for _, state, route in AFTER_DIAGNOSIS],
    ids=[name for name, _, _ in AFTER_DIAGNOSIS],
)
def test_after_diagnosis(state: ContactState, route: Route) -> None:
    assert decide_route(state, LIMITS).route is route


@pytest.mark.parametrize(
    ("reason", "rechecks", "route"),
    [
        ("NO_FRESH_EVIDENCE", 2, Route.KEEP_WAITING),
        ("NO_FRESH_EVIDENCE", 3, Route.HANDOFF),
        ("SUSPECTED_NODE_FAULT", 3, Route.KEEP_WAITING),  # a suspected fault is given longer
        ("SUSPECTED_NODE_FAULT", 5, Route.KEEP_WAITING),
        ("SUSPECTED_NODE_FAULT", 6, Route.HANDOFF),
    ],
)
def test_waiting_is_bounded_by_the_recheck_limit(reason: str, rechecks: int, route: Route) -> None:
    state = diagnosed(
        "UNKNOWN",
        trigger=Trigger.RECHECK,
        reason=reason,
        network_rechecks=rechecks,
        suspected_told=True,
    )

    decision = decide_route(state, LIMITS)

    assert decision.route is route
    if route is Route.HANDOFF:
        assert decision.handoff_reason == NETWORK_STATUS_UNVERIFIED


def test_customer_always_gets_an_answer_even_past_the_limit() -> None:
    state = diagnosed(
        "UNKNOWN", trigger=Trigger.CUSTOMER, reason="NO_FRESH_EVIDENCE", network_rechecks=99
    )

    assert decide_route(state, LIMITS).route is Route.UNKNOWN_REPLY


# --- confirmation after restoration ----------------------------------------------------------


def after_triage(**overrides: Any) -> ContactState:
    intent = {
        "intent": "connectivity",
        "confidence": "high",
        "wants_human": False,
        "abusive_or_off_topic": False,
        "confirms_working": True,
    }
    base: dict[str, Any] = {
        "phase": Phase.TRIAGED.value,
        "identity": {"status": "IDENTIFIED"},
        "service": {"service_id": "S1", "service_type": "broadband", "address_id": "A1"},
        "intent": intent,
        "steps": 1,
    }
    return ContactState(**{**base, **overrides})  # type: ignore[typeddict-item]


def test_it_works_now_closes_the_contact_once_restoration_was_announced() -> None:
    assert decide_route(after_triage(restoration_told=True)).route is Route.CLOSE


def test_it_works_now_means_nothing_before_restoration_was_announced() -> None:
    assert decide_route(after_triage()).route is Route.DIAGNOSE


def test_asking_for_a_person_beats_a_confirmation() -> None:
    state = after_triage(restoration_told=True)
    state["intent"] = {**state["intent"], "wants_human": True}

    assert decide_route(state).route is Route.HANDOFF


def test_confirmation_inside_an_injection_attempt_closes_nothing() -> None:
    state = after_triage(restoration_told=True)
    state["intent"] = {**state["intent"], "abusive_or_off_topic": True}

    assert decide_route(state).route is Route.OUT_OF_SCOPE


def test_watching_an_outage_that_is_simply_still_active_never_counts_towards_a_handoff() -> None:
    """A long outage is not a failure to verify: the slow watch goes on as long as it does."""
    state = diagnosed(
        "ACTIVE", trigger=Trigger.RECHECK, outage_id=TOLD, told_outage_id=TOLD, network_rechecks=99
    )

    decision = decide_route(state, LIMITS)

    assert (decision.route, decision.updates) == (Route.KEEP_WAITING, {"network_rechecks": 0})


def test_sources_that_disagree_about_a_resolved_outage_are_watched_only_so_long() -> None:
    state = diagnosed(
        "ACTIVE",
        trigger=Trigger.RECHECK,
        outage_id=TOLD,
        told_outage_id=TOLD,
        resolved_outage_id=TOLD,
        network_rechecks=3,
    )

    assert decide_route(state, LIMITS).route is Route.HANDOFF


def test_resolution_arriving_together_with_a_customer_message_is_still_noted() -> None:
    """The event is consumed by this turn and will not come again."""
    decision = decide_route(
        new_turn(
            event("outage.resolved", outage_id=TOLD), event("customer.message"), told_outage_id=TOLD
        )
    )

    assert decision.route is Route.TRIAGE
    assert decision.updates == {"trigger": "customer", "resolved_outage_id": TOLD}


@pytest.mark.parametrize("also", ["requests_credit", "requests_technician", "insists_device_fault"])
def test_it_works_now_with_something_else_asked_for_does_not_close_the_contact(also: str) -> None:
    state = after_triage(restoration_told=True)
    state["intent"] = {**state["intent"], also: True}

    assert decide_route(state).route is not Route.CLOSE


# --- the customer's own line ---------------------------------------------------------------------


def clear(**state: Any) -> ContactState:
    return diagnosed("CONFIRMED_CLEAR", trigger=Trigger.CUSTOMER, reason="LIVE_CLEAR", **state)


@pytest.mark.parametrize(
    ("state", "route"),
    [
        (clear(line_test={"result": "FAIL"}), Route.LINE_HOLD),
        (clear(line_test={"result": "FAIL"}, line_hold_over=True), Route.CLEAR_REPLY),
        (clear(line_test={"result": "PASS"}), Route.CLEAR_REPLY),
        (clear(line_test={"result": "INCONCLUSIVE"}), Route.CLEAR_REPLY),
        (clear(line_test=None), Route.CLEAR_REPLY),
        # An outage the customer waited on has ended: that news comes first.
        (clear(line_test={"result": "FAIL"}, told_outage_id=TOLD), Route.RESTORED),
    ],
    ids=[
        "failing line is held",
        "still failing when the hold is over",
        "passing line",
        "inconclusive test",
        "no test could be run",
        "restoration outranks the line",
    ],
)
def test_clear_node_is_followed_by_what_the_line_test_allows(
    state: ContactState, route: Route
) -> None:
    assert decide_route(state, LIMITS).route is route


def test_failing_line_never_unlocks_anything_while_the_node_is_not_confirmed_clear() -> None:
    state = diagnosed(
        "UNKNOWN",
        trigger=Trigger.CUSTOMER,
        reason="NO_FRESH_EVIDENCE",
        line_test={"result": "FAIL"},
        line_hold_over=True,
    )

    assert decide_route(state, LIMITS).route is Route.UNKNOWN_REPLY


def test_line_that_cannot_be_retested_after_a_hold_is_watched_then_given_to_a_person() -> None:
    """A fault was seen; no result now is not a clean bill of health."""

    def after_hold(rechecks: int) -> ContactState:
        return diagnosed(
            "CONFIRMED_CLEAR",
            trigger=Trigger.RECHECK,
            reason="LIVE_CLEAR",
            line_test=None,
            line_hold_until="2026-11-15T04:40:00+00:00",
            line_hold_over=True,
            network_rechecks=rechecks,
        )

    waiting = decide_route(after_hold(1), LIMITS)
    given_up = decide_route(after_hold(LIMITS.unverified_rechecks), LIMITS)

    assert waiting.route is Route.KEEP_WAITING
    assert (given_up.route, given_up.handoff_reason) == (Route.HANDOFF, "LINE_TEST_UNAVAILABLE")


def test_customer_who_asks_while_the_line_cannot_be_retested_is_still_answered() -> None:
    state = clear(
        line_test={"result": "INCONCLUSIVE"},
        line_hold_until="2026-11-15T04:40:00+00:00",
        line_hold_over=True,
        network_rechecks=99,
    )

    assert decide_route(state, LIMITS).route is Route.LINE_HOLD
