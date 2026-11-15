"""The staff queue: what the workflow has passed to a person, and closing an item of it.

Staff see every open item with the complaint it belongs to. An agent or a supervisor closes an
item once they have dealt with it; who did, and what they noted, goes into the contact's audit
trail in the same transaction. The note is free text from a person: it is kept as written with
the item, and contact details in it are masked in the audit trail.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.encoders import jsonable_encoder

from skylink_care.api.auth import Principal, Role, require
from skylink_care.api.schemas import HandoffDoneIn, ok
from skylink_care.clock import Clock
from skylink_care.db.audit import AuditEntry, AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import ClosureOutcome, HandoffRepository
from skylink_care.guardrails.pii import redact_for_log

STAFF = (Role.AGENT, Role.SUPERVISOR, Role.OPS)
CLOSERS = (Role.AGENT, Role.SUPERVISOR)


def register_handoff_routes(app: FastAPI, database: Database, now: Clock) -> None:
    handoffs = HandoffRepository(database)
    contacts = ContactRepository(database)
    audit = AuditRepository(database)

    @app.get("/v1/handoffs")
    async def list_handoffs(
        _: Annotated[Principal, Depends(require(*STAFF))],
    ) -> dict[str, Any]:
        return ok(jsonable_encoder([item.__dict__ for item in await handoffs.list_open()]))

    @app.post("/v1/handoffs/{handoff_id}/done")
    async def close_handoff(
        handoff_id: UUID,
        body: HandoffDoneIn,
        principal: Annotated[Principal, Depends(require(*CLOSERS))],
    ) -> dict[str, Any]:
        """A person has dealt with an item of the queue. Recorded with who, once."""
        async with database.transaction() as conn:
            closure = await handoffs.close(
                conn, handoff_id, closed_by=principal.subject, note=body.note, now=now()
            )
            if closure.outcome is ClosureOutcome.NOT_FOUND or closure.handoff is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "handoff not found")
            if closure.outcome is ClosureOutcome.ALREADY_DONE:
                raise HTTPException(status.HTTP_409_CONFLICT, "this item is already closed")
            closed = closure.handoff
            contact = await contacts.get(closed.contact_id, conn=conn)
            await audit.append(
                AuditEntry(
                    correlation_id=contact.correlation_id if contact else str(handoff_id),
                    contact_id=closed.contact_id,
                    actor=f"staff:{principal.subject}",
                    step="handoff.done",
                    outcome=closed.reason,
                    inputs={"handoff_id": str(handoff_id), "role": principal.role.value},
                    outputs={"note": str(redact_for_log(body.note)) if body.note else None},
                ),
                conn=conn,
            )
        return ok(jsonable_encoder(closed.__dict__))
