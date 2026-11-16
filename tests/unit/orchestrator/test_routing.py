"""Supervisor routing is a pure function of evidence. These tables are its specification."""

from __future__ import annotations

from typing import Any

import pytest

from skylink_care.orchestrator.routing import (
    MAX_IDENTITY_ASKS,
    MAX_STEPS_PER_RUN,
    decide_route,
)
from skylink_care.orchestrator.state import ContactState, Phase, Route

IDENTIFIED = {"status": "IDENTIFIED", "customer_id": "C0000001", "verified": True}
SERVICE = {"service_id": "S0000001", "service_type": "broadband", "address_id": "A1"}


def reading(**overrides: Any) -> dict[str, Any]:
    base = {
        "intent": "connectivity",
        "confidence": "high",
        "wants_human": False,
        "insists_device_fault": False,
        "requests_technician": False,
        "requests_credit": False,
        "abusive_or_off_topic": False,
        # What the keyword rules saw beside the reading: here, nothing of note.
        "rules": {},
    }
    return {**base, **overrides}


def triaged(**overrides: Any) -> ContactState:
    base: dict[str, Any] = {
        "phase": Phase.TRIAGED.value,
        "identity": IDENTIFIED,
        "service": SERVICE,
        "intent": reading(),
        "steps": 1,
    }
    return ContactState(**{**base, **overrides})  # type: ignore[typeddict-item]


def test_new_turn_with_a_customer_message_goes_to_triage() -> None:
    state = ContactState(
        phase=Phase.NEW_TURN.value, events=[{"event_type": "contact.received", "seq": 1}]
    )

    assert decide_route(state).route is Route.TRIAGE


def test_new_turn_with_only_system_events_waits() -> None:
    state = ContactState(
        phase=Phase.NEW_TURN.value, events=[{"event_type": "action.finished", "seq": 4}]
    )

    assert decide_route(state).route is Route.AWAIT


def test_identified_connectivity_contact_goes_to_diagnostics() -> None:
    assert decide_route(triaged()).route is Route.DIAGNOSE


TRIAGE_CASES = [
    (
        "asks for a person",
        {"intent": reading(wants_human=True)},
        Route.HANDOFF,
        "CUSTOMER_REQUESTED_HUMAN",
    ),
    (
        "words meant to steer the assistant and nothing of a fault, however it was read",
        {"intent": {**reading(), "rules": {"injection": True}}},
        Route.OUT_OF_SCOPE,
        None,
    ),
    (
        "such words in a plain fault report: the report goes its usual way",
        {"intent": {**reading(), "rules": {"injection": True, "plain_fault": True}}},
        Route.DIAGNOSE,
        None,
    ),
    (
        "abuse and nothing else",
        {"intent": reading(intent="other", confidence="low", abusive_or_off_topic=True)},
        Route.OUT_OF_SCOPE,
        None,
    ),
    (
        "abuse with a fault report is a fault report",
        {"intent": reading(abusive_or_off_topic=True)},
        Route.DIAGNOSE,
        None,
    ),
    (
        "off-topic says the reading, a fault say the customer's plain words",
        {
            "intent": {
                **reading(intent="other", abusive_or_off_topic=True),
                "rules": {"plain_fault": True},
            }
        },
        Route.HANDOFF,
        "LOW_CONFIDENCE",
    ),
    (
        "unrelated request",
        {"intent": reading(intent="other", confidence="low")},
        Route.OUT_OF_SCOPE,
        None,
    ),
    ("crm down", {"identity": {"status": "CRM_UNAVAILABLE"}}, Route.HANDOFF, "CRM_UNAVAILABLE"),
    ("no customer id yet", {"identity": {"status": "NEED_CUSTOMER_ID"}}, Route.ASK_IDENTITY, None),
    ("claim not verified", {"identity": {"status": "NEED_VERIFICATION"}}, Route.ASK_IDENTITY, None),
    ("wrong details", {"identity": {"status": "VERIFICATION_FAILED"}}, Route.ASK_IDENTITY, None),
    ("low confidence", {"intent": reading(confidence="low")}, Route.HANDOFF, "LOW_CONFIDENCE"),
    ("billing", {"intent": reading(intent="billing")}, Route.HANDOFF, "BILLING"),
    ("no service", {"service": None}, Route.HANDOFF, "NO_SERVICE_ON_ACCOUNT"),
]


@pytest.mark.parametrize(
    ("overrides", "route", "reason"),
    [case[1:] for case in TRIAGE_CASES],
    ids=[case[0] for case in TRIAGE_CASES],
)
def test_routes_after_triage(overrides: dict[str, Any], route: Route, reason: str | None) -> None:
    decision = decide_route(triaged(**overrides))

    assert decision.route is route
    assert decision.handoff_reason == reason


