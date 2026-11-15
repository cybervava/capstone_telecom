"""Conversation messages for a contact. Kept out of workflow state so checkpoints stay small."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection


@dataclass(frozen=True)
class Message:
    message_id: int
    contact_id: UUID
    direction: str
    author: str
    body: str
    meta: dict[str, Any]
    created_at: datetime


class MessageRepository(Repository):
    async def add(
        self,
        contact_id: UUID,
        *,
        direction: str,
        author: str,
        body: str,
        dedupe_key: str,
        meta: dict[str, Any] | None = None,
        conn: Connection | None = None,
    ) -> bool:
        """Store a message once. A repeat with the same ``dedupe_key`` is ignored (False)."""
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "INSERT INTO contact_messages"
                " (contact_id, direction, author, body, meta, dedupe_key)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT ON CONSTRAINT contact_messages_dedupe DO NOTHING"
                " RETURNING message_id",
                (contact_id, direction, author, body, Jsonb(meta or {}), dedupe_key),
            )
            return await cursor.fetchone() is not None

    async def list_for_contact(self, contact_id: UUID, *, after_id: int = 0) -> list[Message]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT message_id, contact_id, direction, author, body, meta, created_at"
                " FROM contact_messages WHERE contact_id = %s AND message_id > %s"
                " ORDER BY message_id",
                (contact_id, after_id),
            )
            return [Message(**row) for row in await cursor.fetchall()]
