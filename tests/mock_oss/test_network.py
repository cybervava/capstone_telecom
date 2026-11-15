"""Network Monitoring: node status, line tests and the truth model."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock
from mock_oss.config import Settings
from mock_oss.main import create_app
from tests.mock_oss.conftest import START, auth

Admin = Callable[..., dict[str, object]]


def _status(client: TestClient, node_id: str) -> dict[str, object]:
    resp = client.get(f"/network/v1/nodes/{node_id}/status", headers=auth("diagnostics"))
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


def _line_test(client: TestClient, service_id: str) -> dict[str, object]:
    resp = client.post(
        "/network/v1/line-tests", json={"service_id": service_id}, headers=auth("diagnostics")
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


def test_status_operational(client: TestClient) -> None:
    body = _status(client, "BLR-OLT-0001")
    assert body["node_id"] == "BLR-OLT-0001"
    assert body["status"] == "OPERATIONAL"
    assert body["outage"] is None
    assert body["active_alarms"] == []
    assert body["observed_at"] == "2026-11-15T04:00:00Z"
    assert body["feed_seq"] == 0


def test_status_unknown_node(client: TestClient) -> None:
    resp = client.get("/network/v1/nodes/XXX-OLT-9999/status", headers=auth("diagnostics"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NODE_NOT_FOUND"


def test_status_decommissioned_node(client: TestClient) -> None:
    resp = client.get("/network/v1/nodes/BLR-OLT-0003/status", headers=auth("diagnostics"))
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "NODE_DECOMMISSIONED"


def test_status_shows_detected_outage(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "eta_minutes": 60})
    body = _status(client, "BLR-OLT-0001")
    assert body["status"] == "OUTAGE"
    outage = body["outage"]
    assert isinstance(outage, dict)
    assert outage == {
        "outage_id": out["outage_id"],
        "version": 1,
        "cause": "FIBRE_CUT",
        "planned": False,
        "service_types": ["broadband"],
        "started_at": "2026-11-15T04:00:00Z",
        "eta": "2026-11-15T05:00:00Z",
        "eta_source": "noc_estimate",
        "resolved_at": None,
    }
    assert body["feed_seq"] == 2
    alarms = body["active_alarms"]
    assert isinstance(alarms, list)
    assert [a["alarm_id"] for a in alarms] == [out["alarm_id"]]
    assert _status(client, "BLR-OLT-0002")["status"] == "OPERATIONAL"


def test_line_test_pass(client: TestClient) -> None:
    body = _line_test(client, "S0000001")
    assert body["result"] == "PASS"
    assert body["service_id"] == "S0000001"
    assert body["node_id"] == "BLR-OLT-0001"
    assert str(body["test_id"]).startswith("LT-")
    assert body["tested_at"] == "2026-11-15T04:00:00Z"
    checks = body["checks"]
    assert isinstance(checks, dict)
    assert checks["link"] == "UP"
    assert checks["sync"] is True
    assert -24 <= checks["optical_rx_dbm"] <= -14
    assert 0 <= checks["packet_loss_pct"] < 1


def test_line_test_unknown_service(client: TestClient) -> None:
    resp = client.post(
        "/network/v1/line-tests", json={"service_id": "S9999999"}, headers=auth("diagnostics")
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SERVICE_NOT_FOUND"


def test_line_test_fails_during_undetected_outage(
    client: TestClient, admin: Admin, clock: ManualClock
) -> None:
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 300})
    assert _status(client, "BLR-OLT-0001")["status"] == "OPERATIONAL"
    body = _line_test(client, "S0000001")
    assert body["result"] == "FAIL"
    assert body["checks"] == {
        "link": "DOWN",
        "optical_rx_dbm": None,
        "sync": False,
        "packet_loss_pct": 100.0,
    }
    clock.advance(301)
    assert _status(client, "BLR-OLT-0001")["status"] == "OUTAGE"


def test_line_test_uses_true_node_for_stale_mapping(client: TestClient, admin: Admin) -> None:
    # S0000004 is mapped to the decommissioned BLR-OLT-0003 but truly served by BLR-OLT-0002.
    admin("outages", {"node_id": "BLR-OLT-0002", "cause": "EQUIPMENT_FAULT"})
    body = _line_test(client, "S0000004")
    assert body["node_id"] == "BLR-OLT-0002"
    assert body["result"] == "FAIL"


def test_line_test_ambiguous_mapping_uses_true_node(client: TestClient, admin: Admin) -> None:
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "EQUIPMENT_FAULT"})
    # S0000003 is mapped to both OLT-0001 and OLT-0002, truly on OLT-0002.
    assert _line_test(client, "S0000003")["result"] == "PASS"


def test_line_test_individual_fault(client: TestClient, admin: Admin) -> None:
    admin("line-faults", {"service_id": "S0000001", "fault": True, "result": "FAIL"})
    assert _line_test(client, "S0000001")["result"] == "FAIL"
    assert _line_test(client, "S0000006")["result"] == "PASS"
    admin("line-faults", {"service_id": "S0000001", "fault": False})
    assert _line_test(client, "S0000001")["result"] == "PASS"


def test_line_test_inconclusive_only_when_injected(client: TestClient, admin: Admin) -> None:
    results = {str(_line_test(client, f"S{i:07d}")["result"]) for i in range(1, 15)}
    assert results == {"PASS"}
    admin("line-faults", {"service_id": "S0000008", "fault": True, "result": "INCONCLUSIVE"})
    body = _line_test(client, "S0000008")
    assert body["result"] == "INCONCLUSIVE"
    checks = body["checks"]
    assert isinstance(checks, dict)
    assert checks["link"] == "UNKNOWN"


def test_line_test_passes_after_resolution(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-TWR-0001", "cause": "TOWER_FAULT"})
    assert _line_test(client, "S0000008")["result"] == "FAIL"
    admin(f"outages/{out['outage_id']}/resolve", {})
    assert _line_test(client, "S0000008")["result"] == "PASS"


def test_line_test_ids_unique(client: TestClient) -> None:
    ids = {_line_test(client, "S0000001")["test_id"] for _ in range(5)}
    assert len(ids) == 5


@pytest.mark.parametrize("body", [{}, {"service_id": 5}, {"service_id": ""}])
def test_line_test_rejects_bad_body(client: TestClient, body: dict[str, object]) -> None:
    resp = client.post("/network/v1/line-tests", json=body, headers=auth("diagnostics"))
    assert resp.status_code == 422


def test_line_test_latency_uses_clock(settings: Settings, app: object) -> None:
    clock = ManualClock(START)
    slow = dataclasses.replace(settings, line_test_delay_ms=(300, 1500))
    with TestClient(create_app(slow, clock=clock)) as c:
        resp = c.post(
            "/network/v1/line-tests", json={"service_id": "S0000001"}, headers=auth("diagnostics")
        )
    assert resp.status_code == 200
    waited = (clock.now() - START).total_seconds()
    assert 0.3 <= waited <= 1.5
    expected = "2026-11-15T04:00:01Z" if waited >= 1 else "2026-11-15T04:00:00Z"
    assert resp.json()["tested_at"] == expected
