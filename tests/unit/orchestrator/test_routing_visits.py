"""Routing around a technician visit.

The rule being specified: a visit is considered only for a line that still fails after the
hold on a node proven clear; a reply to an offer is read as the answer to that offer; and a
line that needs a repair is never closed because nobody answered.
"""

from __future__ import annotations

from typing import Any

import pytest

from skylink_care.orchestrator.handover import handoff_summary, wording_for
from skylink_care.orchestrator.routing import VISIT_OFFER_UNANSWERED, decide_route
from skylink_care.orchestrator.state import ContactState, Phase, Route, Trigger

ACTION = "11111111-1111-1111-1111-111111111111"
SLOT = {
    "slot_id": "SL-560038-20261116-AM",
    "start": "2026-11-16T03:30:00+00:00",
    "end": "2026-11-16T07:30:00+00:00",
}
OFFERED = {"status": "OFFERED", "slots": [SLOT], "asks": 0, "attempt": 0}
BOOKING = {"status": "BOOKING", "action_id": ACTION, "slot": SLOT, "attempt": 1}


def state(phase: Phase, **fields: Any) -> ContactState:
    return ContactState(phase=phase.value, steps=1, **fields)  # type: ignore[typeddict-item]


def clear_node(line: str | None, **fields: Any) -> ContactState:
    network = {"state": "CONFIRMED_CLEAR", "reason": "LIVE_CLEAR", "outage": None}
    line_test = {"result": line, "tested_at": "2026-11-15T06:29:30+00:00"} if line else None
    return state(
        Phase.DIAGNOSED,
        network=network,
        line_test=line_test,
        trigger=fields.pop("trigger", Trigger.CUSTOMER.value),
        **fields,
    )


def triaged(**intent: Any) -> ContactState:
    """A reply after an offer. A few words with no topic of their own ("2", "yes") keep the
    topic the customer came about, which is how triage hands them on."""
    reading = {
        "intent": "connectivity",
        "confidence": "high",
        "carried_over": True,
        "wants_human": False,
        "abusive_or_off_topic": False,
        "confirms_working": False,
    }
    return state(
        Phase.TRIAGED,
        identity={"status": "IDENTIFIED"},
        service={"service_id": "S1", "service_type": "broadband"},
        intent={**reading, **intent},
        visit=OFFERED,
    )


def finished(action_id: str = ACTION) -> dict[str, Any]:
    return {
        "event_type": "action.finished",
        "seq": 1,
        "payload": {"action_id": action_id, "status": "SUCCEEDED"},
    }


# --- when a visit is considered ------------------------------------------------------------------


def test_line_still_failing_after_the_hold_on_a_clear_node_is_the_case_for_a_visit() -> None:
    held = clear_node("FAIL", line_hold_until="2026-11-15T06:28:00+00:00", line_hold_over=True)

    assert decide_route(held).route is Route.VISIT


@pytest.mark.parametrize("trigger", [Trigger.CUSTOMER, Trigger.RECHECK])
def test_failing_line_is_held_first_whoever_started_the_turn(trigger: Trigger) -> None:
    just_failed = clear_node("FAIL", line_hold_over=False, trigger=trigger.value)

    assert decide_route(just_failed).route is Route.LINE_HOLD


def test_passing_line_never_leads_to_a_visit() -> None:
    assert decide_route(clear_node("PASS")).route is Route.GUIDE
    assert decide_route(clear_node("PASS", guidance_told="g §1 v1")).route is Route.CLEAR_REPLY


@pytest.mark.parametrize("network_state", ["ACTIVE", "UNKNOWN"])
def test_no_visit_is_considered_while_the_node_is_not_proven_clear(network_state: str) -> None:
    failing = state(
        Phase.DIAGNOSED,
        network={"state": network_state, "reason": "LIVE_OUTAGE", "outage": {"outage_id": "O1"}},
        line_test={"result": "FAIL", "tested_at": "2026-11-15T06:29:30+00:00"},
        line_hold_over=True,
        trigger=Trigger.CUSTOMER.value,
    )

    assert decide_route(failing).route is not Route.VISIT


# --- the customer's answer -----------------------------------------------------------------------


def test_few_words_in_reply_to_an_offer_lead_to_the_evidence_first() -> None:
    """The reply is read once the network and the line have been looked at again."""
    assert decide_route(triaged()).route is Route.DIAGNOSE


