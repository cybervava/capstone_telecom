"""Workflow state for one contact.

Deliberately small and JSON-plain: identifiers, the evidence summary and the inbox cursor.
Conversation text and tool payloads live in their own tables, so a checkpoint stays a few
hundred bytes however long the contact lasts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class Phase(StrEnum):
    NEW_TURN = "NEW_TURN"  # fresh events were drained; nothing decided yet
    TRIAGED = "TRIAGED"  # identity and intent are known for this turn
    DIAGNOSED = "DIAGNOSED"  # a network decision is available for this turn
    RECHECK = "RECHECK"  # a booking was declined on changed evidence: look at it again
    REPLIED = "REPLIED"  # this turn is finished; wait for the next event
    HANDED_OFF = "HANDED_OFF"  # a person owns the contact now
    CLOSED = "CLOSED"


class Trigger(StrEnum):
    """What started the turn. It decides whether an unchanged finding is worth repeating."""

    CUSTOMER = "customer"  # the customer wrote: they always get an answer
    DECLARED = "declared"  # the NOC declared an outage on the customer's node
    RESOLVED = "resolved"  # the NOC resolved an outage on the customer's node
    RECHECK = "recheck"  # the timer armed while the network status could not be verified
    QUIET = "quiet"  # the customer did not answer for the quiet-close period


class Route(StrEnum):
    TRIAGE = "triage"
    ASK_IDENTITY = "ask_identity"
    OUT_OF_SCOPE = "out_of_scope"
    DIAGNOSE = "diagnose"
    OUTAGE_REPLY = "outage_reply"
    UNKNOWN_REPLY = "unknown_reply"
    CLEAR_REPLY = "clear_reply"
    LINE_HOLD = "line_hold"
    GUIDE = "guide"
    RESTORED = "restored"
    KEEP_WAITING = "keep_waiting"
    CREDIT_RESULT = "credit_result"
    VISIT = "visit"
    VISIT_RESULT = "visit_result"
    CLOSE = "close"
    HANDOFF = "handoff"
    AWAIT = "await_event"
    FINISH = "finish"


class ContactState(TypedDict, total=False):
    contact_id: str
    inbox_cursor: int
    turn: int
    steps: int
    phase: str
    route: str
    handoff_reason: str | None
    events: list[dict[str, Any]]
    trigger: str
    identity: dict[str, Any]
    # What the customer has offered as identification so far (not yet believed).
    claim: dict[str, Any]
    identity_asks: int
    # Highest inbound message id already read by triage.
    message_cursor: int
    service: dict[str, Any] | None
    intent: dict[str, Any]
    mapping: dict[str, Any]
    network: dict[str, Any]
    # The latest remote test of this customer's line, when one could be run.
    line_test: dict[str, Any] | None
    # A failed line on a clear node is held until this moment before a repair is arranged: it
    # may be the first sign of a node fault. ``line_hold_over`` is set by the diagnosis that
    # finds the moment has passed (and has tested the line afresh).
    line_hold_until: str | None
    line_hold_over: bool
    # When the fault that started the hold was seen, for the person who may take over.
    line_fault_seen_at: str | None
    episode_id: str | None
    # The outage the customer has been told about and is waiting on.
    told_outage_id: str | None
    # An outage on their node the NOC has reported resolved: "service restored" is owed.
    resolved_outage_id: str | None
    # Key of the armed network re-check timer. A timer with any other key is stale.
    recheck_key: str | None
    # Re-checks made since the customer was last told something new.
    network_rechecks: int
    # The customer has been told a node fault is suspected (said once per episode of waiting).
    suspected_told: bool
    # Key of the armed quiet-close timer, set whenever a turn ends waiting for the customer.
    close_key: str | None
    # The last thing said ended by asking whether the service works (after a restoration, after
    # guide steps, after a clear check). Only then does "it works now" close the contact. Any
    # later news about the network withdraws the question.
    asked_if_working: bool
    # The service credits proposed for this contact, one per outage, in the order proposed:
    # action id and key, the amount as frozen in the proposal, and whether it is still
    # unsettled. A contact is not closed while a credit of its own is unsettled.
    credits: list[dict[str, Any]]
    close_when_credit_settles: bool
    # A technician visit for this contact: the slots on offer, the one the customer chose, the
    # booking action and the appointment. ``status`` is one of the VISIT_* values below.
    visit: dict[str, Any] | None
    # Guide steps the customer has been given (the citation). Given once; if they do not help,
    # a person takes over rather than the same steps being repeated.
    guidance_told: str | None


VISIT_OFFERED = "OFFERED"  # slots were listed; the customer has not chosen yet
VISIT_CHOSEN = "CHOSEN"  # the customer chose a slot; nothing is proposed yet
VISIT_BOOKING = "BOOKING"  # the booking is with the executor
VISIT_BOOKED = "BOOKED"
VISIT_DECLINED = "DECLINED"


def visit_status(state: ContactState) -> str | None:
    return (state.get("visit") or {}).get("status")


def pending_credits(state: ContactState) -> list[dict[str, Any]]:
    """The credits of this contact whose outcome the customer has not been told yet."""
    return [credit for credit in state.get("credits") or [] if credit.get("pending")]
