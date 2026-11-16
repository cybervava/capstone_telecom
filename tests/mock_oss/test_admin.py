"""Simulator controls: fixture finder, line faults, counters and reset."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.mock_oss.conftest import auth, idem
from tests.mock_oss.test_billing_field import credit_body

Admin = Callable[..., dict[str, Any]]


def _search(client: TestClient, query: str) -> list[dict[str, Any]]:
    resp = client.get(f"/admin/v1/customers/search?{query}", headers=auth("admin"))
    assert resp.status_code == 200, resp.text
    customers: list[dict[str, Any]] = resp.json()["customers"]
    return customers


@pytest.mark.parametrize(
    ("query", "service_ids"),
    [
        ("node_id=BLR-OLT-0002", ["S0000003", "S0000004"]),
        ("mapping_defect=ambiguous", ["S0000003"]),
        ("consent=false", ["S0000003"]),
        ("segment=sme", ["S0000006"]),
        ("status=suspended", ["S0000013"]),
        ("router_model=NX-R2&min_monthly_fee=3000", ["S0000004", "S0000006"]),
        ("service_type=mobile&node_id=MUM-TWR-0001", ["S0000007", "S0000011"]),
        ("service_type=broadband&limit=2", ["S0000001", "S0000002"]),
        ("node_id=NOPE", []),
    ],
)
def test_search_filters(client: TestClient, query: str, service_ids: list[str]) -> None:
    assert [c["service_id"] for c in _search(client, query)] == service_ids


def test_search_row_shape(client: TestClient) -> None:
    (row,) = _search(client, "mapping_defect=stale")
    assert row == {
        "customer_id": "C0000004",
        "service_id": "S0000004",
        "true_node_id": "BLR-OLT-0002",
        "mapped_node_ids": ["BLR-OLT-0003"],
        "router_model": "NX-R2",
        "monthly_fee_inr": "3999.00",
        "consent": True,
        "mapping_defect": "stale",
        "phone": "+919845000004",
    }
    (missing,) = _search(client, "mapping_defect=missing")
    assert missing["mapped_node_ids"] == []
    (ambiguous,) = _search(client, "mapping_defect=ambiguous")
    assert ambiguous["mapped_node_ids"] == ["BLR-OLT-0001", "BLR-OLT-0002"]


def test_search_consent_reflects_runtime_flip(client: TestClient, admin: Admin) -> None:
    admin("consent", {"customer_id": "C0000004", "outage_notifications": False})
    assert [c["service_id"] for c in _search(client, "consent=false")] == ["S0000003", "S0000004"]


@pytest.mark.parametrize(
    "query", ["limit=51", "limit=0", "consent=maybe", "mapping_defect=odd", "min_monthly_fee=x"]
)
def test_search_rejects_bad_filters(client: TestClient, query: str) -> None:
    resp = client.get(f"/admin/v1/customers/search?{query}", headers=auth("admin"))
    assert resp.status_code == 422


def test_line_fault_unknown_service(client: TestClient) -> None:
    resp = client.post(
        "/admin/v1/line-faults", json={"service_id": "S9", "fault": True}, headers=auth("admin")
    )
    assert resp.status_code == 404


def test_line_fault_rejects_bad_result(client: TestClient) -> None:
    resp = client.post(
        "/admin/v1/line-faults",
        json={"service_id": "S0000001", "fault": True, "result": "PASS"},
        headers=auth("admin"),
    )
    assert resp.status_code == 422


def test_state_counters(client: TestClient) -> None:
    client.post("/billing/v1/credits", json=credit_body(), headers=idem())
    client.get("/crm/v1/customers/C0000001", headers=auth("triage"))
    client.get("/crm/v1/customers/C9999999", headers=auth("triage"))
    client.get("/crm/v1/customers/C0000001", headers={"X-API-Key": "bad"})
    state = client.get("/admin/v1/state", headers=auth("admin")).json()
    assert state["tables"] == {
        "credits": 1,
        "appointments": 0,
        "incidents": 0,
        "incident_complaints": 0,
        "complaints": 0,
        "messages": 0,
    }
    assert state["requests"]["crm"] == {"200": 1, "404": 1, "401": 1}
    assert state["requests"]["billing"] == {"201": 1}
    assert "admin" not in state["requests"]


def test_reset_clears_runtime_but_not_reference(client: TestClient, admin: Admin) -> None:
    client.post("/billing/v1/credits", json=credit_body(), headers=idem(key="r1"))
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    admin("line-faults", {"service_id": "S0000006", "fault": True})
    admin("consent", {"customer_id": "C0000001", "outage_notifications": False})
    admin("chaos", {"target": "crm", "mode": "latency", "latency_ms": 1})
    admin("reset", {})
    state = client.get("/admin/v1/state", headers=auth("admin")).json()
    assert set(state["tables"].values()) == {0}
    assert state["requests"] == {}
    assert client.get("/admin/v1/outages", headers=auth("admin")).json()["outages"] == []
    assert client.get("/admin/v1/chaos", headers=auth("admin")).json()["rules"] == []
    consent = client.get("/crm/v1/customers/C0000001/consent", headers=auth("executor")).json()
    assert consent["outage_notifications"] is True
    line = client.post(
        "/network/v1/line-tests", json={"service_id": "S0000006"}, headers=auth("diagnostics")
    )
    assert line.json()["result"] == "PASS"
    assert client.get("/billing/v1/credits/by-key/r1", headers=auth("admin")).status_code == 404
    again = client.post("/billing/v1/credits", json=credit_body(), headers=idem(key="r1"))
    assert again.status_code == 201
    assert "Idempotent-Replay" not in again.headers
    assert client.get("/health").json()["customers"] == 12
