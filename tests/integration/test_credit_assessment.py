"""The assessment of a credit, with Billing scripted and the ledger real.

What must hold: credits that are proposed and not yet settled count towards the billing-cycle
cap, each of them once, and the credit being assessed never counts against itself.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from skylink_care.adapters.models import CreditList
from skylink_care.adapters.result import Err, ErrorKind, Result
from skylink_care.agents.resolution import ResolutionAgent, ServiceFacts
from skylink_care.db.actions import ActionRepository
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.domain.credit import CreditReason
from skylink_care.domain.models import OutageInfo
from skylink_care.observability.audit import Auditor
from tests.support.credits import (
    MONTHLY_FEE,
    NOW,
    a_contact,
    account,
    credit,
    decide,
    policy,
    proposed,
)

SERVICE = ServiceFacts(
    customer_id="C1",
    service_id="S1",
    service_type="broadband",
    monthly_fee_usd=MONTHLY_FEE,
    active=True,
    billing_cycle_day=10,
)
# Five hours, resolved: $6.94 by the formula, raised to the $50 minimum.
OUTAGE = OutageInfo(
    outage_id="OUT-1",
    version=2,
    cause="FIBRE_CUT",
    planned=False,
    started_at=NOW - timedelta(hours=5),
    eta=None,
    eta_source=None,
    resolved_at=NOW,
)
UNCAPPED = Decimal("50.00")
OTHER_OUTAGE = {"key": "credit:S1:OUT-0", "outage_id": "OUT-0"}


class AccountReader:
    def __init__(self, existing: Result[CreditList]) -> None:
        self.existing = existing
        self.asked_since: list[str | None] = []

    async def credits(
        self, customer_id: str, *, since: str | None = None, correlation_id: str
    ) -> Result[CreditList]:
        self.asked_since.append(since)
        return self.existing


async def assess(db: Database, billing: AccountReader, *, with_ledger: bool = True) -> Any:
    agent = ResolutionAgent(
        billing,  # type: ignore[arg-type]
        policy(),
        review_all=True,
        ledger=ActionRepository(db) if with_ledger else None,
    )
    audit = Auditor(AuditRepository(db), contact_id=None, correlation_id="corr-1")
    return await agent.assess(SERVICE, OUTAGE, now=NOW, audit=audit)


async def test_credit_is_priced_against_the_cycle_that_contains_today(db: Database) -> None:
    billing = AccountReader(account())

    assessment = await assess(db, billing)

    assert assessment.decision.amount_usd == UNCAPPED
    assert billing.asked_since == ["2026-11-10"]  # cycle day 10; it is the 15th in India


async def test_credit_waiting_for_approval_counts_towards_the_cap(
    db: Database, contacts: ContactRepository
) -> None:
    """Two outages in one billing cycle are not each priced as if the other did not exist."""
    await proposed(db, await a_contact(contacts), amount_usd="960.00", **OTHER_OUTAGE)

    assessment = await assess(db, AccountReader(account()))

    assert assessment.decision.amount_usd == MONTHLY_FEE - Decimal("960.00")
    assert assessment.decision.calculation.cap_applied


async def test_approved_credit_on_its_way_counts_and_leaves_nothing_under_the_cap(
    db: Database, contacts: ContactRepository
) -> None:
    on_its_way = await proposed(
        db, await a_contact(contacts), level="supervisor", amount_usd="999.00", **OTHER_OUTAGE
    )
    await decide(db, on_its_way, role="supervisor")

    assessment = await assess(db, AccountReader(account()))

    assert not assessment.eligible
    assert assessment.decision.reason is CreditReason.CAP_REACHED


async def test_credit_that_billing_already_shows_is_counted_once(
    db: Database, contacts: ContactRepository
) -> None:
    """Applied, but the executor has not recorded the outcome yet: the same money is in both
    places for a moment."""
    await proposed(db, await a_contact(contacts), amount_usd="960.00", **OTHER_OUTAGE)
    applied = credit(
        credit_id="CR-0",
        outage_id="OUT-0",
        amount_usd=Decimal("960.00"),
        idempotency_key="credit:S1:OUT-0",
    )

    assessment = await assess(db, AccountReader(account(applied)))

    assert assessment.decision.amount_usd == MONTHLY_FEE - Decimal("960.00")


@pytest.mark.parametrize(
    "other",
    [
        {"key": "credit:S1:OUT-1"},  # the credit being assessed, proposed in an earlier turn
        {"key": "credit:S2:OUT-0", "service_id": "S2", "outage_id": "OUT-0"},
    ],
    ids=["itself", "another service of the customer"],
)
async def test_what_does_not_count_towards_the_cap(
    db: Database, contacts: ContactRepository, other: dict[str, str]
) -> None:
    await proposed(db, await a_contact(contacts), amount_usd="960.00", **other)

    assessment = await assess(db, AccountReader(account()))

    assert assessment.decision.amount_usd == UNCAPPED


async def test_rejected_credit_no_longer_counts(db: Database, contacts: ContactRepository) -> None:
    rejected = await proposed(
        db, await a_contact(contacts), level="supervisor", amount_usd="960.00", **OTHER_OUTAGE
    )
    await decide(db, rejected, approve=False, role="supervisor")

    assessment = await assess(db, AccountReader(account()))

    assert assessment.decision.amount_usd == UNCAPPED


async def test_without_a_ledger_only_the_account_is_counted(
    db: Database, contacts: ContactRepository
) -> None:
    await proposed(db, await a_contact(contacts), amount_usd="960.00", **OTHER_OUTAGE)

    assessment = await assess(db, AccountReader(account()), with_ledger=False)

    assert assessment.decision.amount_usd == UNCAPPED


async def test_unreadable_account_is_not_an_assessment(db: Database) -> None:
    billing = AccountReader(Err(ErrorKind.UNAVAILABLE, "billing down", status_code=503))

    assessment = await assess(db, billing)

    assert assessment.decision is None
    assert not assessment.eligible
