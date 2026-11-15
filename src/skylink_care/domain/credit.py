"""Service credit engine: eligibility and amount, computed in code from a versioned policy file.

The language model never touches these numbers. Callers pass verified facts in and get back a
decision that carries its own working, so a human can check it and an auditor can replay it.

Order of operations (fixed, and stated in the policy document):

    hours   = ceil(outage_minutes / billable_unit_minutes)
    amount  = monthly_fee / hours_in_month * hours * tier_multiplier
    amount  = max(amount, minimum_credit)                  floor
    amount  = min(amount, cap - prior_credits_in_cycle)    billing-cycle cap
    amount  = round to the policy quantum

The two rules that also have to hold at the moment a credit is applied, which may be days after
it was worked out, are exposed on their own: who must have approved an amount
(``required_approval``) and whether it still fits under the cap (``within_cycle_cap``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from skylink_care.clock import IST
from skylink_care.domain.models import ServiceType

ZERO = Decimal("0.00")
# The reason code under which Billing records a credit for an outage.
OUTAGE_CREDIT = "OUTAGE_CREDIT"


class CreditReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_AFFECTED = "NOT_AFFECTED"
    PLANNED_WORK = "PLANNED_WORK"
    SERVICE_SUSPENDED = "SERVICE_SUSPENDED"
    SERVICE_NOT_COVERED = "SERVICE_NOT_COVERED"
    OUTAGE_NOT_RESOLVED = "OUTAGE_NOT_RESOLVED"
    BELOW_MINIMUM_DURATION = "BELOW_MINIMUM_DURATION"
    ALREADY_CREDITED = "ALREADY_CREDITED"
    CAP_REACHED = "CAP_REACHED"


class ApprovalLevel(StrEnum):
    NONE = "none"
    AGENT = "agent"
    SUPERVISOR = "supervisor"

    def covers(self, needed: ApprovalLevel) -> bool:
        """An approval at this level is enough where ``needed`` is asked for."""
        return _RANK[self] >= _RANK[needed]

    def stricter(self, other: ApprovalLevel) -> ApprovalLevel:
        return self if _RANK[self] >= _RANK[other] else other


_RANK = {ApprovalLevel.NONE: 0, ApprovalLevel.AGENT: 1, ApprovalLevel.SUPERVISOR: 2}


# --- policy (validated at the file boundary) ---------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Tier(_Frozen):
    from_minutes: int = Field(ge=0)
    multiplier: Decimal = Field(gt=0)


class ServiceRule(_Frozen):
    min_outage_minutes: int = Field(gt=0)
    tiers: tuple[Tier, ...] = Field(min_length=1)
    minimum_credit_inr: Decimal = Field(ge=0)
    cap_fraction_of_monthly_fee: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _tiers_cover_every_eligible_duration(self) -> Self:
        starts = [tier.from_minutes for tier in self.tiers]
        if starts != sorted(set(starts)):
            raise ValueError("tiers must be in strictly ascending order of from_minutes")
        if starts[0] > self.min_outage_minutes:
            raise ValueError("tiers must start at or below min_outage_minutes")
        return self

    def multiplier_for(self, outage_minutes: int) -> Decimal:
        applicable = [tier for tier in self.tiers if tier.from_minutes <= outage_minutes]
        return applicable[-1].multiplier


class Rounding(_Frozen):
    quantum: Decimal = Field(gt=0)
    mode: Literal["ROUND_HALF_UP"]


class Approval(_Frozen):
    supervisor_above_inr: Decimal = Field(ge=0)


class CreditPolicy(_Frozen):
    policy_id: str
    version: str
    status: str
    effective_from: date
    currency: Literal["INR"]
    rounding: Rounding
    approval: Approval
    hours_in_month: int = Field(gt=0)
    billable_unit_minutes: int = Field(gt=0)
    exclude_planned_work: bool
    services: dict[ServiceType, ServiceRule]


def load_policy(path: Path) -> CreditPolicy:
    """Read and validate a policy file. Fails loudly: a bad policy must never price a credit."""
    if not path.is_file():
        raise FileNotFoundError(f"credit policy file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CreditPolicy.model_validate(raw)


# --- inputs and outputs ------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditInput:
    """Verified facts about one service and one outage. Nothing here comes from free text."""

    service_type: ServiceType
    monthly_fee_inr: Decimal
    service_active: bool
    affected: bool
    planned: bool
    started_at: datetime
    resolved_at: datetime | None
    prior_outage_credits_in_cycle_inr: Decimal
    already_credited_for_outage: bool


@dataclass(frozen=True)
class CreditCalculation:
    """The working behind a decision, kept for the customer explanation and the audit trail."""

    outage_minutes: int | None = None
    billable_hours: int | None = None
    multiplier: Decimal | None = None
    computed_inr: Decimal | None = None
    cap_remaining_inr: Decimal | None = None
    floor_applied: bool = False
    cap_applied: bool = False


@dataclass(frozen=True)
class CreditDecision:
    eligible: bool
    reason: CreditReason
    amount_inr: Decimal
    requires_supervisor: bool
    policy_id: str
    policy_version: str
    calculation: CreditCalculation


# --- engine ------------------------------------------------------------------------------------


def evaluate_credit(policy: CreditPolicy, facts: CreditInput) -> CreditDecision:
    """Decide eligibility and amount for one service and one outage under ``policy``."""
    _validate(facts)
    rule = policy.services.get(facts.service_type)
    minutes = _outage_minutes(facts)

    blocker = _first_blocker(policy, facts, rule, minutes)
    if blocker is not None:
        return _refuse(policy, blocker, CreditCalculation(outage_minutes=minutes))
    if rule is None or minutes is None:  # unreachable: both are blockers above
        raise AssertionError("eligibility checks let an unpriceable case through")

    return _price(policy, facts, rule, minutes)


def approval_level(decision: CreditDecision, *, review_all: bool) -> ApprovalLevel:
    """Who must approve before the credit is applied.

    Above the policy limit a supervisor is always required. ``review_all`` is the deployment
    switch that keeps a human in the loop for smaller credits too.
    """
    if not decision.eligible:
        return ApprovalLevel.NONE
    if decision.requires_supervisor:
        return ApprovalLevel.SUPERVISOR
    return ApprovalLevel.AGENT if review_all else ApprovalLevel.NONE


def required_approval(
    policy: CreditPolicy, amount_inr: Decimal, *, review_all: bool
) -> ApprovalLevel:
    """Who must have approved a credit of this amount, worked out from the amount alone.

    The executor asks this immediately before it applies a credit, so that the answer does not
    rest on what was recorded when the credit was proposed.
    """
    if amount_inr > policy.approval.supervisor_above_inr:
        return ApprovalLevel.SUPERVISOR
    return ApprovalLevel.AGENT if review_all else ApprovalLevel.NONE


def within_cycle_cap(
    policy: CreditPolicy,
    *,
    service_type: ServiceType,
    monthly_fee_inr: Decimal,
    credited_in_cycle_inr: Decimal,
    amount_inr: Decimal,
) -> bool:
    """Does a credit of this amount still fit under the billing-cycle cap of the service.

    It is the same comparison the engine makes when it prices a credit, on the rounded value an
    amount is stored as: a credit that was priced at the cap fits, one unit more does not.
    """
    rule = policy.services.get(service_type)
    if rule is None:
        return False
    remaining = _cap_remaining(rule, monthly_fee_inr, credited_in_cycle_inr)
    return amount_inr <= _round(policy, remaining)


def cycle_start(today: date, cycle_day: int) -> date:
    """The first day of the billing cycle that contains ``today`` (cycle days run 1 to 28)."""
    if today.day >= cycle_day:
        return today.replace(day=cycle_day)
    last_of_previous_month = today.replace(day=1) - timedelta(days=1)
    return last_of_previous_month.replace(day=cycle_day)


def billing_cycle_start(now: datetime, cycle_day: int) -> date:
    """The first day of the customer's current billing cycle, counted in their own time zone."""
    return cycle_start(now.astimezone(IST).date(), cycle_day)


