"""Timers: wake a contact's workflow at a later time.

``fire_due`` turns each due timer into one inbox event in the same transaction that marks it
fired, so a timer fires exactly once even with several workers. Callers pass the current time
in, which keeps the clock injectable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection
from skylink_care.db.inbox import append_event

TIMER_FIRED_EVENT = "timer.fired"
# Timer kinds.
RECHECK_NETWORK = "recheck_network"
CLOSE_QUIET = "close_quiet"


class TimerRepository(Repository):
    async def schedule(
        self,
        contact_id: UUID,
        kind: str,
        *,
        fire_at: datetime,
        dedupe_key: str,
        conn: Connection | None = None,
    ) -> bool:
        """Arm a timer. Arming the same key again changes nothing, except that a timer which was
        cancelled before it fired is armed again: a workflow step that cancels the contact's
        timers and then arms this one must end with it armed however often the step is repeated.

        Returns False when the timer has already fired.
        """
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "INSERT INTO timers (contact_id, kind, fire_at, dedupe_key)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT ON CONSTRAINT timers_dedupe DO UPDATE"
                " SET cancelled = false WHERE timers.fired_at IS NULL"
                " RETURNING timer_id",
                (contact_id, kind, fire_at, dedupe_key),
            )
            return await cursor.fetchone() is not None

    async def cancel(self, contact_id: UUID, kind: str | None = None) -> int:
        """Cancel the contact's pending timers: of one kind, or all of them."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE timers SET cancelled = true"
                " WHERE contact_id = %s AND (%s::text IS NULL OR kind = %s)"
                " AND fired_at IS NULL AND NOT cancelled",
                (contact_id, kind, kind),
            )
            return cursor.rowcount

    async def fire_due(self, now: datetime, *, limit: int = 200) -> int:
        """Queue an inbox event for every timer that is due. Returns how many fired."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT timer_id, contact_id, kind, dedupe_key FROM timers"
                " WHERE fired_at IS NULL AND NOT cancelled AND fire_at <= %s"
                " ORDER BY fire_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (now, limit),
            )
            due = await cursor.fetchall()
            # Contact rows are locked in id order here and by the feed consumer, so the two can
            # never wait on each other in a circle.
            for timer in sorted(due, key=lambda item: item["contact_id"]):
                await append_event(
                    conn,
                    timer["contact_id"],
                    TIMER_FIRED_EVENT,
                    dedupe_key=f"timer:{timer['timer_id']}",
                    # The key lets the workflow tell the timer it is waiting for from a stale one.
                    payload={"kind": timer["kind"], "key": timer["dedupe_key"]},
                )
            if due:
                await conn.execute(
                    "UPDATE timers SET fired_at = %s WHERE timer_id = ANY(%s)",
                    (now, [timer["timer_id"] for timer in due]),
                )
            return len(due)
