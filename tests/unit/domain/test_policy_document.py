"""The policy document customers and agents read must say what the engine does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from skylink_care.domain.credit import CreditInput, evaluate_credit, load_policy
from skylink_care.domain.models import ServiceType

ROOT = Path(__file__).resolve().parents[3]
POLICY = load_policy(ROOT / "data" / "policies" / "credit_policy.v0.yaml")
DOCUMENT = (ROOT / "data" / "knowledge" / "policies" / "service_credit_policy.md").read_text(
    encoding="utf-8"
)
STARTED = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)

# (monthly fee, outage hours, already credited this cycle) as printed in the worked examples.
EXAMPLES = [
    ("999.00", 4, "0.00", "50.00", "No"),
    ("3999.00", 10, "0.00", "55.54", "No"),
    ("9999.00", 26, "0.00", "541.61", "Yes"),
    ("6000.00", 40, "0.00", "500.00", "No"),
    ("999.00", 100, "800.00", "199.00", "No"),
]


@pytest.mark.parametrize(("fee", "hours", "prior", "credit", "supervisor"), EXAMPLES)
def test_worked_examples_in_the_document_match_the_engine(
    fee: str, hours: int, prior: str, credit: str, supervisor: str
) -> None:
    decision = evaluate_credit(
        POLICY,
        CreditInput(
            service_type=ServiceType.BROADBAND,
            monthly_fee_inr=Decimal(fee),
            service_active=True,
            affected=True,
            planned=False,
            started_at=STARTED,
            resolved_at=STARTED + timedelta(hours=hours),
            prior_outage_credits_in_cycle_inr=Decimal(prior),
            already_credited_for_outage=False,
        ),
    )

    assert decision.amount_inr == Decimal(credit)
    assert decision.requires_supervisor is (supervisor == "Yes")
    assert f"| {credit} | {supervisor} |" in DOCUMENT


def test_document_states_the_version_and_parameters_the_engine_uses() -> None:
    broadband = POLICY.services[ServiceType.BROADBAND]
    mobile = POLICY.services[ServiceType.MOBILE]

    assert f'version: "{POLICY.version}"' in DOCUMENT
    assert f"{broadband.min_outage_minutes} minutes for broadband" in DOCUMENT
    assert f"{mobile.min_outage_minutes:,} minutes for mobile" in DOCUMENT
    assert f"÷ {POLICY.hours_in_month} ×" in DOCUMENT  # noqa: RUF001 - as printed
    assert f"above INR {POLICY.approval.supervisor_above_inr}" in DOCUMENT
    assert f"INR {broadband.minimum_credit_inr} (broadband)" in DOCUMENT
    assert f"INR {mobile.minimum_credit_inr} (mobile)" in DOCUMENT
    for tier in broadband.tiers:
        assert f"| Broadband | {tier.from_minutes:,} | {tier.multiplier} |" in DOCUMENT
    for tier in mobile.tiers:
        assert f"| Mobile | {tier.from_minutes:,} | {tier.multiplier} |" in DOCUMENT


def test_document_is_labelled_as_a_placeholder() -> None:
    assert "not SkyLink's real policy" in DOCUMENT
