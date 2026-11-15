"""How the end of a credit is put to the customer, and what a person is told about it."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from skylink_care.db.actions import CREDIT_APPLY, Action, ActionStatus
from skylink_care.orchestrator.credit_nodes import (
    CREDIT_NOT_APPLIED,
    CREDIT_UNCONFIRMED,
    _amount_differs,
    _service_facts,
    credit_outcome,
    outcome_note,
)
from skylink_care.orchestrator.handover import handoff_summary
from skylink_care.orchestrator.routing import decide_route
from skylink_care.orchestrator.state import ContactState, Phase, Route, pending_credits

REF = "SKL-261115-000001-9"
NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)
KEY = "credit:S1:OUT-1"


def action(status: ActionStatus, **overrides: Any) -> Action:
    fields: dict[str, Any] = {
        "action_id": uuid4(),
        "action_key": KEY,
        "action_type": CREDIT_APPLY,
        "lane": 4,
        "contact_id": uuid4(),
        "episode_id": None,
        "requested_by": "agent:resolution",
        "request_body": {"amount_inr": "120.50", "service_id": "S1", "outage_id": "OUT-1"},
        "request_hash": "0" * 64,
        "inputs": {},
        "depends_on": None,
        "approval_level": "agent",
        "status": status,
        "policy_version": "v0-placeholder",
        "external_ref": None,
        "response_body": None,
        "attempts": 1,
        "available_at": NOW,
        "last_error": None,
        "correlation_id": "corr-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return Action(**{**fields, **overrides})


def test_applied_credit_is_confirmed_with_the_frozen_amount_and_its_reference() -> None:
    outcome = credit_outcome(REF, action(ActionStatus.SUCCEEDED, external_ref="CR-9"))

    assert "INR 120.50 for your outage has been applied" in outcome.reply.text
    assert "credit reference CR-9" in outcome.reply.text
    assert (outcome.status, outcome.follow_up) == ("SUCCEEDED", None)


def test_credit_skipped_because_one_is_there_already_needs_nobody() -> None:
    outcome = credit_outcome(REF, action(ActionStatus.SKIPPED, last_error="ALREADY_CREDITED"))

    assert "already on your account" in outcome.reply.text
    assert outcome.follow_up is None


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (ActionStatus.REJECTED, "approval REJECTED"),
        (ActionStatus.EXPIRED, "approval EXPIRED"),
        (ActionStatus.BLOCKED, "no agent approval on record for this exact request"),
        (ActionStatus.FAILED, "CAP_EXCEEDED"),
        (ActionStatus.FAILED, "CONFLICT: ACCOUNT_SUSPENDED"),
    ],
)
def test_credit_known_not_to_have_been_applied_goes_to_a_person(
    status: ActionStatus, error: str
) -> None:
    outcome = credit_outcome(REF, action(status, last_error=error))

    assert "has not been applied" in outcome.reply.text
    assert (outcome.status, outcome.follow_up) == (status.value, CREDIT_NOT_APPLIED)


def test_credit_the_executor_gave_up_on_is_not_reported_as_not_applied() -> None:
    gave_up = action(ActionStatus.FAILED, last_error="GAVE_UP after 31: TIMEOUT: no answer")

    outcome = credit_outcome(REF, gave_up)

    assert "could not confirm" in outcome.reply.text
    assert "has not been applied" not in outcome.reply.text
    assert (outcome.status, outcome.follow_up) == ("UNCONFIRMED", CREDIT_UNCONFIRMED)
    note = outcome_note(CREDIT_UNCONFIRMED, gave_up)
    assert f"INR 120.50 ({KEY})" in note
    assert "look for a credit under this key" in note


def test_note_on_a_credit_known_not_applied_does_not_send_anyone_looking() -> None:
    note = outcome_note(CREDIT_NOT_APPLIED, action(ActionStatus.REJECTED, last_error="approval"))

    assert "ended REJECTED: approval" in note
    assert "look for a credit" not in note


def test_credit_whose_action_cannot_be_found_is_treated_as_not_applied() -> None:
    outcome = credit_outcome(REF, None)

    assert (outcome.status, outcome.follow_up) == ("UNKNOWN", CREDIT_NOT_APPLIED)


# --- several credits on one contact --------------------------------------------------------------

FIRST, SECOND = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def waiting_on(*credits: dict[str, Any], finished: str) -> ContactState:
    event = {
        "event_type": "action.finished",
        "payload": {"action_id": finished, "status": "REJECTED"},
    }
    return {  # type: ignore[typeddict-item]
        "phase": Phase.NEW_TURN.value,
        "events": [event],
        "credits": list(credits),
    }


def credit(action_id: str, *, pending: bool = True, **more: Any) -> dict[str, Any]:
    return {"action_id": action_id, "pending": pending, **more}


def test_end_of_an_earlier_credit_is_noticed_after_a_later_one_was_proposed() -> None:
    state = waiting_on(credit(FIRST), credit(SECOND), finished=FIRST)

    assert decide_route(state).route is Route.CREDIT_RESULT
    assert len(pending_credits(state)) == 2


def test_credit_already_told_does_not_start_a_second_message() -> None:
    state = waiting_on(credit(FIRST, pending=False), credit(SECOND), finished=FIRST)

    assert decide_route(state).route is Route.AWAIT
    assert [item["action_id"] for item in pending_credits(state)] == [SECOND]


def test_person_taking_over_sees_every_credit_and_which_one_is_unconfirmed() -> None:
    def told(outage: str, amount: str, status: str | None, approval: str = "agent") -> Any:
        record = credit(outage, pending=status is None, approval=approval)
        record |= {"action_key": f"credit:S1:{outage}", "amount_inr": amount}
        return record | ({"status": status} if status else {})

    state: ContactState = {  # type: ignore[typeddict-item]
        "credits": [
            told("OUT-1", "50.00", "SUCCEEDED"),
            told("OUT-2", "75.00", "UNCONFIRMED"),
            told("OUT-3", "90.00", None, approval="supervisor"),
        ]
    }

    summary = handoff_summary(state, CREDIT_UNCONFIRMED)

    assert "INR 50.00 (credit:S1:OUT-1), SUCCEEDED, approval agent." in summary
    assert "INR 75.00 (credit:S1:OUT-2), UNCONFIRMED, approval agent." in summary
    assert "INR 90.00 (credit:S1:OUT-3), pending, approval supervisor." in summary
    assert summary.count("look for a credit under this key") == 1


@pytest.mark.parametrize(
    ("on_record", "differs"),
    [("120.50", False), ("120.5", False), ("120.49", True), (None, True), ("a lot", True)],
)
def test_amount_on_record_is_compared_as_money_not_as_text(
    on_record: str | None, differs: bool
) -> None:
    """An amount on record that cannot be read is treated as a difference: a person looks."""
    recorded = action(ActionStatus.READY, request_body={"amount_inr": on_record})
    worked_out = SimpleNamespace(amount_inr=Decimal("120.50"))

    assert _amount_differs(recorded, worked_out) is differs  # type: ignore[arg-type]


# --- the service a credit is worked out for ------------------------------------------------------


def service_state(**service: Any) -> ContactState:
    fields = {
        "service_id": "S1",
        "service_type": "broadband",
        "monthly_fee_inr": "999.00",
        "active": True,
        "billing_cycle_day": 10,
    }
    return {  # type: ignore[typeddict-item]
        "identity": {"customer_id": "C1"},
        "service": {**fields, **service},
    }


def test_service_with_a_usable_fee_and_cycle_is_assessed() -> None:
    facts = _service_facts(service_state())

    assert facts is not None
    assert (str(facts.monthly_fee_inr), facts.billing_cycle_day) == ("999.00", 10)


@pytest.mark.parametrize(
    "service",
    [
        {"monthly_fee_inr": "NaN"},
        {"monthly_fee_inr": "Infinity"},
        {"monthly_fee_inr": "0"},
        {"monthly_fee_inr": "-10"},
        {"monthly_fee_inr": "a lot"},
        {"monthly_fee_inr": None},
        {"billing_cycle_day": 29},
        {"billing_cycle_day": -1},
        {"billing_cycle_day": "tenth"},
    ],
    ids=str,
)
def test_fee_or_cycle_that_cannot_be_used_is_never_priced(service: dict[str, Any]) -> None:
    assert _service_facts(service_state(**service)) is None
