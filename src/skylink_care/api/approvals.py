"""Approval endpoints: a person's decision on a service credit that needs one.

The approver is shown the frozen request with its working and a hash of it. The decision must
quote that hash back: a decision taken on a stale screen is refused, not applied to something
the approver did not see. Who may decide is settled by role, checked here and again in the
repository: an agent decides what needs an agent, a supervisor may decide either. A rejection
must say why; the reason is kept with the approval and written to the audit trail.

A decision, its effect on the action and its entry in the audit trail are one transaction: a
decision that could not be recorded in the trail is not taken.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import StringConstraints, model_validator

from skylink_care.api.auth import Principal, Role, require
from skylink_care.api.schemas import _In, ok
from skylink_care.clock import Clock
from skylink_care.db.approvals import Approval, ApprovalRepository, Decision, DecisionOutcome
from skylink_care.db.audit import AuditEntry, AuditRepository
from skylink_care.db.database import Database
from skylink_care.guardrails.pii import redact_for_log

APPROVERS = (Role.AGENT, Role.SUPERVISOR)
# The fewest letters a note must have to be a reason. Letters, not characters: dots, spaces
# that are not spaces and invisible characters are not a reason.
MIN_REASON_LETTERS = 5
_REFUSALS = {
    DecisionOutcome.NOT_FOUND: (status.HTTP_404_NOT_FOUND, "approval not found"),
    DecisionOutcome.NOT_PENDING: (
        status.HTTP_409_CONFLICT,
        "this approval has already been decided or has expired",
    ),
    DecisionOutcome.WRONG_ROLE: (
        status.HTTP_403_FORBIDDEN,
        "this decision needs a supervisor",
    ),
    DecisionOutcome.STALE: (
        status.HTTP_409_CONFLICT,
        "the request shown is not the one on record; reload and decide again",
    ),
}


class DecisionIn(_In):
    decision: Literal["approve", "reject"]
    # The hash of the request as it was shown to the approver.
    request_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    note: Annotated[str, StringConstraints(max_length=500, strip_whitespace=True)] | None = None

    @model_validator(mode="after")
    def _a_rejection_says_why(self) -> Self:
        # The policy says the credit is owed and a person says no. The customer will ask
        # why, and so will whoever reads the trail: the person who decides must say.
        letters = sum(char.isalpha() for char in self.note or "")
        if self.decision == "reject" and letters < MIN_REASON_LETTERS:
            raise ValueError("a rejection needs its reason in the note")
        return self


def register_approval_routes(app: FastAPI, database: Database, now: Clock) -> None:
    approvals = ApprovalRepository(database)
    audit = AuditRepository(database)

    @app.get("/v1/approvals")
    async def list_pending(
        principal: Annotated[Principal, Depends(require(*APPROVERS))],
    ) -> dict[str, Any]:
        pending = await approvals.list_pending(principal.role.value, now=now())
        return ok([_view(item) for item in pending])

    @app.post("/v1/approvals/{approval_id}/decision")
    async def decide(
        approval_id: UUID,
        body: DecisionIn,
        principal: Annotated[Principal, Depends(require(*APPROVERS))],
    ) -> dict[str, Any]:
        async with database.transaction() as conn:
            result = await approvals.decide(
                approval_id,
                approve=body.decision == "approve",
                decided_by=principal.subject,
                role=principal.role.value,
                request_hash=body.request_hash,
                now=now(),
                note=body.note,
                conn=conn,
            )
            if result.outcome is not DecisionOutcome.DECIDED or result.approval is None:
                code, message = _REFUSALS[result.outcome]
                raise HTTPException(code, message)
            await audit.append(_audit_entry(result, result.approval, principal), conn=conn)
        return ok(_view(result.approval))


def _audit_entry(result: Decision, decided: Approval, principal: Principal) -> AuditEntry:
    action = result.action
    return AuditEntry(
        correlation_id=action.correlation_id if action else str(decided.action_id),
        contact_id=action.contact_id if action else None,
        actor=f"staff:{principal.subject}",
        step="approval.decided",
        outcome=decided.status.value,
        inputs={
            "approval_id": str(decided.approval_id),
            "request_hash": decided.request_hash,
            "role": principal.role.value,
            "required_role": decided.required_role,
        },
        outputs={
            "action_key": action.action_key if action else None,
            # A person's own words, with contact details masked as everywhere in the trail.
            "note": str(redact_for_log(decided.decision_note)) if decided.decision_note else None,
        },
        policy_version=action.policy_version if action else None,
    )


def _view(approval: Approval) -> dict[str, Any]:
    return dict(
        jsonable_encoder(
            {
                "approval_id": approval.approval_id,
                "required_role": approval.required_role,
                "status": approval.status.value,
                "request_hash": approval.request_hash,
                "request": approval.summary,
                "requested_at": approval.requested_at,
                "expires_at": approval.expires_at,
                "decided_by": approval.decided_by,
                "decided_at": approval.decided_at,
            }
        )
    )