def _validate(facts: CreditInput) -> None:
    if facts.monthly_fee_inr <= 0:
        raise ValueError("monthly_fee_inr must be positive")
    if facts.prior_outage_credits_in_cycle_inr < 0:
        raise ValueError("prior_outage_credits_in_cycle_inr must not be negative")
    stamps = [facts.started_at] + ([facts.resolved_at] if facts.resolved_at else [])
    if any(stamp.tzinfo is None for stamp in stamps):
        raise ValueError("outage timestamps must carry a timezone")
    if facts.resolved_at is not None and facts.resolved_at < facts.started_at:
        raise ValueError("resolved_at must not be earlier than started_at")


def _outage_minutes(facts: CreditInput) -> int | None:
    if facts.resolved_at is None:
        return None
    return int((facts.resolved_at - facts.started_at).total_seconds() // 60)


def _first_blocker(
    policy: CreditPolicy, facts: CreditInput, rule: ServiceRule | None, minutes: int | None
) -> CreditReason | None:
    """Return the first reason the outage earns nothing, in the documented precedence order."""
    checks: tuple[tuple[bool, CreditReason], ...] = (
        (not facts.affected, CreditReason.NOT_AFFECTED),
        (policy.exclude_planned_work and facts.planned, CreditReason.PLANNED_WORK),
        (not facts.service_active, CreditReason.SERVICE_SUSPENDED),
        (rule is None, CreditReason.SERVICE_NOT_COVERED),
        (minutes is None, CreditReason.OUTAGE_NOT_RESOLVED),
        (
            rule is not None and minutes is not None and minutes < rule.min_outage_minutes,
            CreditReason.BELOW_MINIMUM_DURATION,
        ),
        (facts.already_credited_for_outage, CreditReason.ALREADY_CREDITED),
    )
    return next((reason for failed, reason in checks if failed), None)


def _price(
    policy: CreditPolicy, facts: CreditInput, rule: ServiceRule, minutes: int
) -> CreditDecision:
    hours = -(-minutes // policy.billable_unit_minutes)  # ceiling division
    multiplier = rule.multiplier_for(minutes)
    # Multiply before the single division so that exact results (and exact .xx5 ties) stay exact.
    computed = facts.monthly_fee_inr * hours * multiplier / policy.hours_in_month

    floored = max(computed, rule.minimum_credit_inr)
    cap_remaining = _cap_remaining(
        rule, facts.monthly_fee_inr, facts.prior_outage_credits_in_cycle_inr
    )
    capped = min(floored, cap_remaining)
    amount = _round(policy, capped)

    working = CreditCalculation(
        outage_minutes=minutes,
        billable_hours=hours,
        multiplier=multiplier,
        computed_inr=_round(policy, computed),
        cap_remaining_inr=_round(policy, cap_remaining),
        floor_applied=floored > computed,
        cap_applied=capped < floored,
    )
    if amount <= 0:
        return _refuse(policy, CreditReason.CAP_REACHED, working)
    return CreditDecision(
        eligible=True,
        reason=CreditReason.ELIGIBLE,
        amount_inr=amount,
        requires_supervisor=required_approval(policy, amount, review_all=False)
        is ApprovalLevel.SUPERVISOR,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        calculation=working,
    )


def _cap_remaining(rule: ServiceRule, monthly_fee_inr: Decimal, credited_inr: Decimal) -> Decimal:
    """What outage credits on the service may still total in the billing cycle."""
    cap_total = monthly_fee_inr * rule.cap_fraction_of_monthly_fee
    return max(cap_total - credited_inr, Decimal(0))


def _refuse(
    policy: CreditPolicy, reason: CreditReason, working: CreditCalculation
) -> CreditDecision:
    return CreditDecision(
        eligible=False,
        reason=reason,
        amount_inr=ZERO,
        requires_supervisor=False,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        calculation=working,
    )


def _round(policy: CreditPolicy, amount: Decimal) -> Decimal:
    return amount.quantize(policy.rounding.quantum, rounding=ROUND_HALF_UP)
