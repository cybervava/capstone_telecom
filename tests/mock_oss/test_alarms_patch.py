"""Contract amendment: alarms in node status and the feed, and PATCH on incidents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock
from tests.mock_oss.conftest import auth, idem
from tests.mock_oss.test_incident_notify import incident_body

Admin = Callable[..., dict[str, Any]]


def _status(client: TestClient, node_id: str) -> dict[str, Any]:
    resp = client.get(f"/network/v1/nodes/{node_id}/status", headers=auth("diagnostics"))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _events(client: TestClient) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = client.get("/network/v1/events", headers=auth("ingest")).json()[
        "events"
    ]
    return events


def test_alarm_visible_during_detection_delay(client: TestClient, admin: Admin) -> None:
    out = admin(
        "outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 300}
    )
    body = _status(client, "BLR-OLT-0001")
    assert body["status"] == "OPERATIONAL"
    assert body["outage"] is None
    assert body["active_alarms"] == [
        {
            "alarm_id": out["alarm_id"],
            "node_id": "BLR-OLT-0001",
            "alarm_type": "LINK_DOWN",
            "severity": "critical",
            "raised_at": "2026-11-15T04:00:00Z",
        }
    ]
    assert body["feed_seq"] == 1
    assert _status(client, "BLR-OLT-0002")["active_alarms"] == []


def test_agg_alarm_visible_on_child(client: TestClient, admin: Admin) -> None:
    out = admin(
        "outages",
        {"node_id": "BLR-AGG-0001", "cause": "POWER_FAILURE", "detect_after_seconds": 300},
    )
    child = _status(client, "BLR-TWR-0001")
    assert child["status"] == "OPERATIONAL"
    (alarm,) = child["active_alarms"]
    assert alarm["alarm_id"] == out["alarm_id"]
    assert alarm["node_id"] == "BLR-AGG-0001"
    assert alarm["alarm_type"] == "POWER"
    assert _status(client, "BLR-AGG-0001")["active_alarms"][0]["alarm_id"] == out["alarm_id"]
    assert _status(client, "MUM-OLT-0001")["active_alarms"] == []


def test_alarm_event_precedes_outage_started(
    client: TestClient, admin: Admin, clock: ManualClock
) -> None:
    out = admin(
        "outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 60}
    )
    (alarm_event,) = _events(client)
    assert alarm_event["type"] == "ALARM_RAISED"
    assert alarm_event["alarm_id"] == out["alarm_id"]
    assert alarm_event["alarm_type"] == "LINK_DOWN"
    assert alarm_event["severity"] == "critical"
    assert alarm_event["node_ids"] == ["BLR-OLT-0001"]
    clock.advance(60)
    events = _events(client)
    assert [(e["seq"], e["type"]) for e in events] == [(1, "ALARM_RAISED"), (2, "OUTAGE_STARTED")]


def test_planned_outage_has_no_alarm(client: TestClient, admin: Admin) -> None:
    out = admin(
        "outages", {"node_id": "BLR-OLT-0001", "cause": "PLANNED_MAINTENANCE", "planned": True}
    )
    assert out["alarm_id"] is None
    assert [e["type"] for e in _events(client)] == ["OUTAGE_STARTED"]
    assert _status(client, "BLR-OLT-0001")["active_alarms"] == []


def test_resolve_clears_alarm(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    admin(f"outages/{out['outage_id']}/resolve", {})
    admin(f"outages/{out['outage_id']}/resolve", {})
    types = [e["type"] for e in _events(client)]
    assert types == ["ALARM_RAISED", "OUTAGE_STARTED", "OUTAGE_RESOLVED", "ALARM_CLEARED"]
    assert _status(client, "BLR-OLT-0001")["active_alarms"] == []


def test_standalone_alarm_raise_and_clear(client: TestClient, admin: Admin) -> None:
    raised = admin(
        "alarms", {"node_id": "BLR-OLT-0002", "alarm_type": "HIGH_BER", "severity": "minor"}
    )
    alarm_id = raised["alarm_id"]
    assert str(alarm_id).startswith("ALM-")
    status = _status(client, "BLR-OLT-0002")
    assert status["status"] == "OPERATIONAL"
    assert status["active_alarms"][0]["alarm_type"] == "HIGH_BER"
    first = admin(f"alarms/{alarm_id}/clear", {})
    second = admin(f"alarms/{alarm_id}/clear", {})
    assert first == second
    assert first["cleared_at"] == "2026-11-15T04:00:00Z"
    assert [e["type"] for e in _events(client)] == ["ALARM_RAISED", "ALARM_CLEARED"]
    assert _status(client, "BLR-OLT-0002")["active_alarms"] == []


def test_manual_clear_then_resolve_emits_single_clear(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    admin(f"alarms/{out['alarm_id']}/clear", {})
    admin(f"outages/{out['outage_id']}/resolve", {})
    assert [e["type"] for e in _events(client)].count("ALARM_CLEARED") == 1


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"node_id": "NOPE", "alarm_type": "LOS", "severity": "major"}, 404),
        ({"node_id": "BLR-OLT-0003", "alarm_type": "LOS", "severity": "major"}, 410),
        ({"node_id": "BLR-OLT-0001", "alarm_type": "SMOKE", "severity": "major"}, 422),
        ({"node_id": "BLR-OLT-0001", "alarm_type": "LOS", "severity": "huge"}, 422),
    ],
)
def test_raise_alarm_errors(client: TestClient, body: dict[str, str], status: int) -> None:
    assert client.post("/admin/v1/alarms", json=body, headers=auth("admin")).status_code == status


def test_clear_unknown_alarm(client: TestClient) -> None:
    resp = client.post("/admin/v1/alarms/ALM-nope/clear", json={}, headers=auth("admin"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ALARM_NOT_FOUND"


def test_reset_clears_alarms(client: TestClient, admin: Admin) -> None:
    admin("alarms", {"node_id": "BLR-OLT-0002", "alarm_type": "LOS", "severity": "critical"})
    admin("reset", {})
    assert _status(client, "BLR-OLT-0002")["active_alarms"] == []


# ---------------------------------------------------------------- PATCH incident


def _incident(client: TestClient) -> str:
    resp = client.post(
        "/incident/v1/incidents",
        json=incident_body(kind="SUSPECTED_NODE_FAULT"),
        headers=idem(),
    )
    incident_id: str = resp.json()["incident_id"]
    return incident_id


def _patch(client: TestClient, incident_id: str, body: object, who: str = "executor") -> Any:
    return client.patch(f"/incident/v1/incidents/{incident_id}", json=body, headers=auth(who))


def test_patch_attaches_outage_and_changes_kind(client: TestClient) -> None:
    incident_id = _incident(client)
    resp = _patch(client, incident_id, {"kind": "NETWORK_OUTAGE", "outage_id": "OUT-20261115-0001"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "NETWORK_OUTAGE"
    assert body["outage_id"] == "OUT-20261115-0001"
    assert body["status"] == "OPEN"
    assert body["summary"] == incident_body()["summary"]
    got = client.get(f"/incident/v1/incidents/{incident_id}", headers=auth("executor"))
    assert got.json() == body


def test_patch_status_and_summary_idempotent(client: TestClient) -> None:
    incident_id = _incident(client)
    first = _patch(client, incident_id, {"status": "RESOLVED", "summary": "fixed"})
    second = _patch(client, incident_id, {"status": "RESOLVED", "summary": "fixed"})
    assert first.json() == second.json()
    assert first.json()["status"] == "RESOLVED"
    open_list = client.get("/incident/v1/incidents?status=OPEN", headers=auth("executor"))
    assert open_list.json()["incidents"] == []


def test_patch_can_null_outage_id(client: TestClient) -> None:
    incident_id = _incident(client)
    _patch(client, incident_id, {"outage_id": "OUT-1"})
    assert _patch(client, incident_id, {"outage_id": None}).json()["outage_id"] is None


@pytest.mark.parametrize(
    "body", [{}, {"status": "CLOSED"}, {"kind": "OTHER"}, {"color": "red"}, {"summary": None}]
)
def test_patch_rejects_bad_body(client: TestClient, body: dict[str, Any]) -> None:
    resp = _patch(client, _incident(client), body)
    assert resp.status_code == 422


def test_patch_unknown_incident(client: TestClient) -> None:
    resp = _patch(client, "INC-nope", {"status": "RESOLVED"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "INCIDENT_NOT_FOUND"


@pytest.mark.parametrize(
    ("who", "status"),
    [
        ("triage", 403),
        ("diagnostics", 403),
        ("resolution", 403),
        ("dispatch", 403),
        ("ingest", 403),
        ("executor", 200),
        ("admin", 200),
    ],
)
def test_patch_scope(client: TestClient, who: str, status: int) -> None:
    resp = _patch(client, _incident(client), {"status": "RESOLVED"}, who=who)
    assert resp.status_code == status


def test_patch_response_loss_commits(client: TestClient, admin: Admin) -> None:
    incident_id = _incident(client)
    admin("chaos", {"target": "incident", "mode": "response_loss"})
    resp = _patch(client, incident_id, {"status": "RESOLVED"})
    assert resp.status_code == 504
    admin("chaos", {"target": "incident", "mode": "ok"})
    got = client.get(f"/incident/v1/incidents/{incident_id}", headers=auth("executor"))
    assert got.json()["status"] == "RESOLVED"
