"""Action ledger: every write to an enterprise system is proposed here before it happens.

Three properties are enforced in this module rather than left to callers:

* **Identity.** ``action_key`` is unique. Proposing the same effect twice returns the first row.
* **Frozen requests.** ``request_body`` is stored at proposal time and never rewritten, so every
  retry sends byte-for-byte what was proposed (and, where required, approved).
* **Single claim.** ``claim_runnable`` hands each action to one executor at a time; a crashed
  executor's claim lapses and the action is replayed under the same key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection
from skylink_care.db.inbox import append_event
from skylink_care.hashing import canonical_hash

_COLUMNS = (
    "action_id, action_key, action_type, lane, contact_id, episode_id, requested_by,"
    " request_body, request_hash, inputs, depends_on, approval_level, status, policy_version,"
    " external_ref, response_body, attempts, available_at, last_error, correlation_id,"
    " created_at, updated_at, ready_at"
)
_RETURNING = ", ".join(f"actions.{name.strip()}" for name in _COLUMNS.split(","))

ACTION_FINISHED_EVENT = "action.finished"
# How the error of an action begins when the executor stopped replaying it. Whether the write
# landed is then not known, which is not the same as knowing that it did not.
GAVE_UP = "GAVE_UP"

# Action types: the vocabulary shared by whoever proposes a write and the executor that sends it.
COMPLAINT_REGISTER = "COMPLAINT_REGISTER"
INCIDENT_OPEN = "INCIDENT_OPEN"
INCIDENT_UPDATE = "INCIDENT_UPDATE"
INCIDENT_LINK_COMPLAINT = "INCIDENT_LINK_COMPLAINT"
NOTIFY_SEND = "NOTIFY_SEND"
CREDIT_APPLY = "CREDIT_APPLY"
VISIT_BOOK = "VISIT_BOOK"
# The constraint that allows one visit booking in progress per service (migration 0007).
ONE_OPEN_VISIT = "actions_one_open_visit"

# Lanes: a lower lane is always served first, so a flood of low-priority writes (notifications)
# can never delay complaint registration or incident tickets.
REGULATORY_LANE = 1
INCIDENT_LANE = 2
LINK_LANE = 3
VISIT_LANE = 3
CREDIT_LANE = 4
NOTIFICATION_LANE = 9


class ActionStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    READY = "READY"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"  # deliberately not performed (e.g. no consent at send time)
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"


TERMINAL = frozenset(
    {
        ActionStatus.SUCCEEDED,
        ActionStatus.SKIPPED,
        ActionStatus.FAILED,
        ActionStatus.REJECTED,
        ActionStatus.EXPIRED,
        ActionStatus.BLOCKED,
    }
)
OPEN = frozenset({ActionStatus.PENDING_APPROVAL, ActionStatus.READY, ActionStatus.IN_FLIGHT})


@dataclass(frozen=True)
class NewAction:
    action_key: str
    action_type: str
    requested_by: str
    request_body: dict[str, Any]
    correlation_id: str
    lane: int = 5
    contact_id: UUID | None = None
    episode_id: UUID | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: UUID | None = None
    approval_level: str = "none"
    policy_version: str | None = None


@dataclass(frozen=True)
class Action:
    action_id: UUID
    action_key: str
    action_type: str
    lane: int
    contact_id: UUID | None
    episode_id: UUID | None
    requested_by: str
    request_body: dict[str, Any]
    request_hash: str
    inputs: dict[str, Any]
    depends_on: UUID | None
    approval_level: str
    status: ActionStatus
    policy_version: str | None
    external_ref: str | None
    response_body: dict[str, Any] | None
    attempts: int
    available_at: datetime
    last_error: str | None
    correlation_id: str
    created_at: datetime
    updated_at: datetime
    # When it became runnable: at proposal, or at its approval. None while it awaits approval.
    ready_at: datetime | None = None

    @property
    def gave_up(self) -> bool:
        """The executor stopped replaying it: the write may or may not have landed."""
        return self.status is ActionStatus.FAILED and (self.last_error or "").startswith(GAVE_UP)


@dataclass(frozen=True)
class OpenCredit:
    """A credit that is proposed and not yet settled."""

    action_key: str
    amount_inr: Decimal


@dataclass(frozen=True)
class KeyedAction:
    """The little a proposer needs to know about an action it finds by key."""

    action_id: UUID
    status: ActionStatus


@dataclass(frozen=True)
class Proposal:
    action: Action
    created: bool
    # The key already existed with a different request: the caller must not treat it as its own.
    conflicting: bool


class ActionRepository(Repository):
    async def propose(self, new: NewAction, *, conn: Connection | None = None) -> Proposal:
        """Record the intent to perform an effect, or return the existing record for its key."""
        request_hash = canonical_hash(new.request_body)
        status = (
            ActionStatus.READY if new.approval_level == "none" else ActionStatus.PENDING_APPROVAL
        )
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "INSERT INTO actions (action_id, action_key, action_type, lane, contact_id,"  # noqa: S608 - constant column list
                " episode_id, requested_by, request_body, request_hash, inputs, depends_on,"
                " approval_level, status, policy_version, correlation_id, ready_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                # Runnable from the start (the last parameter says so), or not until approved.
                " CASE WHEN %s THEN clock_timestamp() END)"
                " ON CONFLICT (action_key) DO NOTHING"
                f" RETURNING {_COLUMNS}",
                (
                    uuid4(),
                    new.action_key,
                    new.action_type,
                    new.lane,
                    new.contact_id,
                    new.episode_id,
                    new.requested_by,
                    Jsonb(new.request_body),
                    request_hash,
                    Jsonb(new.inputs),
                    new.depends_on,
                    new.approval_level,
                    status.value,
                    new.policy_version,
                    new.correlation_id,
                    status is ActionStatus.READY,
                ),
            )
            row = await cursor.fetchone()
            if row is not None:
                return Proposal(_to_action(row), created=True, conflicting=False)
            existing = await _select_by_key(active, new.action_key)
        if existing is None:  # pragma: no cover - a conflicting row cannot disappear
            raise RuntimeError(f"action {new.action_key} vanished during proposal")
        return Proposal(existing, created=False, conflicting=existing.request_hash != request_hash)

    async def propose_only_one_open(self, new: NewAction) -> Proposal | None:
        """Propose an action of a kind that may be open only once per subject (a visit booking
        per service, by constraint). None when another one is open already."""
        try:
            return await self.propose(new)
        except UniqueViolation as exc:
            if exc.diag.constraint_name != ONE_OPEN_VISIT:
                raise
            return None

    async def propose_many(self, conn: Connection, proposals: Sequence[NewAction]) -> int:
        """Record many independent proposals in the caller's transaction (notification fan-out).

        Existing keys are left untouched. Returns how many rows were inserted.
        """
        if not proposals:
            return 0
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO actions (action_id, action_key, action_type, lane, contact_id,"
                " episode_id, requested_by, request_body, request_hash, inputs, depends_on,"
                " approval_level, status, policy_version, correlation_id, ready_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " CASE WHEN %s THEN clock_timestamp() END)"
                " ON CONFLICT (action_key) DO NOTHING",
                [
                    (
                        uuid4(),
                        new.action_key,
                        new.action_type,
                        new.lane,
                        new.contact_id,
                        new.episode_id,
                        new.requested_by,
                        Jsonb(new.request_body),
                        canonical_hash(new.request_body),
                        Jsonb(new.inputs),
                        new.depends_on,
                        new.approval_level,
                        (
                            ActionStatus.READY
                            if new.approval_level == "none"
                            else ActionStatus.PENDING_APPROVAL
                        ).value,
                        new.policy_version,
                        new.correlation_id,
                        new.approval_level == "none",
                    )
                    for new in proposals
                ],
            )
            return cursor.rowcount

    async def lookup_keys(self, conn: Connection, keys: Sequence[str]) -> dict[str, KeyedAction]:
        """Which of these keys already have an action, and where each one stands."""
        if not keys:
            return {}
        cursor = await conn.execute(
            "SELECT action_key, action_id, status FROM actions WHERE action_key = ANY(%s)",
            (list(keys),),
        )
        return {
            row["action_key"]: KeyedAction(row["action_id"], ActionStatus(row["status"]))
            for row in await cursor.fetchall()
        }

    @asynccontextmanager
    async def customer_lock(self, customer_id: str) -> AsyncIterator[None]:
        """Serialise money-moving writes for one customer, across every executor.

        Held for the check-then-write of one credit, so two credits for the same customer can
        never both pass the "not credited yet" check. PostgreSQL releases it when the block
        ends or the process dies.
        """
        async with self._db.transaction() as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"customer:{customer_id}",),
            )
            yield

    async def open_credits(self, service_id: str) -> list[OpenCredit]:
        """Credits on this service that are proposed and not yet settled, whoever proposed them."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT action_key, request_body ->> 'amount_inr' AS amount FROM actions"
                " WHERE action_type = %s AND request_body ->> 'service_id' = %s"
                " AND status IN ('PENDING_APPROVAL', 'READY', 'IN_FLIGHT')",
                (CREDIT_APPLY, service_id),
            )
            rows = await cursor.fetchall()
        return [OpenCredit(row["action_key"], _amount(row["amount"])) for row in rows]

    async def get(self, action_id: UUID) -> Action | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM actions WHERE action_id = %s",  # noqa: S608
                (action_id,),
            )
            row = await cursor.fetchone()
        return _to_action(row) if row else None

    async def get_by_key(self, action_key: str) -> Action | None:
        async with self._db.connection() as conn:
            return await _select_by_key(conn, action_key)

    async def list_for_contact(self, contact_id: UUID) -> list[Action]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM actions"  # noqa: S608 - constant column list
                " WHERE contact_id = %s ORDER BY created_at",
                (contact_id,),
            )
            return [_to_action(row) for row in await cursor.fetchall()]

    async def claim_runnable(self, *, limit: int, visibility_seconds: float) -> list[Action]:
        """Take up to ``limit`` actions to execute, most urgent lane first.

        A claimed action is hidden for ``visibility_seconds``. If the executor dies it becomes
        claimable again and is replayed under the same key; it is never reset to READY, because
        the earlier attempt may have reached the enterprise system.
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "WITH picked AS ("  # noqa: S608 - constant column list
                "  SELECT a.action_id FROM actions a"
                "  LEFT JOIN actions dep ON dep.action_id = a.depends_on"
                "  WHERE a.status IN ('READY', 'IN_FLIGHT') AND a.available_at <= clock_timestamp()"
                "    AND (a.depends_on IS NULL OR dep.status = 'SUCCEEDED')"
                "  ORDER BY a.lane, a.created_at LIMIT %s FOR UPDATE OF a SKIP LOCKED)"
                " UPDATE actions SET status = 'IN_FLIGHT', attempts = actions.attempts + 1,"
                "   available_at = clock_timestamp() + make_interval(secs => %s),"
                "   updated_at = clock_timestamp()"
                " FROM picked WHERE actions.action_id = picked.action_id"
                f" RETURNING {_RETURNING}",
                (limit, visibility_seconds),
            )
            claimed = [_to_action(row) for row in await cursor.fetchall()]
        return sorted(claimed, key=lambda action: (action.lane, action.created_at))

    async def finish(
        self,
        action_id: UUID,
        status: ActionStatus,
        *,
        external_ref: str | None = None,
        response_body: dict[str, Any] | None = None,
        error: str | None = None,
        wake_contact: bool = False,
    ) -> bool:
        """Record a terminal outcome exactly once and, if asked, wake the originating contact.

        Returns False when the action had already finished (a late duplicate result).
        """
        if status not in TERMINAL:
            raise ValueError(f"{status} is not a terminal status")
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE actions SET status = %s, external_ref = COALESCE(%s, external_ref),"
                " response_body = %s, last_error = %s, updated_at = clock_timestamp()"
                " WHERE action_id = %s AND status IN ('READY', 'IN_FLIGHT', 'PENDING_APPROVAL')"
                " RETURNING contact_id",
                (
                    status.value,
                    external_ref,
                    Jsonb(response_body) if response_body is not None else None,
                    error[:2000] if error else None,
                    action_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            if wake_contact and row["contact_id"] is not None:
                await append_event(
                    conn,
                    row["contact_id"],
                    ACTION_FINISHED_EVENT,
                    dedupe_key=f"action:{action_id}",
                    payload={"action_id": str(action_id), "status": status.value},
                )
            return True

    async def defer(self, action_id: UUID, *, delay_seconds: float, error: str) -> bool:
        """Outcome unknown or dependency unavailable: try the identical request again later.

        Returns False when the action was no longer in flight (someone finished it meanwhile).
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE actions SET available_at = clock_timestamp() + make_interval(secs => %s),"
                " last_error = %s, updated_at = clock_timestamp()"
                " WHERE action_id = %s AND status = 'IN_FLIGHT'",
                (delay_seconds, error[:2000], action_id),
            )
            return cursor.rowcount == 1

    async def block_orphans(self) -> list[Action]:
        """Settle actions whose prerequisite can no longer succeed, and return the blocked ones.

        A prerequisite that failed blocks its dependants, and someone has to look. A prerequisite
        that was deliberately skipped skips them too, quietly: a customer who was not told an
        outage began is not told that it ended.
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE actions SET"  # noqa: S608 - constant column list
                " status = CASE WHEN dep.status = 'SKIPPED' THEN 'SKIPPED' ELSE 'BLOCKED' END,"
                " updated_at = clock_timestamp(),"
                " last_error = 'prerequisite action ' || dep.status"
                " FROM actions dep WHERE dep.action_id = actions.depends_on"
                " AND actions.status IN ('READY', 'PENDING_APPROVAL')"
                " AND dep.status IN ('FAILED', 'REJECTED', 'EXPIRED', 'BLOCKED', 'SKIPPED')"
                f" RETURNING {_RETURNING}"
            )
            settled = [_to_action(row) for row in await cursor.fetchall()]
        return [action for action in settled if action.status is ActionStatus.BLOCKED]


async def _select_by_key(conn: Connection, action_key: str) -> Action | None:
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM actions WHERE action_key = %s",  # noqa: S608 - constant columns
        (action_key,),
    )
    row = await cursor.fetchone()
    return _to_action(row) if row else None


def _to_action(row: dict[str, Any]) -> Action:
    return Action(**{**row, "status": ActionStatus(row["status"])})


def _amount(text: str | None) -> Decimal:
    """A stored amount. One that cannot be read counts as nothing rather than failing the read."""
    try:
        amount = Decimal(str(text))
    except InvalidOperation:
        return Decimal(0)
    return amount if amount.is_finite() and amount > 0 else Decimal(0)
