"""A credit in the ledger with Billing scripted: what the credit tests share."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from skylink_care.adapters.gateway import ExecutorClients
from skylink_care.adapters.models import CreditDto, CreditList
from skylink_care.adapters.result import Ok, Result
from skylink_care.db.actions import (
    CREDIT_APPLY,
    CREDIT_LANE,
    Action,
    ActionRepository,
    NewAction,
)
from skylink_care.db.approvals import ApprovalRepository, Decision
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.domain.credit import CreditPolicy, load_policy
from skylink_care.domain.models import Channel
from skylink_care.executor.worker import ActionExecutor

NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)
KEY = "credit:S1:OUT-1"
BODY = {
    "customer_id": "C1",
    "service_id": "S1",
    "amount_usd": "120.50",
    "reason_code": "OUTAGE_CREDIT",
    "outage_id": "OUT-1",
    "policy_version": "v0-placeholder",
    "complaint_ref": "SKL-261115-000001-9",
}
# What the proposal records beside the request: the service the cap is worked out for. The
# placeholder policy caps a cycle's outage credits at one monthly fee.
MONTHLY_FEE = Decimal("999.00")
INPUTS = {"monthly_fee_usd": "999.00", "service_type": "broadband", "billing_cycle_day": 10}


def policy() -> CreditPolicy:
    return load_policy(Path("data/policies/credit_policy.v0.yaml"))


def credit(**overrides: Any) -> CreditDto:
    fields: dict[str, Any] = {
        "credit_id": "CR-1",
        "customer_id": "C1",
        "service_id": "S1",
        "amount_usd": Decimal("120.50"),
        "reason_code": "OUTAGE_CREDIT",
        "outage_id": "OUT-1",
        "policy_version": "v0-placeholder",
        "status": "APPLIED",
        "created_at": NOW,
        "idempotency_key": KEY,
    }
    return CreditDto(**{**fields, **overrides})


def account(*credits: CreditDto) -> Result[CreditList]:
    return Ok(CreditList(credits=credits), status_code=200)


class ScriptedBilling:
    def __init__(
        self,
        *,
        existing: Result[CreditList] | None = None,
        applied: Result[CreditDto] | None = None,
    ) -> None:
        self.existing = existing or account()
        self.applied = applied or Ok(credit(), status_code=201)
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def credits(self, customer_id: str, **_: Any) -> Result[CreditList]:
        return self.existing

    async def apply_credit(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[CreditDto]:
        self.writes.append((idempotency_key, body))
        return self.applied


def executor(
    db: Database, billing: ScriptedBilling, *, review_all: bool = True, **options: Any
) -> ActionExecutor:
    clients = ExecutorClients(
        billing=billing,  # type: ignore[arg-type]
        field=None,  # type: ignore[arg-type]
        incident=None,  # type: ignore[arg-type]
        notify=None,  # type: ignore[arg-type]
        network=None,  # type: ignore[arg-type]
        crm=None,  # type: ignore[arg-type]
    )
    settings: dict[str, Any] = {
        "visibility_seconds": 0.01,
        "backoff_base_seconds": 0.01,
        "approvals": ApprovalRepository(db),
        "credit_policy": policy(),
        "credit_review_all": review_all,
        "approval_clock": lambda: NOW,
    }
    return ActionExecutor(
        ActionRepository(db),
        AuditRepository(db),
        HandoffRepository(db),
        clients,
        **{**settings, **options},
    )


async def a_contact(contacts: ContactRepository) -> UUID:
    intake = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="internet down",
            correlation_id=uuid4().hex,
            received_at=NOW,
        )
    )
    return intake.contact_id


async def proposed(
    db: Database,
    contact_id: UUID,
    *,
    level: str = "agent",
    ttl: timedelta = timedelta(hours=72),
    key: str = KEY,
    inputs: dict[str, Any] | None = None,
    asked_at: datetime = NOW,
    **changes: Any,
) -> Action:
    """A credit in the ledger, with the approval request its level calls for."""
    actions, approvals = ActionRepository(db), ApprovalRepository(db)
    body = {**BODY, **changes}
    new = NewAction(
        action_key=key,
        action_type=CREDIT_APPLY,
        lane=CREDIT_LANE,
        requested_by="agent:resolution",
        contact_id=contact_id,
        correlation_id=uuid4().hex,
        approval_level=level,
        request_body=body,
        inputs=dict(INPUTS if inputs is None else inputs),
    )
    async with db.transaction() as conn:
        proposal = await actions.propose(new, conn=conn)
        if level != "none":
            await approvals.request(
                conn,
                action_id=proposal.action.action_id,
                required_role=level,
                request_hash=proposal.action.request_hash,
                summary=body,
                expires_at=asked_at + ttl,
            )
    return proposal.action


async def decide(
    db: Database,
    action: Action,
    *,
    approve: bool = True,
    role: str = "agent",
    now: datetime = NOW,
) -> Decision:
    approvals = ApprovalRepository(db)
    approval = await approvals.for_action(action.action_id)
    assert approval is not None
    return await approvals.decide(
        approval.approval_id,
        approve=approve,
        decided_by="staff-1",
        role=role,
        request_hash=approval.request_hash,
        now=now,
        note="checked against the outage record",
    )


async def stored(db: Database, action: Action) -> Action:
    found = await ActionRepository(db).get(action.action_id)
    assert found is not None
    return found


async def woken_with(inbox: InboxRepository, contact_id: UUID) -> list[str]:
    return [
        event.payload["status"]
        for event in await inbox.fetch_unapplied(contact_id)
        if event.event_type == "action.finished"
    ]
