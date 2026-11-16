"""The operations queue: what needs a person and belongs to no contact.

The staff queue of handoffs is for conversations. This one is for the platform itself: a write
that failed with no customer waiting for it, a feed that cannot go on by itself. One open item
stands for one kind of trouble with one subject. Seeing it again counts on the item, so that a
gateway API that is down for an hour is one line for a person to read, not a thousand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection

MAX_SUMMARY_CHARS = 2000
# What an item carries besides its summary is for finding the trouble, not for keeping it:
# a handful of short facts.
MAX_DETAILS = 20
MAX_DETAIL_CHARS = 500
DONE = "DONE"
_COLUMNS = (
    "item_id, kind, subject, summary, details, priority, status, occurrences, created_at,"
    " last_seen_at, closed_at, closed_by, closed_note"
)


@dataclass(frozen=True)
class OpsItem:
    item_id: UUID
    kind: str
    subject: str
    summary: str
    details: dict[str, Any]
    priority: int
    status: str
    occurrences: int
    created_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None = None
    closed_by: str | None = None
    closed_note: str | None = None


class OpsClosure(StrEnum):
    CLOSED = "CLOSED"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_DONE = "ALREADY_DONE"


@dataclass(frozen=True)
class Closed:
    outcome: OpsClosure
    item: OpsItem | None = None


class OpsRepository(Repository):
    async def raise_item(
        self,
        *,
        kind: str,
        subject: str,
        summary: str,
        details: dict[str, Any] | None = None,
        priority: int = 3,
        conn: Connection | None = None,
    ) -> bool:
        """Queue the trouble for a person, or count it on the item already open for it.

        True when a new item was queued. An open item keeps the summary and the details it
        was raised with, which say how the trouble began; what was seen last is kept beside
        them under ``latest``.
        """
        kept = _bounded(details or {})
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "INSERT INTO ops_items (item_id, kind, subject, summary, details, priority)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (kind, subject) WHERE status = 'OPEN' DO UPDATE"
                " SET occurrences = ops_items.occurrences + 1, last_seen_at = clock_timestamp(),"
                "     details = jsonb_set(ops_items.details, '{latest}', EXCLUDED.details),"
                "     priority = least(ops_items.priority, EXCLUDED.priority)"
                " RETURNING occurrences",
                (uuid4(), kind, subject, summary[:MAX_SUMMARY_CHARS], Jsonb(kept), priority),
            )
            row = await cursor.fetchone()
            return row is not None and row["occurrences"] == 1

    async def open_subjects(
        self, kind: str, *, seen_within_seconds: int, limit: int = 1000
    ) -> list[str]:
        """What the open items of one kind are about, of those seen within this long. An
        item nobody has closed and nothing has touched since is not counted as current."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT subject FROM ops_items WHERE kind = %s AND status = 'OPEN'"
                " AND last_seen_at >= statement_timestamp() - make_interval(secs => %s)"
                " ORDER BY created_at LIMIT %s",
                (kind, seen_within_seconds, limit),
            )
            return [row["subject"] for row in await cursor.fetchall()]

    async def count_open(self) -> int:
        """How many items are open in all: a list of them is always cut somewhere."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS open FROM ops_items WHERE status = 'OPEN'"
            )
            row = await cursor.fetchone()
            return int(row["open"]) if row else 0

    async def list_open(self, *, limit: int = 100) -> list[OpsItem]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM ops_items WHERE status = 'OPEN'"  # noqa: S608
                " ORDER BY priority, created_at LIMIT %s",
                (limit,),
            )
            return [OpsItem(**row) for row in await cursor.fetchall()]

    async def close(
        self,
        conn: Connection,
        item_id: UUID,
        *,
        closed_by: str,
        note: str | None,
        now: datetime,
    ) -> Closed:
        """Mark an item done, once, in the caller's transaction."""
        cursor = await conn.execute(
            f"SELECT {_COLUMNS} FROM ops_items WHERE item_id = %s FOR UPDATE",  # noqa: S608
            (item_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return Closed(OpsClosure.NOT_FOUND)
        if row["status"] == DONE:
            return Closed(OpsClosure.ALREADY_DONE, OpsItem(**row))
        await conn.execute(
            "UPDATE ops_items SET status = %s, closed_at = %s, closed_by = %s, closed_note = %s"
            " WHERE item_id = %s",
            (DONE, now, closed_by, note, item_id),
        )
        closed = {**row, "status": DONE, "closed_at": now, "closed_by": closed_by}
        return Closed(OpsClosure.CLOSED, OpsItem(**{**closed, "closed_note": note}))


def _bounded(details: dict[str, Any]) -> dict[str, Any]:
    """The details cut to a handful of short facts, whatever a producer passed in."""
    kept: dict[str, Any] = {}
    for key, value in list(details.items())[:MAX_DETAILS]:
        if isinstance(value, bool | int | float) or value is None:
            kept[str(key)[:MAX_DETAIL_CHARS]] = value
        else:
            kept[str(key)[:MAX_DETAIL_CHARS]] = str(value)[:MAX_DETAIL_CHARS]
    return kept
