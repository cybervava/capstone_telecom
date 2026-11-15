"""Resolution Agent: is this customer owed a service credit for the outage, and how much.

It reads the customer's existing credits from Billing and nothing else, and it changes nothing:
the credit itself is a proposal in the action ledger, applied by the executor after any approval
the policy requires. Eligibility and amount come from ``domain.credit.evaluate_credit`` under
the versioned policy file. Nothing a customer writes, and no language model, touches the numbers.

When the existing credits cannot be read, no credit is proposed: the cap for the billing cycle
and the one-credit-per-outage rule could not be checked. That is reported, not guessed around.

Credits that are proposed and not yet settled count towards the cap as well, so that two
outages in one billing cycle are not each priced as if the other did not exist. The executor
checks the cap again, under the customer's lock, when a credit is applied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from skylink_care.adapters.clients import BillingReader
from skylink_care.adapters.models import CreditDto
from skylink_care.adapters.result import Ok
from skylink_care.db.actions import OpenCredit
from skylink_care.domain.credit import (
    OUTAGE_CREDIT,
    ApprovalLevel,
    CreditDecision,
    CreditInput,
    CreditPolicy,
    approval_level,
    billing_cycle_start,
    evaluate_credit,
)
from skylink_care.domain.models import OutageInfo, ServiceType
from skylink_care.observability.audit import Auditor

ACTOR = "agent:resolution"
_APPLIED = "APPLIED"


@dataclass(frozen=True)
class ServiceFacts:
    """What is known about the service from the customer's record."""

    customer_id: str
    service_id: str
    service_type: str
    monthly_fee_inr: Decimal
    active: bool
    billing_cycle_day: int


class CreditLedger(Protocol):
    """Where credits that are proposed and not yet settled are looked up."""

    async def open_credits(self, service_id: str) -> Sequence[OpenCredit]: ...


@dataclass(frozen=True)
class CreditAssessment:
    # None when the assessment could not be made (Billing could not be read).
    decision: CreditDecision | None
    level: ApprovalLevel = ApprovalLevel.NONE

    @property
    def eligible(self) -> bool:
        return self.decision is not None and self.decision.eligible


class ResolutionAgent:
    def __init__(
        self,
        billing: BillingReader,
        policy: CreditPolicy,
        *,
        review_all: bool,
        ledger: CreditLedger | None = None,
    ) -> None:
        self._billing = billing
        self._policy = policy
        self._review_all = review_all
        self._ledger = ledger

    @property
    def policy_version(self) -> str:
        return self._policy.version

    async def assess(
        self, service: ServiceFacts, outage: OutageInfo, *, now: datetime, audit: Auditor
    ) -> CreditAssessment:
        """Evaluate a credit for one service and one resolved outage."""
        since = billing_cycle_start(now, service.billing_cycle_day)
        existing = await audit.tool(
            actor=ACTOR,
            tool="billing.credits",
            inputs={"customer_id": service.customer_id, "since": since.isoformat()},
            call=self._billing.credits(
                service.customer_id, since=since.isoformat(), correlation_id=audit.correlation_id
            ),
        )
        if not isinstance(existing, Ok):
            await self._note(audit, "BILLING_UNAVAILABLE", service, outage, None)
            return CreditAssessment(None)
        on_service = [
            credit
            for credit in existing.value.credits
            if credit.service_id == service.service_id and credit.status == _APPLIED
        ]
        on_its_way = await self._on_its_way(
            service.service_id,
            this_credit=credit_key(service.service_id, outage.outage_id),
            already_applied={credit.idempotency_key for credit in on_service},
        )
        facts = CreditInput(
            service_type=ServiceType(service.service_type),
            monthly_fee_inr=service.monthly_fee_inr,
            service_active=service.active,
            # The contact was told about this outage because its node was in it.
            affected=True,
            planned=outage.planned,
            started_at=outage.started_at,
            resolved_at=outage.resolved_at,
            prior_outage_credits_in_cycle_inr=_outage_credits(on_service) + on_its_way,
            already_credited_for_outage=any(c.outage_id == outage.outage_id for c in on_service),
        )
        decision = evaluate_credit(self._policy, facts)
        level = approval_level(decision, review_all=self._review_all)
        await self._note(audit, decision.reason.value, service, outage, decision, level=level)
        return CreditAssessment(decision, level)

    async def _on_its_way(
        self, service_id: str, *, this_credit: str, already_applied: set[str | None]
    ) -> Decimal:
        """What other credits on this service will add once they are applied.

        One that Billing already shows under its key is not counted twice, and the credit being
        assessed is not counted against itself.
        """
        if self._ledger is None:
            return Decimal("0.00")
        return sum(
            (
                credit.amount_inr
                for credit in await self._ledger.open_credits(service_id)
                if credit.action_key != this_credit and credit.action_key not in already_applied
            ),
            Decimal("0.00"),
        )

    async def _note(
        self,
        audit: Auditor,
        outcome: str,
        service: ServiceFacts,
        outage: OutageInfo,
        decision: CreditDecision | None,
        *,
        level: ApprovalLevel = ApprovalLevel.NONE,
    ) -> None:
        await audit.record(
            actor=ACTOR,
            step="credit.evaluate",
            outcome=outcome,
            inputs={
                "service_id": service.service_id,
                "service_type": service.service_type,
                "monthly_fee_inr": str(service.monthly_fee_inr),
                "outage_id": outage.outage_id,
                "planned": outage.planned,
            },
            outputs=working(decision) | {"approval": level.value} if decision else {},
            policy_version=self._policy.version,
        )


def credit_key(service_id: str, outage_id: str) -> str:
    """The identity of a credit: one per service per outage."""
    return f"credit:{service_id}:{outage_id}"


def working(decision: CreditDecision) -> dict[str, Any]:
    """The decision with its working, in plain values: for the audit trail and the approver."""
    calc = decision.calculation
    return {
        "eligible": decision.eligible,
        "reason": decision.reason.value,
        "amount_inr": str(decision.amount_inr),
        "policy_version": decision.policy_version,
        "outage_minutes": calc.outage_minutes,
        "billable_hours": calc.billable_hours,
        "multiplier": str(calc.multiplier) if calc.multiplier is not None else None,
        "computed_inr": str(calc.computed_inr) if calc.computed_inr is not None else None,
        "floor_applied": calc.floor_applied,
        "cap_applied": calc.cap_applied,
    }


def _outage_credits(credits: list[CreditDto]) -> Decimal:
    return sum(
        (credit.amount_inr for credit in credits if credit.reason_code == OUTAGE_CREDIT),
        Decimal("0.00"),
    )
