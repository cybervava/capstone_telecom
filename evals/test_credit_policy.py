"""Evaluation: do credits match the policy, every time.

Brief metric: "Credits match policy 100%". The engine is compared, case by case, with an
independent oracle in exact arithmetic over a grid that sits on every boundary of the policy,
and with amounts worked out by hand. Eligibility, the reason for a refusal, the amount and who
must approve are all compared; one difference fails the evaluation.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any

from evals.credit_oracle import Case, Verdict, load, verdict
from evals.report import Report, frozen, percent
from skylink_care.domain.credit import (
    CreditInput,
    CreditPolicy,
    approval_level,
    evaluate_credit,
    load_policy,
)
from skylink_care.domain.models import ServiceType

POLICY_PATH = Path(__file__).resolve().parents[1] / "data" / "policies" / "credit_policy.v0.yaml"
STARTED = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
FEES = (
    "199.00",
    "399.00",
    "499.00",
    "749.00",
    "999.00",
    "1499.00",
    "2499.00",
    "3999.00",
    "9999.00",
)
# Either side of every duration the policy names, for both services, and far beyond.
MINUTES = (1, 59, 60, 239, 240, 241, 300, 301, 600, 1439, 1440, 1441, 4319, 4320, 4321, 43200)
FLAGS = (
    {},
    {"planned": True},
    {"active": False},
    {"affected": False},
    {"already_credited": True},
    {"planned": True, "already_credited": True},
)


def priors(fee: str) -> tuple[str, ...]:
    """Earlier credits in the cycle: none, a little, and all around what the cap leaves."""
    whole = Decimal(fee)
    return tuple(
        str(value)
        for value in (
            Decimal("0"),
            Decimal("0.01"),
            whole / 2,
            whole - Decimal("500.01"),
            whole - Decimal("500.00"),
            whole - Decimal("499.99"),
            whole - Decimal("50.00"),
            whole - Decimal("0.01"),
            whole,
            whole + Decimal("10"),
        )
        if value >= 0
    )


def grid() -> list[Case]:
    cases = [
        Case(service, fee, minutes, prior, **flags)
        for service, fee, minutes, flags in product(("broadband", "mobile"), FEES, MINUTES, FLAGS)
        for prior in (priors(fee) if not flags else ("0",))
    ]
    cases += [Case(service, fee, None) for service in ("broadband", "mobile") for fee in FEES]
    return cases


def anchor_case(anchor: dict[str, Any]) -> Case:
    return Case(
        anchor["service_type"],
        anchor["monthly_fee"],
        anchor["minutes"],
        anchor.get("prior_credits", "0"),
        planned=anchor.get("planned", False),
    )


def agrees(anchor: dict[str, Any], got: Verdict) -> bool:
    """The engine's answer is the one worked out by hand: amount or reason, and approval."""
    by_hand = (anchor.get("amount", "0.00"), anchor.get("reason", "ELIGIBLE"))
    return (got.amount, got.reason) == by_hand and got.approval_reviewing_all == anchor.get(
        "approval", "none"
    )


def by_the_engine(policy: CreditPolicy, case: Case) -> Verdict:
    facts = CreditInput(
        service_type=ServiceType(case.service_type),
        monthly_fee_inr=Decimal(case.monthly_fee),
        service_active=case.active,
        affected=case.affected,
        planned=case.planned,
        started_at=STARTED,
        resolved_at=None if case.minutes is None else STARTED + timedelta(minutes=case.minutes),
        prior_outage_credits_in_cycle_inr=Decimal(case.prior_credits),
        already_credited_for_outage=case.already_credited,
    )
    decision = evaluate_credit(policy, facts)
    return Verdict(
        decision.eligible,
        decision.reason.value,
        f"{decision.amount_inr:.2f}",
        approval_level(decision, review_all=True).value,
        approval_level(decision, review_all=False).value,
    )


def hand_worked_rows(checked: list[tuple[dict[str, Any], Any]]) -> list[list[Any]]:
    return [
        [
            anchor["why"],
            anchor.get("amount") or anchor["reason"],
            got.amount if got.eligible else got.reason,
            got.approval_reviewing_all,
        ]
        for anchor, got in checked
    ]


def assumptions(version: str) -> list[str]:
    return [
        f"Policy {version} is a synthetic placeholder (assumption A2), not SkyLink's real"
        " policy. The evaluation shows the engine follows the policy file it is given; it"
        " says nothing about whether that policy is the right one.",
        "The oracle is a second implementation by the same author from the same policy"
        " document. The hand-worked amounts are the check that does not share that reading.",
        "This covers the calculation. That the workflow proposes exactly the calculated"
        " amount, once, and applies it only after approval is covered by the end-to-end"
        " tests (tests/e2e/test_service_credits.py, test_credit_lifecycle.py).",
    ]


def the_report(
    version: str,
    cases: int,
    matched: int,
    checked: list[tuple[dict[str, Any], Any]],
    wrong_anchors: list[Any],
    outcomes: dict[str, int],
) -> Report:
    report = Report(
        name="credit_policy",
        title="Credits match the policy",
        proves=(
            "For every case of a grid on the policy's boundaries the credit engine gives the"
            " same eligibility, the same reason for a refusal, the same amount to the paisa"
            " and the same approval level as an independent calculation in exact arithmetic,"
            " and as amounts worked out by hand."
        ),
        target="100% of cases match (brief: credits match policy 100%).",
        headline=(
            f"{matched} of {cases} grid cases match ({percent(matched, cases)}%);"
            f" {len(checked) - len(wrong_anchors)} of {len(checked)} hand-worked amounts match."
        ),
        met=matched == cases and not wrong_anchors,
        numbers={
            "policy_version": version,
            "grid_cases": cases,
            "grid_matches": matched,
            "hand_worked": len(checked),
            "hand_worked_matches": len(checked) - len(wrong_anchors),
            "by_outcome": outcomes,
        },
        assumptions=assumptions(version),
    )
    report.table(
        "Grid, by expected outcome",
        ["Outcome", "Cases"],
        [[reason, count] for reason, count in sorted(outcomes.items())],
    )
    report.table(
        "Hand-worked amounts",
        ["Case", "By hand", "Engine", "Approval"],
        hand_worked_rows(checked),
    )
    return report


def test_credits_match_the_policy_in_every_case() -> None:
    engine_policy, oracle_policy = load_policy(POLICY_PATH), load(POLICY_PATH)
    cases = grid()
    differences = [
        (case, expected, got)
        for case in cases
        if (expected := verdict(oracle_policy, case)) != (got := by_the_engine(engine_policy, case))
    ]
    anchors = frozen("credit_anchors.json")["cases"]
    checked = [(anchor, by_the_engine(engine_policy, anchor_case(anchor))) for anchor in anchors]
    # Wrong against the amount worked out by hand, or against the oracle: counted once.
    wrong_anchors = [
        (anchor["why"], got)
        for anchor, got in checked
        if not agrees(anchor, got) or got != verdict(oracle_policy, anchor_case(anchor))
    ]
    outcomes = Counter(verdict(oracle_policy, case).reason for case in cases)

    report = the_report(
        engine_policy.version,
        len(cases),
        len(cases) - len(differences),
        checked,
        wrong_anchors,
        dict(outcomes),
    )
    report.write()

    assert not differences, f"{len(differences)} differences, first: {differences[0]}"
    assert not wrong_anchors, wrong_anchors
