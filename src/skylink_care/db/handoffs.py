"""Human handoff queue. A contact has at most one open handoff per reason at a time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
                "SELECT handoff_id, contact_id, reason, summary, priority, status, created_at"
                " FROM handoffs WHERE status = 'OPEN' ORDER BY priority, created_at LIMIT %s",
                (limit,),
            )
            return [Handoff(**row) for row in await cursor.fetchall()]