def test_identity_is_asked_a_limited_number_of_times_then_a_person_takes_over() -> None:
    unresolved = {"identity": {"status": "NEED_CUSTOMER_ID"}}

    still_asking = decide_route(triaged(identity_asks=MAX_IDENTITY_ASKS - 1, **unresolved))
    gave_up = decide_route(triaged(identity_asks=MAX_IDENTITY_ASKS, **unresolved))

    assert still_asking.route is Route.ASK_IDENTITY
    assert (gave_up.route, gave_up.handoff_reason) == (Route.HANDOFF, "IDENTITY_UNRESOLVED")


def test_a_request_for_a_person_wins_over_everything_else() -> None:
    state = triaged(
        identity={"status": "NEED_CUSTOMER_ID"}, intent=reading(wants_human=True, intent="billing")
    )

    assert decide_route(state).handoff_reason == "CUSTOMER_REQUESTED_HUMAN"


def test_off_topic_text_is_refused_before_any_identity_question() -> None:
    state = triaged(
        identity={"status": "NEED_CUSTOMER_ID"},
        intent=reading(intent="other", abusive_or_off_topic=True),
    )

    assert decide_route(state).route is Route.OUT_OF_SCOPE


@pytest.mark.parametrize(
    ("network_state", "route"),
    [
        ("ACTIVE", Route.OUTAGE_REPLY),
        ("CONFIRMED_CLEAR", Route.CLEAR_REPLY),
        ("UNKNOWN", Route.UNKNOWN_REPLY),
        ("SOMETHING_NEW", Route.UNKNOWN_REPLY),
        (None, Route.UNKNOWN_REPLY),
    ],
)
def test_only_confirmed_clear_reaches_the_device_branch(
    network_state: str | None, route: Route
) -> None:
    state = ContactState(phase=Phase.DIAGNOSED.value, network={"state": network_state}, steps=2)

    assert decide_route(state).route is route


def test_what_the_customer_insists_on_cannot_override_a_declared_outage() -> None:
    state = ContactState(
        phase=Phase.DIAGNOSED.value,
        network={"state": "ACTIVE"},
        intent=reading(insists_device_fault=True, requests_technician=True),
        steps=2,
    )

    assert decide_route(state).route is Route.OUTAGE_REPLY


def test_after_a_reply_the_workflow_waits() -> None:
    assert decide_route(ContactState(phase=Phase.REPLIED.value, steps=3)).route is Route.AWAIT


@pytest.mark.parametrize("phase", [Phase.HANDED_OFF, Phase.CLOSED])
def test_handed_off_and_closed_contacts_finish(phase: Phase) -> None:
    assert decide_route(ContactState(phase=phase.value, steps=3)).route is Route.FINISH


def test_loop_guard_hands_over_when_a_run_takes_too_many_steps() -> None:
    decision = decide_route(triaged(steps=MAX_STEPS_PER_RUN))

    assert (decision.route, decision.handoff_reason) == (Route.HANDOFF, "LOOP_GUARD")


def test_unrecognised_phase_goes_to_a_person_not_to_an_action() -> None:
    decision = decide_route(ContactState(phase="???", steps=1))

    assert (decision.route, decision.handoff_reason) == (Route.HANDOFF, "UNKNOWN_PHASE")


def test_loop_guard_does_not_fire_again_once_the_contact_is_handed_off() -> None:
    """Regression: the guard used to outrank the phase and hand off in a loop."""
    after_handoff = ContactState(phase=Phase.HANDED_OFF.value, steps=MAX_STEPS_PER_RUN + 1)

    assert decide_route(after_handoff).route is Route.FINISH


def test_loop_guard_does_not_interrupt_a_reply_that_is_already_out() -> None:
    assert decide_route(ContactState(phase=Phase.REPLIED.value, steps=99)).route is Route.AWAIT


def test_a_request_the_fallback_rules_could_not_place_goes_to_a_person() -> None:
    """When the model failed and the rules only know 'other', do not refuse on a guess."""
    state = triaged(intent={**reading(intent="other", confidence="low"), "degraded": True})

    decision = decide_route(state)

    assert (decision.route, decision.handoff_reason) == (Route.HANDOFF, "LOW_CONFIDENCE")


def test_abuse_is_still_refused_when_the_model_is_degraded() -> None:
    state = triaged(intent={**reading(intent="other", abusive_or_off_topic=True), "degraded": True})

    assert decide_route(state).route is Route.OUT_OF_SCOPE


def test_a_claimed_identity_never_reaches_diagnostics() -> None:
    """Only IDENTIFIED (which the triage agent sets after verification) may go on."""
    for status in ("NEED_CUSTOMER_ID", "NEED_VERIFICATION", "VERIFICATION_FAILED"):
        state = triaged(identity={"status": status, "customer_id": None, "verified": False})

        assert decide_route(state).route is Route.ASK_IDENTITY