def test_booking_declined_on_changed_evidence_goes_back_to_the_evidence() -> None:
    declined = state(Phase.RECHECK, visit={"status": "CHOSEN", "chosen": SLOT, "retries": 1})

    assert decide_route(declined).route is Route.DIAGNOSE


@pytest.mark.parametrize(
    ("intent", "route", "reason"),
    [
        ({"wants_human": True}, Route.HANDOFF, "CUSTOMER_REQUESTED_HUMAN"),
        ({"abusive_or_off_topic": True}, Route.OUT_OF_SCOPE, None),
        ({"intent": "billing", "confidence": "high"}, Route.HANDOFF, "BILLING"),
        ({"carried_over": False}, Route.DIAGNOSE, None),
        ({"intent": "other", "degraded": True}, Route.HANDOFF, "LOW_CONFIDENCE"),
    ],
    ids=["a person", "abuse", "a bill", "a fault report", "the model failed"],
)
def test_message_that_is_a_request_of_its_own_goes_its_usual_way(
    intent: dict[str, Any], route: Route, reason: str | None
) -> None:
    decision = decide_route(triaged(**intent))

    assert (decision.route, decision.handoff_reason) == (route, reason)


def test_it_works_now_settles_the_contact_without_having_been_asked() -> None:
    """The offer did not ask whether the service works. If the customer says it does, there
    is no fault left to send anyone to."""
    assert decide_route(triaged(confirms_working=True)).route is Route.CLOSE


def test_it_works_now_with_another_request_is_not_the_end() -> None:
    wants_more = triaged(confirms_working=True, requests_credit=True)

    assert decide_route(wants_more).route is not Route.CLOSE


# --- the booking's outcome, and silence ----------------------------------------------------------


def test_end_of_the_booking_the_contact_waits_on_is_told() -> None:
    waiting = state(Phase.NEW_TURN, events=[finished()], visit=BOOKING)

    assert decide_route(waiting).route is Route.VISIT_RESULT


@pytest.mark.parametrize(
    "visit",
    [BOOKING | {"action_id": "22222222-2222-2222-2222-222222222222"}, OFFERED, None],
    ids=["another action", "nothing proposed yet", "no visit"],
)
def test_other_finished_actions_say_nothing_about_a_visit(visit: dict[str, Any] | None) -> None:
    waiting = state(Phase.NEW_TURN, events=[finished()], visit=visit)

    assert decide_route(waiting).route is Route.AWAIT


@pytest.mark.parametrize("status", ["OFFERED", "CHOSEN"])
def test_offer_nobody_answered_goes_to_a_person_and_is_not_closed(status: str) -> None:
    timer = {"event_type": "timer.fired", "seq": 1, "payload": {"key": "close:3:9"}}
    silent = state(
        Phase.NEW_TURN, events=[timer], close_key="close:3:9", visit={**OFFERED, "status": status}
    )

    decision = decide_route(silent)

    assert (decision.route, decision.handoff_reason) == (Route.HANDOFF, VISIT_OFFER_UNANSWERED)
    assert decision.updates == {"close_key": None}
    assert "haven't heard back from you about a technician visit" in wording_for(
        VISIT_OFFER_UNANSWERED
    )


@pytest.mark.parametrize("status", ["BOOKED", "DECLINED"])
def test_silence_after_a_settled_visit_closes_as_usual(status: str) -> None:
    timer = {"event_type": "timer.fired", "seq": 1, "payload": {"key": "close:3:9"}}
    settled = state(Phase.NEW_TURN, events=[timer], close_key="close:3:9", visit={"status": status})

    assert decide_route(settled).route is Route.CLOSE


# --- what a person is told -----------------------------------------------------------------------


def test_person_taking_over_sees_where_the_visit_stands() -> None:
    booked = state(
        Phase.REPLIED, visit={"status": "BOOKED", "slot": SLOT, "appointment_id": "AP-9"}
    )
    offered = state(Phase.REPLIED, visit=OFFERED)

    assert (
        "Technician visit: BOOKED, slot SL-560038-20261116-AM, appointment AP-9."
        in handoff_summary(booked, "CUSTOMER_REQUESTED_HUMAN")
    )
    assert "Technician visit: OFFERED, slot none chosen, appointment none." in handoff_summary(
        offered, VISIT_OFFER_UNANSWERED
    )
