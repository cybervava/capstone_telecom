"""Per-contact inbox: every event that should wake a contact's workflow.

Exactly-once *application* is guaranteed by the cursor the workflow keeps in its checkpointed
state. ``applied_at`` here is only the scheduling hint that tells workers what still needs a run.

The cursor is a sequence number, so sequence order must equal commit order for one contact:
otherwise an event that commits late with a lower number would sit behind the cursor and never
be seen. ``append_event`` guarantees that by taking a row lock on the contact before inserting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection

# Longest wait between attempts for a contact whose runs keep failing.
MAX_RETRY_DELAY_SECONDS = 300.0
# Events the feed consumer queues for conversations waiting on a node. The payload carries only
# the outage id: the workflow re-reads the evidence itself before it says anything.
# How long the count of the backlog may take before it is abandoned (milliseconds).
BACKLOG_TIMEOUT_MS = 2000
OUTAGE_DECLARED_EVENT = "outage.declared"
OUTAGE_RESOLVED_EVENT = "outage.resolved"


@dataclass(frozen=True)
class InboxEvent:
    seq: int
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class FailureOutcome:
    attempts: int
    dead_lettered: bool


@dataclass(frozen=True)
class Backlog:
    """What is waiting for the orchestrator: how many contacts, and the longest wait."""

    contacts: int
    oldest_seconds: float


async def append_event(
    conn: Connection,
    contact_id: UUID,
    event_type: str,
    *,
    dedupe_key: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Queue an event inside the caller's transaction. False when it was already queued.

    Must run inside a transaction: the row lock taken here is what serialises writers for one
    contact, and it has to be held until the insert commits.
    """
    await conn.execute(
        "SELECT 1 FROM contacts WHERE contact_id = %s FOR NO KEY UPDATE", (contact_id,)
    )
    cursor = await conn.execute(
        "INSERT INTO inbox (contact_id, event_type, payload, dedupe_key)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT ON CONSTRAINT inbox_dedupe DO NOTHING"
        " RETURNING seq",
        (contact_id, event_type, Jsonb(payload or {}), dedupe_key),
    )
    return await cursor.fetchone() is not None


class InboxRepository(Repository):
    async def add(
        self,
        contact_id: UUID,
        event_type: str,
        *,
        dedupe_key: str,
        payload: dict[str, Any] | None = None,
        conn: Connection | None = None,
    ) -> bool:
        """Queue an event. Returns False when the same event was already queued."""
        if conn is not None:
            return await append_event(
                conn, contact_id, event_type, dedupe_key=dedupe_key, payload=payload
            )
        async with self._db.transaction() as own:
            return await append_event(
                own, contact_id, event_type, dedupe_key=dedupe_key, payload=payload
            )

    async def pending_contacts(self, limit: int) -> list[UUID]:
        """Contacts with unapplied events that are due for a run, longest-waiting first."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT contact_id FROM inbox"
                " WHERE applied_at IS NULL AND NOT dead"
                " GROUP BY contact_id"
                " HAVING coalesce(max(retry_after), '-infinity') <= clock_timestamp()"
                " ORDER BY min(seq) LIMIT %s",
                (limit,),
            )
            return [row["contact_id"] for row in await cursor.fetchall()]

    async def backlog(self) -> Backlog:
        """The contacts that are due for a run right now, and how long the longest has been.

        A contact held for a retry is not waiting until its hold ends, and then only since it
        ended: a run that failed and is tried again later is not the orchestrator being behind.
        A contact whose run is in progress counts for as long as the run takes; its customer is
        waiting all the same.

        The count is longest to make just when the database has least to spare, so it gives up
        after two seconds rather than hold a connection of the caller's pool.
        """
        async with self._db.transaction() as conn:
            await conn.execute(f"SET LOCAL statement_timeout = {BACKLOG_TIMEOUT_MS}")
            cursor = await conn.execute(
                "SELECT count(*) AS contacts,"
                "       coalesce(extract(epoch FROM clock_timestamp() - min(due_since)), 0)"
                "         AS oldest_seconds"
                "  FROM (SELECT greatest(min(created_at),"
                "                        coalesce(max(retry_after), '-infinity')) AS due_since"
                "          FROM inbox WHERE applied_at IS NULL AND NOT dead"
                "         GROUP BY contact_id) waiting"
                " WHERE due_since <= clock_timestamp()"
            )
            row = await cursor.fetchone()
        if row is None:  # pragma: no cover - an aggregate always returns a row
            return Backlog(contacts=0, oldest_seconds=0.0)
        return Backlog(
            contacts=int(row["contacts"]), oldest_seconds=max(0.0, float(row["oldest_seconds"]))
        )

    async def fetch_after(self, contact_id: UUID, after_seq: int) -> list[InboxEvent]:
        """Events newer than the workflow's cursor, in order."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT seq, event_type, payload FROM inbox"
                " WHERE contact_id = %s AND seq > %s AND NOT dead ORDER BY seq",
                (contact_id, after_seq),
            )
            return [InboxEvent(**row) for row in await cursor.fetchall()]

    async def fetch_unapplied(self, contact_id: UUID) -> list[InboxEvent]:
        """Events no run has consumed yet, in order."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT seq, event_type, payload FROM inbox"
                " WHERE contact_id = %s AND applied_at IS NULL AND NOT dead ORDER BY seq",
                (contact_id,),
            )
            return [InboxEvent(**row) for row in await cursor.fetchall()]

    async def mark_applied(
        self, contact_id: UUID, up_to_seq: int, *, conn: Connection | None = None
    ) -> None:
        async with self._conn(conn) as active:
            await active.execute(
                "UPDATE inbox SET applied_at = clock_timestamp()"
                " WHERE contact_id = %s AND seq <= %s AND applied_at IS NULL",
                (contact_id, up_to_seq),
            )

    async def record_failure(
        self,
        contact_id: UUID,
        error: str,
        *,
        max_attempts: int,
        backoff_seconds: float,
        conn: Connection | None = None,
    ) -> FailureOutcome:
        """Count a failed run and schedule the next attempt with exponential back-off.

        When the attempts are used up the contact's pending events are dead-lettered. Callers
        pass ``conn`` to do that in the same transaction as the handoff that must accompany it.
        """
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "UPDATE inbox SET attempts = attempts + 1, last_error = %s,"
                " retry_after = clock_timestamp() + make_interval(secs =>"
                "   least(%s * power(2, attempts), %s))"
                " WHERE contact_id = %s AND applied_at IS NULL AND NOT dead RETURNING attempts",
                (error[:2000], backoff_seconds, MAX_RETRY_DELAY_SECONDS, contact_id),
            )
            attempts = max((row["attempts"] for row in await cursor.fetchall()), default=0)
            if attempts < max_attempts:
                return FailureOutcome(attempts, dead_lettered=False)
            await active.execute(
                "UPDATE inbox SET dead = true WHERE contact_id = %s AND applied_at IS NULL",
                (contact_id,),
            )
            return FailureOutcome(attempts, dead_lettered=True)

    async def count_dead(self) -> int:
        """Dead-lettered events: each one is a customer event no workflow will process."""
        async with self._db.connection() as conn:
            cursor = await conn.execute("SELECT count(*) AS n FROM inbox WHERE dead")
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0
