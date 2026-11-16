"""The one way a message of a conversation is stored: after what it claims has been checked.

A workflow step that acts on what it is about to say (proposes a credit, raises a ticket,
proposes a booking) asks first with ``must_be_backed``, so that nothing is done on the strength
of a message that will not be sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from skylink_care.agents.replies import Reply
from skylink_care.db.contacts import Contact
from skylink_care.db.database import Connection
from skylink_care.guardrails.claims import Claim, UnbackedClaimError, Violation, check, unbacked
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
