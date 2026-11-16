"""Approvals: a person's decision on one frozen request.

An approval is bound to the hash of the request the approver was shown. The decision has to
quote that hash back, so an approval can never be applied to a request the approver did not see,
and the executor checks the same hash again before it sends anything.

A decision and its effect on the action commit together: approved means the action becomes
runnable in the same transaction; rejected or expired means the action ends there and the
contact that is waiting on it is woken. The caller may add its own writes to that transaction
(the audit entry of the decision), so that neither exists without the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from skylink_care.db.actions import ACTION_FINISHED_EVENT
from skylink_care.db.base import Repository
from skylink_care.db.database import Connection
from skylink_care.db.inbox import append_event

_COLUMNS = (
    "approval_id, action_id, required_role, request_hash, summary, status, requested_at,"
    " expires_at, decided_by, decided_at, decision_note"
)
AGENT = "agent"
SUPERVISOR = "supervisor"
# Who may decide what: a supervisor may also take a decision that only needs an agent.
_MAY_DECIDE = {AGENT: frozenset({AGENT}), SUPERVISOR: frozenset({AGENT, SUPERVISOR})}


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class DecisionOutcome(StrEnum):
    DECIDED = "DECIDED"
    NOT_FOUND = "NOT_FOUND"
    NOT_PENDING = "NOT_PENDING"  # already decided, or expired
    WRONG_ROLE = "WRONG_ROLE"
    STALE = "STALE"  # the approver was looking at a different request


@dataclass(frozen=True)
class Approval:
    approval_id: UUID
    action_id: UUID
    required_role: str
    request_hash: str
    summary: dict[str, Any]
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


@dataclass(frozen=True)
class DecidedAction:
    """The action a decision was applied to, as far as whoever records the decision needs it."""

    action_key: str
    contact_id: UUID | None
    correlation_id: str
    policy_version: str | None


@dataclass(frozen=True)
class Decision:
    outcome: DecisionOutcome
    approval: Approval | None = None
    action: DecidedAction | None = None


class ApprovalRepository(Repository):
    async def request(
        self,
        conn: Connection,
        *,
        action_id: UUID,
        required_role: str,
        request_hash: str,
        summary: dict[str, Any],
        expires_at: datetime,
    ) -> None:
        """Ask for a decision on an action, in the transaction that proposes it. Once only."""
        await conn.execute(
            "INSERT INTO approvals (approval_id, action_id, required_role, request_hash,"
            " summary, expires_at) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (action_id) DO NOTHING",
            (uuid4(), action_id, required_role, request_hash, Jsonb(summary), expires_at),
        )

    async def list_pending(self, role: str, *, now: datetime, limit: int = 100) -> list[Approval]:
        """What this role may decide now, oldest first. One past its time is no longer offered,
        whether or not the sweep has reached it yet."""
        roles = sorted(_MAY_DECIDE.get(role, frozenset()))
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM approvals"  # noqa: S608 - constant column list
                " WHERE status = 'PENDING' AND required_role = ANY(%s) AND expires_at > %s"
                " ORDER BY requested_at LIMIT %s",
                (roles, now, limit),
            )
            return [_to_approval(row) for row in await cursor.fetchall()]

    async def for_action(self, action_id: UUID) -> Approval | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM approvals WHERE action_id = %s",  # noqa: S608
                (action_id,),
            )
            row = await cursor.fetchone()
        return _to_approval(row) if row else None

    async def decide(
        self,
        approval_id: UUID,
        *,
        approve: bool,
        decided_by: str,
        role: str,
        request_hash: str,
        now: datetime,
        note: str | None = None,
        conn: Connection | None = None,
    ) -> Decision:
        """Record one decision and apply it to the action, atomically.

        With ``conn`` the decision joins the caller's transaction, which must be one.
        """
        verdict = _Verdict(approve, decided_by, role, request_hash, now, note)
        if conn is not None:
            return await _decide(conn, approval_id, verdict)
        async with self._db.transaction() as own:
            return await _decide(own, approval_id, verdict)

    async def expire_due(self, now: datetime, *, limit: int = 200) -> int:
        """End approvals nobody decided in time, with their actions. Returns how many."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE approvals SET status = 'EXPIRED', decided_at = %s"  # noqa: S608
                " WHERE approval_id IN (SELECT approval_id FROM approvals"
                "   WHERE status = 'PENDING' AND expires_at <= %s"
                "   ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED)"
                f" RETURNING {_COLUMNS}",
                (now, now, limit),
            )
            expired = [_to_approval(row) for row in await cursor.fetchall()]
            for approval in sorted(expired, key=lambda item: item.action_id):
                await _apply(conn, approval, "EXPIRED")
            return len(expired)


