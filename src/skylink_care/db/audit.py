"""Append-only audit trail: who or what did each step, with what, and under which versions.

Callers pass inputs and outputs that are already redacted; this module stores what it is given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection


@dataclass(frozen=True)
class AuditEntry:
    correlation_id: str
    actor: str
    step: str
    outcome: str
    contact_id: UUID | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class AuditRecord:
    audit_id: int
    ts: datetime
    correlation_id: str
    contact_id: UUID | None
    actor: str
    step: str
    outcome: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    model: str | None
    prompt_version: str | None
    policy_version: str | None
    duration_ms: int | None


class AuditRepository(Repository):
    async def append(self, entry: AuditEntry, *, conn: Connection | None = None) -> None:
        async with self._conn(conn) as active:
            await active.execute(
                "INSERT INTO audit_log (correlation_id, contact_id, actor, step, outcome, inputs,"
                " outputs, model, prompt_version, policy_version, duration_ms)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry.correlation_id,
                    entry.contact_id,
                    entry.actor,
                    entry.step,
                    entry.outcome,
                    Jsonb(entry.inputs),
                    Jsonb(entry.outputs),
                    entry.model,
                    entry.prompt_version,
                    entry.policy_version,
                    entry.duration_ms,
                ),
            )

    async def for_contact(self, contact_id: UUID) -> list[AuditRecord]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT audit_id, ts, correlation_id, contact_id, actor, step, outcome, inputs,"
                " outputs, model, prompt_version, policy_version, duration_ms"
                " FROM audit_log WHERE contact_id = %s ORDER BY audit_id",
                (contact_id,),
            )
            return [AuditRecord(**row) for row in await cursor.fetchall()]
