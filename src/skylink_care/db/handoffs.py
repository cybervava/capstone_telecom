"""Human handoff queue. A contact has at most one open handoff per reason at a time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection


@dataclass(frozen=True)
class Handoff:
    handoff_id: UUID
    contact_id: UUID
    reason: str
    summary: str
    priority: int
    status: str
    created_at: datetime
    # For whoever works the queue: which complaint it is, and where the contact stands.
    complaint_ref: str | None = None
    contact_status: str | None = None


DONE = "DONE"


class ClosureOutcome(StrEnum):
    CLOSED = "CLOSED"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_DONE = "ALREADY_DONE"


@dataclass(frozen=True)
class Closure:
    outcome: ClosureOutcome
    handoff: Handoff | None = None


class HandoffRepository(Repository):
    async def open(
        self,
        contact_id: UUID,
        *,
        reason: str,
        summary: str,
        priority: int = 5,
        conn: Connection | None = None,
    ) -> bool:
        """Queue the contact for a human. False when it is already queued for this reason."""
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "INSERT INTO handoffs (handoff_id, contact_id, reason, summary, priority)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (contact_id, reason) WHERE status <> 'DONE' DO NOTHING"
                " RETURNING handoff_id",
                (uuid4(), contact_id, reason, summary, priority),
            )
            return await cursor.fetchone() is not None

    async def list_open(self, *, limit: int = 100) -> list[Handoff]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT h.handoff_id, h.contact_id, h.reason, h.summary, h.priority, h.status,"
                " h.created_at, c.complaint_ref, c.status AS contact_status"
                " FROM handoffs h JOIN contacts c USING (contact_id)"
                " WHERE h.status = 'OPEN' ORDER BY h.priority, h.created_at LIMIT %s",
                (limit,),
            )
            return [Handoff(**row) for row in await cursor.fetchall()]

    async def close(
        self,
        conn: Connection,
        handoff_id: UUID,
        *,
        closed_by: str,
        note: str | None,
        now: datetime,
    ) -> Closure:
        """Mark an item of the queue done, once, in the caller's transaction."""
        cursor = await conn.execute(
            "SELECT handoff_id, contact_id, reason, summary, priority, status, created_at"
            " FROM handoffs WHERE handoff_id = %s FOR UPDATE",
            (handoff_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return Closure(ClosureOutcome.NOT_FOUND)
        if row["status"] == DONE:
            return Closure(ClosureOutcome.ALREADY_DONE, Handoff(**row))
        await conn.execute(
            "UPDATE handoffs SET status = %s, closed_at = %s, closed_by = %s, closed_note = %s"
            " WHERE handoff_id = %s",
            (DONE, now, closed_by, note, handoff_id),
        )
        return Closure(ClosureOutcome.CLOSED, Handoff(**{**row, "status": DONE}))
