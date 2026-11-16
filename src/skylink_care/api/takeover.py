"""A person takes a conversation over from the assistant.

The workflow hands a conversation to a person when it cannot go on. This is the other
direction: an agent or a supervisor who sees a conversation the assistant is still handling
takes it, for a reason they give. From then on the assistant does nothing more in it: what
the customer writes reaches the staff queue, and the outcome of anything already under way (a
credit awaiting its approval, a booking being made) is still told to the customer.

The takeover, the item on the staff queue, the message that tells the customer and the entry
in the audit trail are one transaction. It is made under the lock a turn of the workflow
holds, so that a turn in progress cannot put the conversation back into the assistant's hands.

The customer is told that a person has their conversation, not who: the name a member of
staff signs in with is in the audit trail and on the staff queue, and nowhere a customer
reads.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import StringConstraints

from skylink_care.agents import replies
from skylink_care.api.auth import Principal, Role, require
from skylink_care.api.schemas import _In, ok
from skylink_care.db.actions import TERMINAL, ActionRepository
from skylink_care.db.audit import AuditEntry, AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.messages import MessageRepository
from skylink_care.db.timers import TimerRepository
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.orchestrator.outgoing import store_notice

TAKERS = (Role.AGENT, Role.SUPERVISOR)
STAFF_TOOK_OVER = "STAFF_TOOK_OVER"
# Statuses in which there is nothing to take over: a person has it, or it is finished.
_NOT_THE_ASSISTANTS = {
    "HANDOFF": "a person already has this conversation",
    "RESOLVED": "this conversation is finished",
    "CLOSED": "this conversation is finished",
}
# Who a message stored by a takeover is from, as a customer sees it.
FROM_STAFF = "staff"


class TakeoverIn(_In):
    # Why the person is taking it. Kept with the item and, with contact details masked, in the
    # audit trail: an override nobody can explain afterwards is not an override on record.
    reason: Annotated[str, StringConstraints(min_length=5, max_length=500, strip_whitespace=True)]


def register_takeover_routes(app: FastAPI, database: Database) -> None:
    contacts = ContactRepository(database)
    handoffs = HandoffRepository(database)
    messages = MessageRepository(database)
    actions = ActionRepository(database)
    timers = TimerRepository(database)
    audit = AuditRepository(database)

    @app.post("/v1/contacts/{contact_id}/takeover")
    async def take_over(
        contact_id: UUID,
        body: TakeoverIn,
        principal: Annotated[Principal, Depends(require(*TAKERS))],
    ) -> dict[str, Any]:
        async with database.contact_lock(contact_id) as locked:
            if not locked:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "the assistant is answering in this conversation right now; try again"
                    " in a moment",
                )
            # What the assistant had set going goes on without it: the person must know.
            # Read before the transaction opens, so that a takeover never holds one
            # connection of the pool while it waits for another.
            under_way = [
                f"{action.action_type} ({action.status.value})"
                for action in await actions.list_for_contact(contact_id)
                if action.status not in TERMINAL
            ]
            async with database.transaction() as conn:
                contact = await contacts.get(contact_id, conn=conn)
                if contact is None:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
                if contact.status in _NOT_THE_ASSISTANTS:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, _NOT_THE_ASSISTANTS[contact.status]
                    )
                reason = str(redact_for_log(body.reason))
                await handoffs.open(
                    contact_id,
                    reason=STAFF_TOOK_OVER,
                    summary=(
                        f"Reason: {STAFF_TOOK_OVER}. Taken over by {principal.subject}"
                        f" ({principal.role.value}) while the assistant was handling it"
                        f" (status {contact.status}). Their reason: {reason}"
                        " Still under way and not stopped by this:"
                        f" {', '.join(under_way) or 'nothing'}."
                    ),
                    priority=2,
                    conn=conn,
                )
                await contacts.update(contact_id, conn=conn, status="HANDOFF")
                await store_notice(
                    messages,
                    contact_id,
                    replies.taken_over(contact.complaint_ref),
                    author=FROM_STAFF,
                    dedupe_key=f"takeover:{contact_id}",
                    conn=conn,
                )
                await audit.append(
                    AuditEntry(
                        correlation_id=contact.correlation_id,
                        contact_id=contact_id,
                        actor=f"staff:{principal.subject}",
                        step="staff.takeover",
                        outcome=contact.status,
                        inputs={"role": principal.role.value},
                        outputs={"reason": reason, "under_way": under_way},
                    ),
                    conn=conn,
                )
            # The assistant's own wake-ups for this conversation have nothing left to do.
            await timers.cancel(contact_id)
        return ok(
            {"contact_id": str(contact_id), "status": "HANDOFF", "taken_by": principal.subject}
        )
