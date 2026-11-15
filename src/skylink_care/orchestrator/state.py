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
    REPLIED = "REPLIED"  # the customer has been answered; wait for the next event
    HANDED_OFF = "HANDED_OFF"  # a person owns the contact now
    CLOSED = "CLOSED"


class Route(StrEnum):
    TRIAGE = "triage"
    ASK_IDENTITY = "ask_identity"
    OUT_OF_SCOPE = "out_of_scope"
    DIAGNOSE = "diagnose"
    OUTAGE_REPLY = "outage_reply"
    UNKNOWN_REPLY = "unknown_reply"
    CLEAR_REPLY = "clear_reply"
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
