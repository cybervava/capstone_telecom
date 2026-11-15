"""Supervisor routing: which specialist acts next.

A pure function of typed state. No model output can change a branch except through the
structured fields the triage reading fills, and none of those unlock an action: they can only
send the contact to a person, to a refusal, or on to diagnostics, where evidence takes over.
"""

from __future__ import annotations

from dataclasses import dataclass

from skylink_care.domain.models import Intent, NetworkState
from skylink_care.llm.base import CONFIDENCE_SCORE
from skylink_care.orchestrator.state import ContactState, Phase, Route

# Loop guard: supervisor passes allowed within one run before a person takes over.
MAX_STEPS_PER_RUN = 12
# Identity prompts per contact: the customer ID, the verifying digits, and one retry.
MAX_IDENTITY_ASKS = 3
# Readings below this are not acted on automatically.
MIN_INTENT_CONFIDENCE = 0.5
CUSTOMER_EVENTS = frozenset({"contact.received", "customer.message"})


@dataclass(frozen=True)
class Decision:
    route: Route
    handoff_reason: str | None = None


def decide_route(state: ContactState) -> Decision:
    phase = state.get("phase", Phase.NEW_TURN.value)
    # Phases that end the run come first: the loop guard must never re-open a finished turn.
    if phase in {Phase.HANDED_OFF.value, Phase.CLOSED.value}:
        return Decision(Route.FINISH)
    if phase == Phase.REPLIED.value:
        return Decision(Route.AWAIT)
    if state.get("steps", 0) >= MAX_STEPS_PER_RUN:
        return Decision(Route.HANDOFF, "LOOP_GUARD")
    if phase == Phase.NEW_TURN.value:
        has_customer_text = any(
            event.get("event_type") in CUSTOMER_EVENTS for event in state.get("events", [])
        )
        return Decision(Route.TRIAGE if has_customer_text else Route.AWAIT)
    if phase == Phase.TRIAGED.value:
        return _after_triage(state)
    if phase == Phase.DIAGNOSED.value:
        return _after_diagnosis(state)
    return Decision(Route.HANDOFF, "UNKNOWN_PHASE")


def _after_triage(state: ContactState) -> Decision:
    intent = state.get("intent", {})
    identity = state.get("identity", {})
    if intent.get("wants_human"):
        return Decision(Route.HANDOFF, "CUSTOMER_REQUESTED_HUMAN")
    if intent.get("abusive_or_off_topic"):
        return Decision(Route.OUT_OF_SCOPE)
    if intent.get("intent") == Intent.OTHER.value:
        if intent.get("degraded"):
            # The model failed and the keyword rules could not place the request. That is not
            # evidence that it is out of scope, so a person reads it instead of a refusal.
            return Decision(Route.HANDOFF, "LOW_CONFIDENCE")
        return Decision(Route.OUT_OF_SCOPE)
    status = identity.get("status")
    if status == "CRM_UNAVAILABLE":
        return Decision(Route.HANDOFF, "CRM_UNAVAILABLE")
    if status != "IDENTIFIED":
        if state.get("identity_asks", 0) < MAX_IDENTITY_ASKS:
            return Decision(Route.ASK_IDENTITY)
        return Decision(Route.HANDOFF, "IDENTITY_UNRESOLVED")
    if CONFIDENCE_SCORE.get(str(intent.get("confidence")), 0.0) < MIN_INTENT_CONFIDENCE:
        return Decision(Route.HANDOFF, "LOW_CONFIDENCE")
    if intent.get("intent") == Intent.BILLING.value:
        return Decision(Route.HANDOFF, "BILLING")
    if not state.get("service"):
        return Decision(Route.HANDOFF, "NO_SERVICE_ON_ACCOUNT")
    return Decision(Route.DIAGNOSE)


def _after_diagnosis(state: ContactState) -> Decision:
    network_state = state.get("network", {}).get("state")
    if network_state == NetworkState.ACTIVE.value:
        return Decision(Route.OUTAGE_REPLY)
    if network_state == NetworkState.CONFIRMED_CLEAR.value:
        return Decision(Route.CLEAR_REPLY)
    # UNKNOWN, or anything unrecognised: never the device flow.
    return Decision(Route.UNKNOWN_REPLY)
