"""The operations queue: what needs a person and belongs to no contact.

All staff see it, so that an agent can tell a customer why they were not told of an outage.
Closing an item is for those who run the platform, or a supervisor. Who closed it, and what
they noted, is recorded in the audit log; the note is kept as written with the item and
masked in the log, like every note a person writes.
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
from skylink_care.db.database import Database
from skylink_care.db.ops import OpsClosure, OpsRepository
from skylink_care.guardrails.pii import redact_for_log

STAFF = (Role.AGENT, Role.SUPERVISOR, Role.OPS)
CLOSERS = (Role.OPS, Role.SUPERVISOR)
# The page shows this many. The total is given with them, so that a longer queue is not
# mistaken for a short one.
LIST_LIMIT = 100


def register_ops_routes(app: FastAPI, database: Database, now: Clock) -> None:
    ops = OpsRepository(database)
    audit = AuditRepository(database)

    @app.get("/v1/ops")
    async def list_ops(_: Annotated[Principal, Depends(require(*STAFF))]) -> dict[str, Any]:
        """The most urgent open items, and how many are open in all."""
        items = await ops.list_open(limit=LIST_LIMIT)
        total = await ops.count_open()
        return ok({"items": jsonable_encoder([item.__dict__ for item in items]), "total": total})

    @app.post("/v1/ops/{item_id}/done")
    async def close_ops_item(
        item_id: UUID,
        body: HandoffDoneIn,
        principal: Annotated[Principal, Depends(require(*CLOSERS))],
    ) -> dict[str, Any]:
        """A person has dealt with an item of the queue. Recorded with who, once."""
        async with database.transaction() as conn:
            closed = await ops.close(
                conn, item_id, closed_by=principal.subject, note=body.note, now=now()
            )
            if closed.outcome is OpsClosure.NOT_FOUND or closed.item is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
            if closed.outcome is OpsClosure.ALREADY_DONE:
                raise HTTPException(status.HTTP_409_CONFLICT, "this item is already closed")
            await audit.append(
                AuditEntry(
                    correlation_id=str(item_id),
                    actor=f"staff:{principal.subject}",
                    step="ops.done",
                    outcome=closed.item.kind,
                    inputs={
                        "item_id": str(item_id),
                        "subject": closed.item.subject,
                        "role": principal.role.value,
                    },
                    outputs={"note": str(redact_for_log(body.note)) if body.note else None},
                ),
                conn=conn,
            )
        return ok(jsonable_encoder(closed.item.__dict__))