@dataclass(frozen=True)
class _Verdict:
    approve: bool
    decided_by: str
    role: str
    request_hash: str
    now: datetime
    note: str | None


async def _decide(conn: Connection, approval_id: UUID, verdict: _Verdict) -> Decision:
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM approvals WHERE approval_id = %s FOR UPDATE",  # noqa: S608
        (approval_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return Decision(DecisionOutcome.NOT_FOUND)
    approval = _to_approval(row)
    if approval.status is not ApprovalStatus.PENDING or approval.expires_at <= verdict.now:
        return Decision(DecisionOutcome.NOT_PENDING, approval)
    if approval.required_role not in _MAY_DECIDE.get(verdict.role, frozenset()):
        return Decision(DecisionOutcome.WRONG_ROLE, approval)
    if approval.request_hash != verdict.request_hash:
        return Decision(DecisionOutcome.STALE, approval)
    status = ApprovalStatus.APPROVED if verdict.approve else ApprovalStatus.REJECTED
    cursor = await conn.execute(
        "UPDATE approvals SET status = %s, decided_by = %s, decided_at = %s,"  # noqa: S608
        " decision_note = %s WHERE approval_id = %s"
        f" RETURNING {_COLUMNS}",
        (
            status.value,
            verdict.decided_by,
            verdict.now,
            (verdict.note or "")[:1000] or None,
            approval_id,
        ),
    )
    decided = _to_approval(await cursor.fetchone() or row)
    action = await _apply(conn, decided, "READY" if verdict.approve else "REJECTED")
    return Decision(DecisionOutcome.DECIDED, decided, action or await _named(conn, decided))


async def _named(conn: Connection, approval: Approval) -> DecidedAction | None:
    """The action of an approval whose decision changed nothing (it had ended already)."""
    cursor = await conn.execute(
        "SELECT action_key, contact_id, correlation_id, policy_version FROM actions"
        " WHERE action_id = %s",
        (approval.action_id,),
    )
    row = await cursor.fetchone()
    return DecidedAction(**row) if row else None


async def _apply(conn: Connection, approval: Approval, action_status: str) -> DecidedAction | None:
    """Carry a decision over to its action; wake the waiting contact unless it now runs.

    An approved action is runnable from the moment its approval is recorded: that is where the
    executor's clock for it starts. The moment is the ledger's own, stamped by the database
    like every other time the executor judges an action's age by. The time the decision was
    made by the approver's clock stays on the approval.
    """
    cursor = await conn.execute(
        "UPDATE actions SET status = %s, updated_at = clock_timestamp(),"
        " ready_at = CASE WHEN %s = 'READY' THEN clock_timestamp() ELSE ready_at END,"
        " last_error = CASE WHEN %s = 'READY' THEN last_error ELSE %s END"
        " WHERE action_id = %s AND status = 'PENDING_APPROVAL'"
        " RETURNING action_key, contact_id, correlation_id, policy_version",
        (
            action_status,
            action_status,
            action_status,
            f"approval {approval.status.value}",
            approval.action_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if action_status != "READY" and row["contact_id"] is not None:
        await append_event(
            conn,
            row["contact_id"],
            ACTION_FINISHED_EVENT,
            dedupe_key=f"action:{approval.action_id}",
            payload={"action_id": str(approval.action_id), "status": action_status},
        )
    return DecidedAction(**row)


def _to_approval(row: dict[str, Any]) -> Approval:
    return Approval(**{**row, "status": ApprovalStatus(row["status"])})
