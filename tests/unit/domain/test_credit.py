"""Credit engine tests.

Expected amounts are worked out by hand from the placeholder policy (v0), not by calling the
engine, so a wrong formula cannot agree with itself:

    hours   = ceil(outage_minutes / 60)
    amount  = monthly_fee / 720 * hours * tier_multiplier
    amount  = max(amount, minimum)            # floor
    amount  = min(amount, cap - prior)        # billing-cycle cap
    amount  = round half-up to 0.01
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from skylink_care.domain.credit import (
    ApprovalLevel,
    CreditInput,
    CreditPolicy,
    CreditReason,
    approval_level,
    evaluate_credit,
    load_policy,
)
from skylink_care.domain.models import ServiceType

POLICY_PATH = Path(__file__).resolve().parents[3] / "data" / "policies" / "credit_policy.v0.yaml"
STARTED = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def policy() -> CreditPolicy:
    return load_policy(POLICY_PATH)


def make_input(
    *,
    fee: str = "999.00",
    minutes: int | None = 600,
    service_type: ServiceType = ServiceType.BROADBAND,
    prior: str = "0.00",
    **overrides: object,
) -> CreditInput:
    base = CreditInput(
        service_type=service_type,
        monthly_fee_inr=Decimal(fee),
        service_active=True,
        affected=True,
        planned=False,
        started_at=STARTED,
        resolved_at=None if minutes is None else STARTED + timedelta(minutes=minutes),
        prior_outage_credits_in_cycle_inr=Decimal(prior),
        already_credited_for_outage=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- amounts -------------------------------------------------------------------------------------

AMOUNT_CASES = [
    # id, fee, minutes, expected amount, supervisor?
    ("exactly at minimum duration, floor applies", "999.00", 240, "50.00", False),
    ("one minute over rounds up to 5 hours, floor applies", "999.00", 241, "50.00", False),
    ("10 hours on a 3999 plan", "3999.00", 600, "55.54", False),  # 55.5416…
    ("half-up at the third decimal", "3999.24", 600, "55.55", False),  # 55.545 exactly
    ("26 hours on a 9999 plan, tier 1.5", "9999.00", 1560, "541.61", True),  # 541.6125
    ("just under one day stays in tier 1.0", "7200.00", 1439, "240.00", False),
    ("exactly one day enters tier 1.5", "7200.00", 1440, "360.00", False),
    ("just under three days stays in tier 1.5", "7200.00", 4319, "1080.00", True),
    ("exactly three days enters tier 2.0", "7200.00", 4320, "1440.00", True),
    ("approval boundary: 499.99", "5999.88", 2400, "499.99", False),
    ("approval boundary: exactly 500.00 is not above", "6000.00", 2400, "500.00", False),
    ("approval boundary: 500.01", "6000.12", 2400, "500.01", True),
]


@pytest.mark.parametrize(
    ("fee", "minutes", "expected", "supervisor"),
    [case[1:] for case in AMOUNT_CASES],
    ids=[case[0] for case in AMOUNT_CASES],
)
def test_amount_matches_hand_calculation(
    policy: CreditPolicy, fee: str, minutes: int, expected: str, supervisor: bool
) -> None:
    decision = evaluate_credit(policy, make_input(fee=fee, minutes=minutes))

    assert decision.eligible
    assert decision.reason is CreditReason.ELIGIBLE
    assert decision.amount_inr == Decimal(expected)
    assert decision.requires_supervisor is supervisor


def test_amount_is_always_quantised_to_the_paisa(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(fee="1234.56", minutes=777))

    assert decision.amount_inr == decision.amount_inr.quantize(Decimal("0.01"))


def test_mobile_uses_its_own_threshold_tiers_and_floor(policy: CreditPolicy) -> None:
    # 25 h on a 499 plan: 499 / 720 * 25 = 17.33 -> mobile floor 25.00
    decision = evaluate_credit(
        policy, make_input(fee="499.00", minutes=1500, service_type=ServiceType.MOBILE)
    )

    assert decision.amount_inr == Decimal("25.00")


def test_mobile_outage_shorter_than_a_day_earns_nothing(policy: CreditPolicy) -> None:
    decision = evaluate_credit(
        policy, make_input(fee="499.00", minutes=1439, service_type=ServiceType.MOBILE)
    )

    assert not decision.eligible
    assert decision.reason is CreditReason.BELOW_MINIMUM_DURATION


# --- cap and prior credits ---------------------------------------------------------------------


def test_cycle_cap_reduces_the_credit_by_what_was_already_paid(policy: CreditPolicy) -> None:
    # 100 h, tier 2.0: 999 / 720 * 100 * 2 = 277.50; cap 999.00 - prior 800.00 leaves 199.00
    decision = evaluate_credit(policy, make_input(fee="999.00", minutes=6000, prior="800.00"))

    assert decision.amount_inr == Decimal("199.00")
    assert decision.calculation.cap_applied


def test_cap_wins_over_the_minimum_credit(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(fee="999.00", minutes=240, prior="969.00"))

    assert decision.amount_inr == Decimal("30.00")
    assert decision.calculation.floor_applied
    assert decision.calculation.cap_applied


def test_exhausted_cap_makes_the_outage_ineligible(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(fee="999.00", minutes=600, prior="999.00"))

    assert not decision.eligible
    assert decision.reason is CreditReason.CAP_REACHED
    assert decision.amount_inr == Decimal("0.00")


# --- eligibility -------------------------------------------------------------------------------

INELIGIBLE_CASES = [
    ("below minimum duration", {"minutes": 239}, CreditReason.BELOW_MINIMUM_DURATION),
    ("planned maintenance", {"planned": True}, CreditReason.PLANNED_WORK),
    ("service suspended", {"service_active": False}, CreditReason.SERVICE_SUSPENDED),
    ("service not on an affected node", {"affected": False}, CreditReason.NOT_AFFECTED),
    ("already credited", {"already_credited_for_outage": True}, CreditReason.ALREADY_CREDITED),
    ("outage still open", {"minutes": None}, CreditReason.OUTAGE_NOT_RESOLVED),
]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [case[1:] for case in INELIGIBLE_CASES],
    ids=[case[0] for case in INELIGIBLE_CASES],
)
def test_ineligible_cases_pay_nothing_and_say_why(
    policy: CreditPolicy, overrides: dict[str, object], reason: CreditReason
) -> None:
    decision = evaluate_credit(policy, make_input(**overrides))  # type: ignore[arg-type]

    assert not decision.eligible
    assert decision.reason is reason
    assert decision.amount_inr == Decimal("0.00")
    assert not decision.requires_supervisor


def test_an_open_outage_never_gets_a_final_duration_invented(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(minutes=None))

    assert decision.calculation.outage_minutes is None
    assert decision.calculation.billable_hours is None


def test_not_affected_takes_precedence_over_every_other_reason(policy: CreditPolicy) -> None:
    decision = evaluate_credit(
        policy, make_input(affected=False, planned=True, service_active=False, minutes=10)
    )

    assert decision.reason is CreditReason.NOT_AFFECTED


def test_service_type_without_a_rule_is_not_covered(policy: CreditPolicy) -> None:
    broadband_only = policy.model_copy(
        update={"services": {ServiceType.BROADBAND: policy.services[ServiceType.BROADBAND]}}
    )

    decision = evaluate_credit(
        broadband_only, make_input(minutes=6000, service_type=ServiceType.MOBILE)
    )

    assert decision.reason is CreditReason.SERVICE_NOT_COVERED


# --- explainability ----------------------------------------------------------------------------


def test_decision_carries_policy_identity_and_the_working(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(fee="9999.00", minutes=1560))

    assert decision.policy_id == "skylink-service-credit"
    assert decision.policy_version == "v0-placeholder"
    assert decision.calculation.outage_minutes == 1560
    assert decision.calculation.billable_hours == 26
    assert decision.calculation.multiplier == Decimal("1.5")
    assert not decision.calculation.floor_applied
    assert not decision.calculation.cap_applied


# --- input validation --------------------------------------------------------------------------


@pytest.mark.parametrize("fee", ["0.00", "-1.00"])
def test_non_positive_fee_is_rejected(policy: CreditPolicy, fee: str) -> None:
    with pytest.raises(ValueError, match="monthly_fee"):
        evaluate_credit(policy, make_input(fee=fee))


def test_negative_prior_credit_is_rejected(policy: CreditPolicy) -> None:
    with pytest.raises(ValueError, match="prior"):
        evaluate_credit(policy, make_input(prior="-0.01"))


def test_resolution_before_start_is_rejected(policy: CreditPolicy) -> None:
    with pytest.raises(ValueError, match="resolved_at"):
        evaluate_credit(policy, make_input(resolved_at=STARTED - timedelta(minutes=1)))


def test_naive_timestamps_are_rejected(policy: CreditPolicy) -> None:
    naive = datetime(2026, 11, 10, 2, 0)  # noqa: DTZ001 - the point of the test

    with pytest.raises(ValueError, match="timezone"):
        evaluate_credit(policy, make_input(started_at=naive))


# --- approval level ----------------------------------------------------------------------------


def test_small_credit_needs_no_human_when_review_all_is_off(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(fee="3999.00", minutes=600))

    assert approval_level(decision, review_all=False) is ApprovalLevel.NONE


def test_small_credit_needs_an_agent_when_review_all_is_on(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(fee="3999.00", minutes=600))

    assert approval_level(decision, review_all=True) is ApprovalLevel.AGENT


@pytest.mark.parametrize("review_all", [True, False])
def test_credit_above_the_limit_always_needs_a_supervisor(
    policy: CreditPolicy, review_all: bool
) -> None:
    decision = evaluate_credit(policy, make_input(fee="9999.00", minutes=1560))

    assert approval_level(decision, review_all=review_all) is ApprovalLevel.SUPERVISOR


def test_ineligible_decision_needs_no_approval(policy: CreditPolicy) -> None:
    decision = evaluate_credit(policy, make_input(planned=True))

    assert approval_level(decision, review_all=True) is ApprovalLevel.NONE


# --- policy file -------------------------------------------------------------------------------


def test_policy_file_is_marked_as_a_placeholder(policy: CreditPolicy) -> None:
    assert policy.status == "placeholder"
    assert "placeholder" in policy.version


def test_policy_with_unsorted_tiers_is_rejected(tmp_path: Path) -> None:
    second = '- {from_minutes: 1440, multiplier: "1.5"}'
    third = '- {from_minutes: 4320, multiplier: "2.0"}'
    indent = "\n      "
    original = POLICY_PATH.read_text(encoding="utf-8")
    assert second + indent + third in original
    text = original.replace(second + indent + third, third + indent + second)
    broken = tmp_path / "policy.yaml"
    broken.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="tiers"):
        load_policy(broken)


def test_policy_whose_first_tier_starts_after_the_minimum_is_rejected(tmp_path: Path) -> None:
    text = POLICY_PATH.read_text(encoding="utf-8").replace(
        "min_outage_minutes: 240", "min_outage_minutes: 120"
    )
    broken = tmp_path / "policy.yaml"
    broken.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="tiers"):
        load_policy(broken)


def test_missing_policy_file_gives_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="credit policy"):
        load_policy(tmp_path / "nope.yaml")
