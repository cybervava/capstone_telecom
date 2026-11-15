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
    RESTORED = "restored"
    KEEP_WAITING = "keep_waiting"
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
    # "Service restored" has been said: a reply that it works now closes the contact.
    restoration_told: bool
