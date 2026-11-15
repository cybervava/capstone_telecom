"""The credit write: the last checks before money moves.

A credit is proposed by one process, approved by a person and applied here, possibly days
later. Nothing that was true at the proposal is taken on trust at this point:

* the stored request must still match the hash the approver was shown;
* the approval this amount requires under the policy must be on record for that hash, whatever
  the action row says about the level it needed;
* under the customer's lock the account is read. A credit already there under this action's own
  key is the answer to an earlier attempt and is returned as such, with nothing sent again. A
  credit for the same service and outage from any other source means this one is skipped. And
  the outage credits of the billing cycle, with this one, must stay within the policy's cap.

Only then is the frozen request sent, unchanged but for the name of who approved it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from skylink_care.adapters.clients import BillingWriter
from skylink_care.adapters.models import CreditDto
from skylink_care.adapters.result import Ok
from skylink_care.clock import IST, Clock
from skylink_care.db.actions import Action, ActionRepository
from skylink_care.db.approvals import ApprovalRepository, ApprovalStatus
from skylink_care.domain.credit import (
    OUTAGE_CREDIT,
    ApprovalLevel,
    CreditPolicy,
    billing_cycle_start,
    required_approval,
    within_cycle_cap,
)
from skylink_care.domain.models import ServiceType
from skylink_care.executor.outcomes import (
    MissingPrerequisiteError,
    NotAttempted,
    Outcome,
    Refused,
    Skipped,
)
from skylink_care.hashing import canonical_hash

ALREADY_CREDITED = "ALREADY_CREDITED"
CAP_EXCEEDED = "CAP_EXCEEDED"
POLICY_VERSION_CHANGED = "POLICY_VERSION_CHANGED"
APPLIED = "APPLIED"
# Credits written at once. Each holds a database connection for the length of two calls to
# Billing, so this stays well below the size of the pool.
CONCURRENT_CREDITS = 2
LAST_CYCLE_DAY = 28


@dataclass(frozen=True)
class _Terms:
    """The stored request, read back as values. Anything unreadable stops the write."""

    customer_id: str
    service_id: str
    outage_id: str
    amount: Decimal
    service_type: ServiceType
    monthly_fee: Decimal
    cycle_day: int


class CreditApplier:
    def __init__(
        self,
        actions: ActionRepository,
        approvals: ApprovalRepository | None,
        billing: BillingWriter,
        policy: CreditPolicy | None,
        *,
        review_all: bool,
        now: Clock,
        concurrent: int = CONCURRENT_CREDITS,
    ) -> None:
        self._actions = actions
        self._approvals = approvals
        self._billing = billing
        self._policy = policy
        self._review_all = review_all
        self._now = now
        self._slots = asyncio.Semaphore(concurrent)

    async def __call__(self, action: Action) -> Outcome:
        policy = self._policy
        if policy is None:
            raise MissingPrerequisiteError("no credit policy is loaded in this executor")
        terms = _terms(action)
        if action.request_body.get("policy_version") != policy.version:
            # Priced and approved under a policy that is no longer the one in force.
            return Refused(POLICY_VERSION_CHANGED)
        approver = await self._approved_by(action, terms, policy)
        async with self._slots, self._actions.customer_lock(terms.customer_id):
            account = await self._billing.credits(
                terms.customer_id, correlation_id=action.correlation_id
            )
            if not isinstance(account, Ok):
                return NotAttempted(account)
            settled = self._settled_already(action, terms, policy, account.value.credits)
            if settled is not None:
                return settled
            return await self._billing.apply_credit(
                {**action.request_body, "approved_by": approver},
                idempotency_key=action.action_key,
                correlation_id=action.correlation_id,
            )

    async def _approved_by(self, action: Action, terms: _Terms, policy: CreditPolicy) -> str | None:
        """Who approved this exact request, or None when the policy asks for no approval.

        The level is worked out here from the amount, and the stricter of that and what the
        action row says is required. A runnable credit without a sufficient approval should be
        impossible; it is checked anyway, because this is the last point before money moves.
        """
        body_hash = canonical_hash(action.request_body)
        if body_hash != action.request_hash:
            raise MissingPrerequisiteError("the stored request no longer matches its hash")
        needed = required_approval(policy, terms.amount, review_all=self._review_all).stricter(
            _level(action.approval_level, unknown=ApprovalLevel.SUPERVISOR)
        )
        if needed is ApprovalLevel.NONE:
            return None
        approval = await self._approvals.for_action(action.action_id) if self._approvals else None
        if (
            approval is None
            or approval.status is not ApprovalStatus.APPROVED
            or approval.request_hash != body_hash
            or not _level(approval.required_role, unknown=ApprovalLevel.NONE).covers(needed)
        ):
            raise MissingPrerequisiteError(
                f"no {needed.value} approval on record for this exact request"
            )
        return approval.decided_by

    def _settled_already(
        self, action: Action, terms: _Terms, policy: CreditPolicy, credits: Sequence[CreditDto]
    ) -> Outcome | None:
        """Why nothing has to be sent, given what is on the account; None when it must be."""
        ours = next((c for c in credits if c.idempotency_key == action.action_key), None)
        if ours is not None:
            # An earlier attempt landed and its answer was lost. It is not sent a second time.
            return Ok(ours, status_code=200, replayed=True)
        on_service = [
            c for c in credits if c.service_id == terms.service_id and c.status == APPLIED
        ]
        if any(c.outage_id == terms.outage_id for c in on_service):
            return Skipped(ALREADY_CREDITED)
        cycle_began = billing_cycle_start(self._now(), terms.cycle_day)
        credited = sum(
            (
                c.amount_inr
                for c in on_service
                if c.reason_code == OUTAGE_CREDIT
                and c.created_at.astimezone(IST).date() >= cycle_began
            ),
            Decimal("0.00"),
        )
        if not within_cycle_cap(
            policy,
            service_type=terms.service_type,
            monthly_fee_inr=terms.monthly_fee,
            credited_in_cycle_inr=credited,
            amount_inr=terms.amount,
        ):
            return Refused(CAP_EXCEEDED)
        return None


def _terms(action: Action) -> _Terms:
    body, shown = action.request_body, action.inputs
    try:
        terms = _Terms(
            customer_id=_text(body.get("customer_id")),
            service_id=_text(body.get("service_id")),
            outage_id=_text(body.get("outage_id")),
            amount=_money(body.get("amount_inr")),
            service_type=ServiceType(str(shown.get("service_type"))),
            monthly_fee=_money(shown.get("monthly_fee_inr")),
            cycle_day=int(shown["billing_cycle_day"]),
        )
        if not 1 <= terms.cycle_day <= LAST_CYCLE_DAY:
            raise ValueError("billing cycle day out of range")
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise MissingPrerequisiteError(
            f"the stored credit request cannot be read ({type(exc).__name__})"
        ) from exc
    return terms


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("a required identifier is missing")
    return value


def _money(value: Any) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("an amount must be a positive number")
    return amount


def _level(text: str, *, unknown: ApprovalLevel) -> ApprovalLevel:
    try:
        return ApprovalLevel(text)
    except ValueError:
        return unknown
