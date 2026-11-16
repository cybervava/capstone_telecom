"""The one way a message of a conversation is stored: after what it claims has been checked.

A workflow step that acts on what it is about to say (proposes a credit, raises a ticket,
proposes a booking) asks first with ``must_be_backed``, so that nothing is done on the strength
of a message that will not be sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from skylink_care.agents.replies import Reply
from skylink_care.db.contacts import Contact
from skylink_care.db.database import Connection
from skylink_care.db.messages import MessageRepository
from skylink_care.guardrails.claims import (
    CLAIMS,
    Claim,
    UnbackedClaimError,
    Violation,
    check,
    unbacked,
)

if TYPE_CHECKING:
    # Only named in signatures: the API stores a notice through this module without
    # loading the workflow.
    from skylink_care.runtime import OrchestratorRuntime

ACTOR = "guardrail:claims"


async def must_be_backed(
    rt: OrchestratorRuntime,
    contact: Contact,
    claim: Claim,
    *,
    template_id: str,
    network: Mapping[str, Any] | None,
    outage_id: str | None = None,
    resolution_reported: bool = True,
) -> None:
    """Stop the step now if the network decision of the turn does not back what it is about
    to say. ``template_id`` names the message the step would send."""
    code = unbacked(
        claim,
        network,
        now=rt.now(),
        max_age=_max_age(rt),
        outage_id=outage_id,
        resolution_reported=resolution_reported,
    )
    if code is not None:
        state = str(network.get("state")) if network else None
        await _refuse(rt, contact, Violation(code, template_id, claim.value, state), network)


async def store_reply(
    rt: OrchestratorRuntime,
    contact: Contact,
    reply: Reply,
    *,
    author: str,
    dedupe_key: str,
    network: Mapping[str, Any] | None = None,
    conn: Connection | None = None,
) -> None:
    """Store one outgoing message, unless it claims something about the network that the
    evidence does not back.

    ``network`` is the network decision of the turn. A message stored outside a turn (the
    outcome of a credit or a booking, an apology) has none, and may claim nothing.
    """
    violation = check(reply, network, now=rt.now(), max_age=_max_age(rt))
    if violation is not None:
        await _refuse(rt, contact, violation, network)
    await rt.messages.add(
        contact.contact_id,
        direction="outbound",
        author=author,
        body=reply.text,
        dedupe_key=dedupe_key,
        meta={"template_id": reply.template_id, "facts": reply.facts},
        conn=conn,
    )


async def store_notice(
    messages: MessageRepository,
    contact_id: UUID,
    notice: Reply,
    *,
    author: str,
    dedupe_key: str,
    conn: Connection | None = None,
) -> None:
    """Store a message that says nothing about the network, from outside a conversation's
    turn: what the platform tells a customer when a person takes their conversation over.

    It is refused for any template that claims something: there is no evidence here to hold
    a claim against, and this door must not become the way round the check.
    """
    if CLAIMS.get(notice.template_id) is not Claim.NOTHING:
        raise ValueError(f"{notice.template_id} claims something and cannot be a notice")
    await messages.add(
        contact_id,
        direction="outbound",
        author=author,
        body=notice.text,
        dedupe_key=dedupe_key,
        meta={"template_id": notice.template_id, "facts": notice.facts},
        conn=conn,
    )


def _max_age(rt: OrchestratorRuntime) -> timedelta:
    return timedelta(seconds=rt.settings.status_max_age_seconds)


async def _refuse(
    rt: OrchestratorRuntime,
    contact: Contact,
    violation: Violation,
    network: Mapping[str, Any] | None,
) -> None:
    """Record what was stopped and raise. On its own connection: the record must survive
    whatever the caller rolls back."""
    await rt.auditor(contact.contact_id, contact.correlation_id).record(
        actor=ACTOR,
        step="message.blocked",
        outcome=violation.code,
        inputs={"template_id": violation.template_id, "claim": violation.claim},
        outputs={
            "network_state": violation.network_state,
            "network_reason": (network or {}).get("reason"),
            "decided_at": (network or {}).get("decided_at"),
        },
    )
    raise UnbackedClaimError(violation)
