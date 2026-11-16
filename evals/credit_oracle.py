"""An independent reading of the service credit policy, in exact arithmetic.

It shares no code with the engine it checks (``skylink_care.domain.credit``). It reads the
policy file itself, works in rationals instead of decimals, and follows the order of operations
of the policy document: billable hours, tier multiplier, minimum, billing-cycle cap, rounding.
What it does share with the engine is its author's reading of that document, which is why the
evaluation also carries amounts worked out by hand (``evals/cases/credit_anchors.json``).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Case:
    service_type: str
    monthly_fee: str
    # Minutes from the start of the outage to its end; None while it is not resolved.
    minutes: int | None
    prior_credits: str = "0"
    planned: bool = False
    active: bool = True
    affected: bool = True
    already_credited: bool = False


@dataclass(frozen=True)
class Verdict:
    eligible: bool
    reason: str
    amount: str
    # Who must approve: with every credit reviewed, and with small credits left unreviewed.
    approval_reviewing_all: str
    approval_otherwise: str


def load(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def _refused(reason: str) -> Verdict:
    return Verdict(False, reason, "0.00", "none", "none")


def _two_places(amount: Fraction) -> str:
    cents = (amount * 100 + Fraction(1, 2)).__floor__()  # half a paisa and above rounds up
    return f"{cents // 100}.{cents % 100:02d}"


def verdict(policy: dict[str, Any], case: Case) -> Verdict:
    """Is a credit due for this outage on this service, how much, and who must approve it."""
    rule = policy["services"].get(case.service_type)
    if not case.affected:
        return _refused("NOT_AFFECTED")
    if policy["exclude_planned_work"] and case.planned:
        return _refused("PLANNED_WORK")
    if not case.active:
        return _refused("SERVICE_SUSPENDED")
    if rule is None:
        return _refused("SERVICE_NOT_COVERED")
    if case.minutes is None:
        return _refused("OUTAGE_NOT_RESOLVED")
    if case.minutes < rule["min_outage_minutes"]:
        return _refused("BELOW_MINIMUM_DURATION")
    if case.already_credited:
        return _refused("ALREADY_CREDITED")

    fee = Fraction(case.monthly_fee)
    unit = policy["billable_unit_minutes"]
    hours = -(-case.minutes // unit)
    multiplier = Fraction(
        str(
            max(
                (t for t in rule["tiers"] if t["from_minutes"] <= case.minutes),
                key=lambda t: t["from_minutes"],
            )["multiplier"]
        )
    )
    by_formula = fee * hours * multiplier / policy["hours_in_month"]
    at_least = max(by_formula, Fraction(str(rule["minimum_credit_inr"])))
    cap_left = max(
        fee * Fraction(str(rule["cap_fraction_of_monthly_fee"])) - Fraction(case.prior_credits),
        Fraction(0),
    )
    amount = _two_places(min(at_least, cap_left))
    if Fraction(amount) <= 0:
        return _refused("CAP_REACHED")
    above = Fraction(amount) > Fraction(str(policy["approval"]["supervisor_above_inr"]))
    return Verdict(
        True,
        "ELIGIBLE",
        amount,
        "supervisor" if above else "agent",
        "supervisor" if above else "none",
    )
