"""Supervisor routing: which specialist acts next.

A pure function of typed state. No model output can change a branch except through the
structured fields the triage reading fills, and none of those unlock an action: they can only
send the contact to a person, to a refusal, to closure after we said service was restored, or on
to diagnostics, where evidence takes over.

A turn is started either by the customer or by the platform itself (the NOC declared or resolved
an outage on the customer's node, or a timer came due). The difference matters once: a customer
who writes always gets an answer, while a system turn only speaks when there is news.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from skylink_care.db.inbox import OUTAGE_DECLARED_EVENT, OUTAGE_RESOLVED_EVENT
from skylink_care.db.line_tests import FAIL as LINE_FAILED
from skylink_care.db.line_tests import PASS as LINE_PASSED
from skylink_care.db.timers import TIMER_FIRED_EVENT
from skylink_care.domain.models import Intent, NetworkState
from skylink_care.domain.network import DecisionReason
from skylink_care.llm.base import CONFIDENCE_SCORE
from skylink_care.orchestrator.state import ContactState, Phase, Route, Trigger

# Loop guard: supervisor passes allowed within one run before a person takes over.
MAX_STEPS_PER_RUN = 12
# Identity prompts per contact: the customer ID, the verifying digits, and one retry.
MAX_IDENTITY_ASKS = 3
# Readings below this are not acted on automatically.
MIN_INTENT_CONFIDENCE = 0.5
CUSTOMER_EVENTS = frozenset({"contact.received", "customer.message"})
NETWORK_STATUS_UNVERIFIED = "NETWORK_STATUS_UNVERIFIED"
LINE_TEST_UNAVAILABLE = "LINE_TEST_UNAVAILABLE"
# With any of these in the same message, "it works now" does not settle the contact.
_STILL_WANTS_SOMETHING = ("requests_credit", "requests_technician", "insists_device_fault")


@dataclass(frozen=True)
class RoutingLimits:
    """How long a contact waits on an unverified network before a person takes it."""

    unverified_rechecks: int = 5
    # A suspected node fault is given longer: the NOC needs minutes to declare an outage.
    suspected_rechecks: int = 15


DEFAULT_LIMITS = RoutingLimits()


@dataclass(frozen=True)
class Decision:
    route: Route
    handoff_reason: str | None = None
    # State the supervisor records together with the route (what started the turn, …).
    updates: Mapping[str, Any] = field(default_factory=dict)


def decide_route(state: ContactState, limits: RoutingLimits = DEFAULT_LIMITS) -> Decision:
    phase = state.get("phase", Phase.NEW_TURN.value)
    # Phases that end the run come first: the loop guard must never re-open a finished turn.
    if phase in {Phase.HANDED_OFF.value, Phase.CLOSED.value}:
        return Decision(Route.FINISH)
    if phase == Phase.REPLIED.value:
        return Decision(Route.AWAIT)
    if state.get("steps", 0) >= MAX_STEPS_PER_RUN:
        return Decision(Route.HANDOFF, "LOOP_GUARD")
    if phase == Phase.NEW_TURN.value:
        return _on_new_events(state)
    if phase == Phase.TRIAGED.value:
        return _after_triage(state)
    if phase == Phase.DIAGNOSED.value:
        return _after_diagnosis(state, limits)
    return Decision(Route.HANDOFF, "UNKNOWN_PHASE")


def _on_new_events(state: ContactState) -> Decision:
    events = state.get("events", [])
    kinds = {event.get("event_type") for event in events}
    fired = {
        (event.get("payload") or {}).get("key")
        for event in events
        if event.get("event_type") == TIMER_FIRED_EVENT
    }
    # A timer that has fired is spent whatever happens next, so its key is cleared on every
    # path. A fired timer with any other key was replaced or cancelled earlier and is ignored.
    spent: dict[str, Any] = {}
    recheck_due = bool(state.get("recheck_key")) and state.get("recheck_key") in fired
    close_due = bool(state.get("close_key")) and state.get("close_key") in fired
    if recheck_due:
        spent |= {"recheck_key": None, "network_rechecks": state.get("network_rechecks", 0) + 1}
    if close_due:
        spent["close_key"] = None

    told = state.get("told_outage_id")
    waiting = bool(told or state.get("resolved_outage_id") or state.get("recheck_key"))
    resolved = _outage_ids(events, OUTAGE_RESOLVED_EVENT) if waiting else []
    # Theirs is the outage they were told about; a customer who was never told of one is owed
    # the news about whatever was resolved on their node. It is noted on every path, because
    # this event is consumed now and will not come again.
    theirs = told if told in resolved else (None if told or not resolved else resolved[-1])
    owed = {"resolved_outage_id": theirs} if theirs else {}

    if kinds & CUSTOMER_EVENTS:
        updates = {**spent, **owed, "trigger": Trigger.CUSTOMER.value}
        return Decision(Route.TRIAGE, updates=updates)
    if close_due:
        return Decision(Route.CLOSE, updates={**spent, "trigger": Trigger.QUIET.value})
    if not waiting:
        # Not waiting on the network: news about it is not for this conversation.
        return Decision(Route.AWAIT, updates=spent)
    if resolved:
        return Decision(
            Route.DIAGNOSE, updates={**spent, **owed, "trigger": Trigger.RESOLVED.value}
        )
    if any(outage_id != told for outage_id in _outage_ids(events, OUTAGE_DECLARED_EVENT)):
        return Decision(Route.DIAGNOSE, updates={**spent, "trigger": Trigger.DECLARED.value})
    if recheck_due:
        return Decision(Route.DIAGNOSE, updates={**spent, "trigger": Trigger.RECHECK.value})
    return Decision(Route.AWAIT, updates=spent)


def _outage_ids(events: list[dict[str, Any]], event_type: str) -> list[str]:
    return [
        str((event.get("payload") or {})["outage_id"])
        for event in events
        if event.get("event_type") == event_type and (event.get("payload") or {}).get("outage_id")
    ]


def _after_triage(state: ContactState) -> Decision:
    intent = state.get("intent", {})
    identity = state.get("identity", {})
    if intent.get("wants_human"):
        return Decision(Route.HANDOFF, "CUSTOMER_REQUESTED_HUMAN")
    if intent.get("abusive_or_off_topic"):
        return Decision(Route.OUT_OF_SCOPE)
    if (
        state.get("asked_if_working")
        and intent.get("confirms_working")
        and not any(intent.get(flag) for flag in _STILL_WANTS_SOMETHING)
    ):
        # We asked whether the service works (after a restoration, a clear check or steps to
        # try); the customer says it does and asks for nothing else. There is nothing to check.
        return Decision(Route.CLOSE)
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


def _after_diagnosis(state: ContactState, limits: RoutingLimits) -> Decision:
    network = state.get("network", {})
    network_state = network.get("state")
    reason = network.get("reason")
    by_customer = state.get("trigger", Trigger.CUSTOMER.value) == Trigger.CUSTOMER.value
    told = state.get("told_outage_id")
    resolved = state.get("resolved_outage_id")
    current = (network.get("outage") or {}).get("outage_id")

    if network_state == NetworkState.ACTIVE.value:
        if by_customer or current != told:
            return Decision(Route.OUTAGE_REPLY)
        if resolved == current:
            # The NOC reported this outage resolved while the live status still shows it. Say
            # nothing yet and look again shortly rather than wait for an event that has passed.
            return _keep_waiting(state, limits, reason)
        # The outage they already know about, still active: nothing to say. The slow watch on
        # it goes on for as long as the outage does, so it is not counted towards a handoff.
        return Decision(Route.KEEP_WAITING, updates={"network_rechecks": 0})
    if network_state == NetworkState.CONFIRMED_CLEAR.value:
        if told or resolved:
            # After an outage the customer waited on, a clear network is "service restored".
            return Decision(Route.RESTORED)
        result = (state.get("line_test") or {}).get("result")
        if result == LINE_FAILED and not state.get("line_hold_over"):
            # The node looks healthy but this line fails. That is also what the first minutes of
            # an undeclared node fault look like, so nothing is arranged until the hold is over.
            return Decision(Route.LINE_HOLD)
        if state.get("line_hold_until") and result not in {LINE_FAILED, LINE_PASSED}:
            # A fault was found on this line and it cannot be tested again just now. That is
            # not a clean bill of health: try again shortly, and give it to a person if the
            # test stays unavailable. A customer who asks meanwhile is told where things stand.
            if by_customer:
                return Decision(Route.LINE_HOLD)
            return _keep_waiting(state, limits, reason, give_up=LINE_TEST_UNAVAILABLE)
        if result == LINE_PASSED and not state.get("guidance_told"):
            # A clear node and a passing line point at the equipment in the home: the one case
            # in which the customer is given steps to try, once.
            return Decision(Route.GUIDE)
        return Decision(Route.CLEAR_REPLY)

    # UNKNOWN, or anything unrecognised: never the device flow.
    if by_customer or reason == DecisionReason.MAPPING_UNRESOLVED.value:
        return Decision(Route.UNKNOWN_REPLY)
    if resolved and reason == DecisionReason.CLEAR_NOT_CONFIRMED.value:
        # The live status cannot be read, but the feed is fresh, carries the NOC's explicit
        # resolution and shows no other outage or alarm on the node. That is news worth passing
        # on as what it is: a report from the network team, not a check of this line.
        return Decision(Route.RESTORED)
    if reason == DecisionReason.SUSPECTED_NODE_FAULT.value and not state.get("suspected_told"):
        return Decision(Route.UNKNOWN_REPLY)  # something specific to say for the first time
    return _keep_waiting(state, limits, reason)


def _keep_waiting(
    state: ContactState,
    limits: RoutingLimits,
    reason: str | None,
    *,
    give_up: str = NETWORK_STATUS_UNVERIFIED,
) -> Decision:
    suspected = reason == DecisionReason.SUSPECTED_NODE_FAULT.value
    limit = limits.suspected_rechecks if suspected else limits.unverified_rechecks
    if state.get("network_rechecks", 0) >= limit:
        return Decision(Route.HANDOFF, give_up)
    return Decision(Route.KEEP_WAITING)
