"""Fault injection through /admin/v1/chaos."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock
from tests.mock_oss.conftest import START, auth
from tests.mock_oss.test_billing_field import credit_body

Admin = Callable[..., dict[str, Any]]
STATUS = "/network/v1/nodes/BLR-OLT-0001/status"


def _read(client: TestClient) -> Any:
    return client.get(STATUS, headers=auth("diagnostics"))


def _write(client: TestClient, key: str = "w") -> Any:
    headers = {**auth("executor"), "Idempotency-Key": key}
    return client.post("/billing/v1/credits", json=credit_body(), headers=headers)


def _credits(client: TestClient) -> int:
    count: int = client.get("/admin/v1/state", headers=auth("admin")).json()["tables"]["credits"]
    return count


def _elapsed(clock: ManualClock) -> float:
    return (clock.now() - START).total_seconds()


@pytest.mark.parametrize("call", [_read, _write], ids=["read", "write"])
def test_down(client: TestClient, admin: Admin, call: Callable[[TestClient], Any]) -> None:
    admin("chaos", {"target": "network", "mode": "down"})
    admin("chaos", {"target": "billing", "mode": "down"})
    resp = call(client)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert _credits(client) == 0


@pytest.mark.parametrize("call", [_read, _write], ids=["read", "write"])
def test_timeout(
    client: TestClient, admin: Admin, clock: ManualClock, call: Callable[[TestClient], Any]
) -> None:
    for target in ("network", "billing"):
        admin("chaos", {"target": target, "mode": "timeout", "hold_seconds": 7})
    resp = call(client)
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "GATEWAY_TIMEOUT"
    assert _elapsed(clock) == pytest.approx(7)
    assert _credits(client) == 0


def test_timeout_default_hold(client: TestClient, admin: Admin, clock: ManualClock) -> None:
    admin("chaos", {"target": "network", "mode": "timeout"})
    assert _read(client).status_code == 504
    assert _elapsed(clock) == pytest.approx(30)


@pytest.mark.parametrize(("call", "ok"), [(_read, 200), (_write, 201)], ids=["read", "write"])
def test_latency(
    client: TestClient,
    admin: Admin,
    clock: ManualClock,
    call: Callable[[TestClient], Any],
    ok: int,
) -> None:
    for target in ("network", "billing"):
        admin("chaos", {"target": target, "mode": "latency", "latency_ms": 250})
    assert call(client).status_code == ok
    assert _elapsed(clock) == pytest.approx(0.25)


@pytest.mark.parametrize("call", [_read, _write], ids=["read", "write"])
def test_error_rate(client: TestClient, admin: Admin, call: Callable[[TestClient], Any]) -> None:
    for target in ("network", "billing"):
        admin("chaos", {"target": target, "mode": "error_rate", "error_rate": 1.0})
    resp = call(client)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    for target in ("network", "billing"):
        admin("chaos", {"target": target, "mode": "error_rate", "error_rate": 0.0})
    assert call(client).status_code in (200, 201)


def test_response_loss_on_write_commits(client: TestClient, admin: Admin) -> None:
    admin("chaos", {"target": "billing", "mode": "response_loss"})
    lost = _write(client, key="lost")
    assert lost.status_code == 504
    assert lost.json()["error"]["code"] == "GATEWAY_TIMEOUT"
    admin("chaos", {"target": "billing", "mode": "ok"})
    assert _credits(client) == 1
    found = client.get("/billing/v1/credits/by-key/lost", headers=auth("resolution"))
    assert found.status_code == 200
    replay = _write(client, key="lost")
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == found.json()
    assert _credits(client) == 1


def test_response_loss_on_read_is_timeout(
    client: TestClient, admin: Admin, clock: ManualClock
) -> None:
    admin("chaos", {"target": "network", "mode": "response_loss", "hold_seconds": 3})
    assert _read(client).status_code == 504
    assert _elapsed(clock) == pytest.approx(3)


def test_ttl_expiry(client: TestClient, admin: Admin, clock: ManualClock) -> None:
    admin("chaos", {"target": "network", "mode": "down", "ttl_seconds": 10})
    assert _read(client).status_code == 503
    rules = client.get("/admin/v1/chaos", headers=auth("admin")).json()["rules"]
    assert rules[0]["expires_at"] == "2026-11-15T04:00:10Z"
    clock.advance(10)
    assert _read(client).status_code == 200
    assert client.get("/admin/v1/chaos", headers=auth("admin")).json()["rules"] == []


def test_admin_and_health_unaffected(client: TestClient, admin: Admin) -> None:
    for target in ("network", "network_feed", "crm", "billing", "field", "incident", "notify"):
        admin("chaos", {"target": target, "mode": "down"})
    assert client.get("/health").status_code == 200
    assert client.get("/admin/v1/state", headers=auth("admin")).status_code == 200
    assert client.get("/crm/v1/customers/C0000001", headers=auth("triage")).status_code == 503


def test_network_and_feed_are_independent(client: TestClient, admin: Admin) -> None:
    admin("chaos", {"target": "network", "mode": "down"})
    assert client.get("/network/v1/events", headers=auth("ingest")).status_code == 200
    line = client.post(
        "/network/v1/line-tests", json={"service_id": "S0000001"}, headers=auth("diagnostics")
    )
    assert line.status_code == 503
    admin("chaos", {"target": "network", "mode": "ok"})
    admin("chaos", {"target": "network_feed", "mode": "down"})
    assert client.get("/network/v1/events", headers=auth("ingest")).status_code == 503
    assert _read(client).status_code == 200


def test_list_and_clear_rules(client: TestClient, admin: Admin) -> None:
    admin("chaos", {"target": "crm", "mode": "down"})
    admin("chaos", {"target": "crm", "mode": "latency", "latency_ms": 5})
    admin("chaos", {"target": "notify", "mode": "down"})
    rules = client.get("/admin/v1/chaos", headers=auth("admin")).json()["rules"]
    assert {(r["target"], r["mode"]) for r in rules} == {("crm", "latency"), ("notify", "down")}
    cleared = client.delete("/admin/v1/chaos", headers=auth("admin"))
    assert cleared.status_code == 200
    assert client.get("/admin/v1/chaos", headers=auth("admin")).json()["rules"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"target": "admin", "mode": "down"},
        {"target": "crm", "mode": "explode"},
        {"target": "crm", "mode": "error_rate", "error_rate": 1.5},
        {"target": "crm", "mode": "latency", "latency_ms": -1},
        {"target": "crm", "mode": "down", "ttl_seconds": 0},
    ],
)
def test_bad_chaos_rule(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/admin/v1/chaos", json=body, headers=auth("admin")).status_code == 422


def test_chaos_applies_before_auth_and_is_counted(client: TestClient, admin: Admin) -> None:
    admin("chaos", {"target": "crm", "mode": "down"})
    client.get("/crm/v1/customers/C0000001", headers=auth("triage"))
    state = client.get("/admin/v1/state", headers=auth("admin")).json()
    assert state["requests"]["crm"]["503"] == 1
