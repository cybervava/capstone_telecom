"""Billing credits and Field Service appointments."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock
from tests.mock_oss.conftest import auth, idem


def credit_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "customer_id": "C0000001",
        "service_id": "S0000001",
        "amount_inr": "812.50",
        "reason_code": "OUTAGE_CREDIT",
        "outage_id": "OUT-20261115-0001",
        "policy_version": "v0",
        "complaint_ref": "CMP-0001",
        "approved_by": None,
    }
    return {**body, **overrides}


def test_create_credit(client: TestClient) -> None:
    resp = client.post("/billing/v1/credits", json=credit_body(), headers=idem(key="cr-1"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["credit_id"].startswith("CR-")
    assert body["amount_inr"] == "812.50"
    assert body["status"] == "APPLIED"
    assert body["created_at"] == "2026-11-15T04:00:00Z"
    assert body["idempotency_key"] == "cr-1"
    assert body["outage_id"] == "OUT-20261115-0001"
    assert "Idempotent-Replay" not in resp.headers


@pytest.mark.parametrize("amount", ["0", "0.00", "-1", "5000.01", "1.234", "NaN", 5001])
def test_credit_invalid_amount(client: TestClient, amount: object) -> None:
    resp = client.post("/billing/v1/credits", json=credit_body(amount_inr=amount), headers=idem())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_AMOUNT"


@pytest.mark.parametrize("amount", ["5000.00", "0.01", 10])
def test_credit_boundary_amounts(client: TestClient, amount: object) -> None:
    resp = client.post("/billing/v1/credits", json=credit_body(amount_inr=amount), headers=idem())
    assert resp.status_code == 201


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"customer_id": "C9999999"}, 404, "CUSTOMER_NOT_FOUND"),
        ({"service_id": "S9999999"}, 404, "SERVICE_NOT_FOUND"),
        ({"service_id": "S0000005"}, 404, "SERVICE_NOT_FOUND"),  # belongs to another customer
        ({"customer_id": "C0000012", "service_id": "S0000013"}, 409, "ACCOUNT_SUSPENDED"),
    ],
)
def test_credit_errors(
    client: TestClient, overrides: dict[str, str], status: int, code: str
) -> None:
    resp = client.post("/billing/v1/credits", json=credit_body(**overrides), headers=idem())
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


def test_failed_create_is_not_recorded(client: TestClient) -> None:
    bad = client.post(
        "/billing/v1/credits", json=credit_body(customer_id="C9999999"), headers=idem(key="x")
    )
    assert bad.status_code == 404
    lookup = client.get("/billing/v1/credits/by-key/x", headers=auth("resolution"))
    assert lookup.status_code == 404


def test_list_credits_newest_first_and_since(client: TestClient, clock: ManualClock) -> None:
    client.post("/billing/v1/credits", json=credit_body(amount_inr="1.00"), headers=idem())
    clock.advance(86400 * 2)
    client.post("/billing/v1/credits", json=credit_body(amount_inr="2.00"), headers=idem())
    resp = client.get("/billing/v1/accounts/C0000001/credits", headers=auth("resolution"))
    assert [c["amount_inr"] for c in resp.json()["credits"]] == ["2.00", "1.00"]
    since = client.get(
        "/billing/v1/accounts/C0000001/credits?since=2026-11-16", headers=auth("resolution")
    )
    assert [c["amount_inr"] for c in since.json()["credits"]] == ["2.00"]
    other = client.get("/billing/v1/accounts/C0000002/credits", headers=auth("resolution"))
    assert other.json()["credits"] == []


def test_list_credits_since_is_a_day_in_ist(client: TestClient, clock: ManualClock) -> None:
    """The day starts at 00:00 IST, which is 18:30 UTC the day before."""
    clock.advance(14 * 3600 + 20 * 60)  # 2026-11-15T18:20:00Z, 23:50 IST on the 15th
    client.post("/billing/v1/credits", json=credit_body(amount_inr="1.00"), headers=idem())
    clock.advance(20 * 60)  # 2026-11-15T18:40:00Z, 00:10 IST on the 16th
    client.post("/billing/v1/credits", json=credit_body(amount_inr="2.00"), headers=idem())
    since = client.get(
        "/billing/v1/accounts/C0000001/credits?since=2026-11-16", headers=auth("resolution")
    )
    assert [c["amount_inr"] for c in since.json()["credits"]] == ["2.00"]


def test_list_credits_errors(client: TestClient) -> None:
    unknown = client.get("/billing/v1/accounts/C9999999/credits", headers=auth("resolution"))
    assert unknown.status_code == 404
    bad = client.get(
        "/billing/v1/accounts/C0000001/credits?since=yesterday", headers=auth("resolution")
    )
    assert bad.status_code == 422


def test_credit_by_key(client: TestClient) -> None:
    created = client.post("/billing/v1/credits", json=credit_body(), headers=idem(key="bk"))
    found = client.get("/billing/v1/credits/by-key/bk", headers=auth("resolution"))
    assert found.status_code == 200
    assert found.json() == created.json()
    missing = client.get("/billing/v1/credits/by-key/zz", headers=auth("resolution"))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- field service


def appt_body(slot_id: str = "SL-560038-20261116-AM", **overrides: Any) -> dict[str, Any]:
    body = {
        "customer_id": "C0000001",
        "service_id": "S0000001",
        "slot_id": slot_id,
        "reason": "ONT replacement",
        "complaint_ref": "CMP-0001",
    }
    return {**body, **overrides}


def _slots(client: TestClient, query: str) -> list[dict[str, Any]]:
    resp = client.get(f"/field/v1/slots?{query}", headers=auth("dispatch"))
    assert resp.status_code == 200, resp.text
    slots: list[dict[str, Any]] = resp.json()["slots"]
    return slots


def test_slots_next_five_days(client: TestClient) -> None:
    slots = _slots(client, "pincode=560038&from_date=2026-11-16")
    assert len(slots) == 10
    assert slots[0] == {
        "slot_id": "SL-560038-20261116-AM",
        "start": "2026-11-16T03:30:00Z",
        "end": "2026-11-16T07:30:00Z",
        "remaining": 3,
    }
    assert slots[1]["slot_id"] == "SL-560038-20261116-PM"
    assert slots[1]["start"] == "2026-11-16T08:30:00Z"
    assert slots[-1]["slot_id"] == "SL-560038-20261120-PM"


def test_slots_default_from_today_skips_started(client: TestClient) -> None:
    slots = _slots(client, "pincode=560038")
    assert slots[0]["slot_id"] == "SL-560038-20261115-PM"
    assert len(slots) == 9


def test_slots_window_is_bounded(client: TestClient) -> None:
    assert len(_slots(client, "pincode=560038&from_date=2026-11-19")) == 4
    assert _slots(client, "pincode=560038&from_date=2026-12-30") == []


@pytest.mark.parametrize("query", ["pincode=abc", "", "pincode=560038&from_date=x"])
def test_slots_bad_query(client: TestClient, query: str) -> None:
    resp = client.get(f"/field/v1/slots?{query}", headers=auth("dispatch"))
    assert resp.status_code == 422


def test_book_cancel_and_capacity(client: TestClient) -> None:
    ids = []
    for i in range(3):
        resp = client.post("/field/v1/appointments", json=appt_body(reason=f"r{i}"), headers=idem())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "BOOKED"
        assert body["start"] == "2026-11-16T03:30:00Z"
        assert body["end"] == "2026-11-16T07:30:00Z"
        ids.append(body["appointment_id"])
    full = client.post("/field/v1/appointments", json=appt_body(reason="r4"), headers=idem())
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "SLOT_FULL"
    assert _slots(client, "pincode=560038&from_date=2026-11-16")[0]["remaining"] == 0
    cancel = client.post(f"/field/v1/appointments/{ids[0]}/cancel", headers=auth("executor"))
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED"
    again = client.post(f"/field/v1/appointments/{ids[0]}/cancel", headers=auth("executor"))
    assert again.status_code == 200
    assert again.json()["status"] == "CANCELLED"
    assert _slots(client, "pincode=560038&from_date=2026-11-16")[0]["remaining"] == 1
    retry = client.post("/field/v1/appointments", json=appt_body(reason="r4"), headers=idem())
    assert retry.status_code == 201


@pytest.mark.parametrize(
    "slot_id",
    [
        "SL-560038-20261115-AM",
        "SL-560038-20261125-AM",
        "garbage",
        "SL-560038-20261116-XX",
        "SL-560038-20261399-AM",
    ],
)
def test_book_unknown_slot(client: TestClient, slot_id: str) -> None:
    resp = client.post("/field/v1/appointments", json=appt_body(slot_id), headers=idem())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SLOT_NOT_FOUND"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"customer_id": "C9999999"}, "CUSTOMER_NOT_FOUND"),
        ({"service_id": "S0000005"}, "SERVICE_NOT_FOUND"),
    ],
)
def test_book_unknown_customer_or_service(
    client: TestClient, overrides: dict[str, str], code: str
) -> None:
    resp = client.post("/field/v1/appointments", json=appt_body(**overrides), headers=idem())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == code


def test_cancel_unknown(client: TestClient) -> None:
    resp = client.post("/field/v1/appointments/AP-nope/cancel", headers=auth("executor"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "APPOINTMENT_NOT_FOUND"


def test_list_and_lookup_appointments(client: TestClient) -> None:
    made = client.post("/field/v1/appointments", json=appt_body(), headers=idem(key="ap"))
    appt_id = made.json()["appointment_id"]
    by_key = client.get("/field/v1/appointments/by-key/ap", headers=auth("dispatch"))
    assert by_key.json() == made.json()
    booked = client.get(
        "/field/v1/appointments?service_id=S0000001&status=BOOKED", headers=auth("dispatch")
    )
    assert [a["appointment_id"] for a in booked.json()["appointments"]] == [appt_id]
    client.post(f"/field/v1/appointments/{appt_id}/cancel", headers=auth("executor"))
    after = client.get(
        "/field/v1/appointments?service_id=S0000001&status=BOOKED", headers=auth("dispatch")
    )
    assert after.json()["appointments"] == []
    every = client.get("/field/v1/appointments?service_id=S0000001", headers=auth("dispatch"))
    assert every.json()["appointments"][0]["status"] == "CANCELLED"
    missing = client.get("/field/v1/appointments/by-key/none", headers=auth("dispatch"))
    assert missing.status_code == 404


def test_list_appointments_requires_service(client: TestClient) -> None:
    resp = client.get("/field/v1/appointments", headers=auth("dispatch"))
    assert resp.status_code == 422
