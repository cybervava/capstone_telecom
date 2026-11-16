"""CRM endpoints."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock
from tests.mock_oss.conftest import auth

Admin = Callable[..., dict[str, object]]


def _get(client: TestClient, path: str) -> tuple[int, dict[str, object]]:
    # Each thing the CRM serves has its scope: a profile for triage, the consent record for
    # the executor, which sends the notices and may read nothing else here.
    who = "executor" if path.endswith("/consent") else "triage"
    resp = client.get(path, headers=auth(who))
    body: dict[str, object] = resp.json()
    return resp.status_code, body


@pytest.mark.parametrize(
    "query",
    [
        "phone=%2B919845000001",
        "phone=9845000001",
        "customer_id=C0000001",
        "email=asha.shah1@example.com",
        "email=ASHA.SHAH1@EXAMPLE.COM",
    ],
)
def test_lookup_single_match(client: TestClient, query: str) -> None:
    status, body = _get(client, f"/crm/v1/customers/lookup?{query}")
    assert status == 200
    assert body["matches"] == [
        {
            "customer_id": "C0000001",
            "name_masked": "A*** S***",
            "phone_masked": "+91******0001",
            "status": "active",
        }
    ]


def test_lookup_no_match(client: TestClient) -> None:
    status, body = _get(client, "/crm/v1/customers/lookup?phone=9000000000")
    assert status == 200
    assert body["matches"] == []


@pytest.mark.parametrize(
    "query",
    [
        "",
        "phone=9845000001&customer_id=C0000001",
        "phone=12345",
        "phone=%2B449845000001",
        "phone=",
    ],
)
def test_lookup_invalid(client: TestClient, query: str) -> None:
    status, body = _get(client, f"/crm/v1/customers/lookup?{query}")
    assert status == 400
    assert body["error"]["code"] == "INVALID_LOOKUP"  # type: ignore[index]


def test_profile(client: TestClient) -> None:
    status, body = _get(client, "/crm/v1/customers/C0000001")
    assert status == 200
    assert body["full_name"] == "Asha Shah"
    assert body["phone"] == "+919845000001"
    assert body["segment"] == "consumer"
    assert body["consent"] == {
        "outage_notifications": True,
        "channel": "sms",
        "updated_at": "2026-08-02T10:00:00Z",
    }
    services = body["services"]
    assert isinstance(services, list)
    assert [s["service_id"] for s in services] == ["S0000001", "S0000014"]
    assert services[0] == {
        "service_id": "S0000001",
        "service_type": "broadband",
        "plan_name": "Fibre 300",
        "monthly_fee_inr": "999.00",
        "status": "active",
        "address": {
            "address_id": "A0000001",
            "area": "Indiranagar",
            "city": "BLR",
            "pincode": "560038",
        },
        "router_model": "AL-AX18",
        "activated_on": "2024-03-11",
        "billing_cycle_day": 5,
    }
    assert services[1]["router_model"] is None
    assert "node_id" not in str(body)


def test_profile_not_found(client: TestClient) -> None:
    status, body = _get(client, "/crm/v1/customers/C9999999")
    assert status == 404
    assert body["error"]["code"] == "CUSTOMER_NOT_FOUND"  # type: ignore[index]


def test_consent_and_admin_flip(client: TestClient, admin: Admin, clock: ManualClock) -> None:
    status, body = _get(client, "/crm/v1/customers/C0000002/consent")
    assert status == 200
    assert body == {
        "outage_notifications": True,
        "channel": "push",
        "updated_at": "2026-08-02T10:00:00Z",
    }
    clock.advance(60)
    admin("consent", {"customer_id": "C0000002", "outage_notifications": False})
    _, after = _get(client, "/crm/v1/customers/C0000002/consent")
    assert after == {
        "outage_notifications": False,
        "channel": "none",
        "updated_at": "2026-11-15T04:01:00Z",
    }
    _, profile = _get(client, "/crm/v1/customers/C0000002")
    assert profile["consent"] == after
    admin("consent", {"customer_id": "C0000002", "outage_notifications": True})
    _, again = _get(client, "/crm/v1/customers/C0000002/consent")
    assert again["outage_notifications"] is True
    assert again["channel"] == "sms"


def test_consent_not_found(client: TestClient) -> None:
    status, _ = _get(client, "/crm/v1/customers/C9999999/consent")
    assert status == 404


def test_admin_consent_unknown_customer(client: TestClient) -> None:
    resp = client.post(
        "/admin/v1/consent",
        json={"customer_id": "C9", "outage_notifications": False},
        headers=auth("admin"),
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(("last4", "verified"), [("0001", True), ("0002", False)])
def test_verify(client: TestClient, last4: str, verified: bool) -> None:
    resp = client.post(
        "/crm/v1/customers/C0000001/verify", json={"phone_last4": last4}, headers=auth("triage")
    )
    assert resp.status_code == 200
    assert resp.json() == {"verified": verified}


@pytest.mark.parametrize("last4", ["12", "abcd", "12345"])
def test_verify_rejects_malformed(client: TestClient, last4: str) -> None:
    resp = client.post(
        "/crm/v1/customers/C0000001/verify", json={"phone_last4": last4}, headers=auth("triage")
    )
    assert resp.status_code == 422


def test_verify_unknown_customer(client: TestClient) -> None:
    resp = client.post(
        "/crm/v1/customers/C9999999/verify", json={"phone_last4": "0001"}, headers=auth("triage")
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"
