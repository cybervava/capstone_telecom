"""Outage lifecycle via the admin API and the NOC event feed."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock, iso
from tests.mock_oss.conftest import auth

Admin = Callable[..., dict[str, object]]


def _events(client: TestClient, query: str = "") -> dict[str, object]:
    resp = client.get(f"/network/v1/events{query}", headers=auth("ingest"))
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


def _event_list(client: TestClient, query: str = "") -> list[dict[str, object]]:
    events = _events(client, query)["events"]
    assert isinstance(events, list)
    return events


def _outage_events(client: TestClient, query: str = "") -> list[dict[str, object]]:
    return [e for e in _event_list(client, query) if str(e["type"]).startswith("OUTAGE_")]


def test_start_on_access_node(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0002", "cause": "EQUIPMENT_FAULT"})
    assert str(out["outage_id"]).startswith("OUT-20261115-")
    assert out["affected_node_ids"] == ["BLR-OLT-0002"]


def test_start_on_agg_fans_out_to_active_children(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-AGG-0001", "cause": "POWER_FAILURE"})
    assert out["affected_node_ids"] == ["BLR-OLT-0001", "BLR-OLT-0002", "BLR-TWR-0001"]
    (event,) = _outage_events(client)
    assert event["service_types"] == ["broadband", "mobile"]
    assert event["node_ids"] == ["BLR-OLT-0001", "BLR-OLT-0002", "BLR-TWR-0001"]
    status = client.get("/network/v1/nodes/BLR-TWR-0001/status", headers=auth("diagnostics"))
    assert status.json()["status"] == "OUTAGE"
    other = client.get("/network/v1/nodes/MUM-OLT-0001/status", headers=auth("diagnostics"))
    assert other.json()["status"] == "OPERATIONAL"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"node_id": "NOPE", "cause": "FIBRE_CUT"}, 404, "NODE_NOT_FOUND"),
        ({"node_id": "BLR-OLT-0003", "cause": "FIBRE_CUT"}, 410, "NODE_DECOMMISSIONED"),
        ({"node_id": "BLR-OLT-0001", "cause": "ALIENS"}, 422, "VALIDATION_ERROR"),
        (
            {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "eta_minutes": -1},
            422,
            "VALIDATION_ERROR",
        ),
    ],
)
def test_start_errors(client: TestClient, body: dict[str, object], status: int, code: str) -> None:
    resp = client.post("/admin/v1/outages", json=body, headers=auth("admin"))
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


def test_detection_delay(client: TestClient, admin: Admin, clock: ManualClock) -> None:
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 120})
    assert _outage_events(client) == []
    active = client.get("/admin/v1/outages?active=true", headers=auth("admin")).json()
    assert len(active["outages"]) == 1
    assert active["outages"][0]["detected"] is False
    clock.advance(119)
    assert _outage_events(client) == []
    clock.advance(1)
    (event,) = _outage_events(client)
    assert event["type"] == "OUTAGE_STARTED"
    assert event["emitted_at"] == "2026-11-15T04:02:00Z"
    assert event["started_at"] == "2026-11-15T04:00:00Z"
    assert event["version"] == 1
    assert str(event["event_id"]).startswith("evt_")
    assert len(str(event["event_id"])) == 20


def test_update_increments_version_and_eta(
    client: TestClient, admin: Admin, clock: ManualClock
) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "eta_minutes": 180})
    clock.advance(600)
    updated = admin(
        f"outages/{out['outage_id']}/update", {"eta_minutes": 60, "eta_source": "field_update"}
    )
    assert updated["version"] == 2
    assert updated["eta"] == "2026-11-15T05:10:00Z"
    events = _outage_events(client)
    assert [e["type"] for e in events] == ["OUTAGE_STARTED", "OUTAGE_UPDATED"]
    assert events[1]["version"] == 2
    assert events[1]["eta_source"] == "field_update"
    status = client.get("/network/v1/nodes/BLR-OLT-0001/status", headers=auth("diagnostics"))
    assert status.json()["outage"]["version"] == 2
    assert status.json()["outage"]["eta"] == "2026-11-15T05:10:00Z"


def test_update_before_detection_detects_first(client: TestClient, admin: Admin) -> None:
    out = admin(
        "outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 600}
    )
    admin(f"outages/{out['outage_id']}/update", {"eta_minutes": 30})
    assert [e["type"] for e in _outage_events(client)] == ["OUTAGE_STARTED", "OUTAGE_UPDATED"]


def test_resolve_is_idempotent(client: TestClient, admin: Admin, clock: ManualClock) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    clock.advance(3600)
    first = admin(f"outages/{out['outage_id']}/resolve", {})
    second = admin(f"outages/{out['outage_id']}/resolve", {})
    assert first == second
    assert first["resolved_at"] == "2026-11-15T05:00:00Z"
    events = _outage_events(client)
    assert [e["type"] for e in events] == ["OUTAGE_STARTED", "OUTAGE_RESOLVED"]
    assert events[1]["resolved_at"] == "2026-11-15T05:00:00Z"
    assert events[1]["version"] == 2
    status = client.get("/network/v1/nodes/BLR-OLT-0001/status", headers=auth("diagnostics"))
    assert status.json()["status"] == "OPERATIONAL"
    active = client.get("/admin/v1/outages?active=true", headers=auth("admin")).json()
    assert active["outages"] == []
    everything = client.get("/admin/v1/outages", headers=auth("admin")).json()
    assert len(everything["outages"]) == 1


def test_update_resolved_outage_conflicts(client: TestClient, admin: Admin) -> None:
    out = admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    admin(f"outages/{out['outage_id']}/resolve", {})
    resp = client.post(
        f"/admin/v1/outages/{out['outage_id']}/update",
        json={"eta_minutes": 5},
        headers=auth("admin"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "OUTAGE_RESOLVED"


@pytest.mark.parametrize("action", ["update", "resolve"])
def test_unknown_outage(client: TestClient, action: str) -> None:
    resp = client.post(
        f"/admin/v1/outages/OUT-0-0/{action}", json={"eta_minutes": 5}, headers=auth("admin")
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "OUTAGE_NOT_FOUND"


def test_feed_seq_paging_and_last_seq(client: TestClient, admin: Admin) -> None:
    for node in ["BLR-OLT-0001", "BLR-OLT-0002", "MUM-OLT-0001", "BLR-TWR-0001"]:
        admin("outages", {"node_id": node, "cause": "EQUIPMENT_FAULT"})
    page = _events(client, "?after_seq=0&limit=2")
    seqs = [e["seq"] for e in page["events"]]  # type: ignore[union-attr]
    assert seqs == [1, 2]
    assert page["last_seq"] == 8  # each start emits ALARM_RAISED then OUTAGE_STARTED
    assert page["generated_at"] == "2026-11-15T04:00:00Z"
    rest = _events(client, "?after_seq=2&limit=200")
    assert [e["seq"] for e in rest["events"]] == [3, 4, 5, 6, 7, 8]  # type: ignore[union-attr]
    empty = _events(client, "?after_seq=8")
    assert empty["events"] == []
    assert empty["last_seq"] == 8


@pytest.mark.parametrize(
    "query", ["?limit=0", "?limit=501", "?wait_seconds=26", "?after_seq=-1", "?limit=x"]
)
def test_feed_rejects_bad_params(client: TestClient, query: str) -> None:
    resp = client.get(f"/network/v1/events{query}", headers=auth("ingest"))
    assert resp.status_code == 422


def test_reset_closes_what_was_open_on_the_feed_and_keeps_its_history(
    client: TestClient, admin: Admin
) -> None:
    """A feed consumer must not be left believing in an outage the simulator has forgotten."""
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    admin("reset", {})
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"})
    body = _events(client)
    assert [(e["seq"], e["type"]) for e in body["events"]] == [  # type: ignore[union-attr]
        (1, "ALARM_RAISED"),
        (2, "OUTAGE_STARTED"),
        (3, "OUTAGE_RESOLVED"),
        (4, "ALARM_CLEARED"),
        (5, "ALARM_RAISED"),
        (6, "OUTAGE_STARTED"),
    ]
    assert body["last_seq"] == 6


def test_reset_forgets_an_undetected_outage_without_announcing_it(
    client: TestClient, admin: Admin
) -> None:
    admin("outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 600})
    admin("reset", {})
    types = [e["type"] for e in _events(client)["events"]]  # type: ignore[union-attr]
    assert types == ["ALARM_RAISED", "ALARM_CLEARED"]


@pytest.fixture
async def aclient(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://oss") as c:
        yield c


async def test_long_poll_returns_empty_after_wait(aclient: httpx.AsyncClient) -> None:
    started = time.monotonic()
    resp = await aclient.get("/network/v1/events?wait_seconds=1", headers=auth("ingest"))
    elapsed = time.monotonic() - started
    assert resp.json()["events"] == []
    assert 0.9 <= elapsed < 3


async def test_long_poll_returns_early_on_event(aclient: httpx.AsyncClient) -> None:
    async def start_outage() -> None:
        await asyncio.sleep(0.3)
        resp = await aclient.post(
            "/admin/v1/outages",
            json={"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT"},
            headers=auth("admin"),
        )
        assert resp.status_code == 201

    started = time.monotonic()
    poll = aclient.get("/network/v1/events?wait_seconds=10", headers=auth("ingest"))
    resp, _ = await asyncio.gather(poll, start_outage())
    assert time.monotonic() - started < 3
    assert [e["type"] for e in resp.json()["events"]] == ["ALARM_RAISED", "OUTAGE_STARTED"]


async def test_long_poll_sees_detection_from_clock(
    aclient: httpx.AsyncClient, clock: ManualClock
) -> None:
    resp = await aclient.post(
        "/admin/v1/outages",
        json={"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "detect_after_seconds": 60},
        headers=auth("admin"),
    )
    assert resp.status_code == 201

    async def advance() -> None:
        await asyncio.sleep(0.3)
        clock.advance(60)

    poll = aclient.get("/network/v1/events?after_seq=1&wait_seconds=10", headers=auth("ingest"))
    result, _ = await asyncio.gather(poll, advance())
    assert [e["type"] for e in result.json()["events"]] == ["OUTAGE_STARTED"]


def test_backdated_outage_began_in_the_past_and_its_events_say_so(
    client: TestClient, admin: Admin, clock: ManualClock
) -> None:
    now = clock.now()
    created = admin(
        "outages", {"node_id": "BLR-OLT-0001", "cause": "FIBRE_CUT", "started_minutes_ago": 300}
    )

    status = client.get("/network/v1/nodes/BLR-OLT-0001/status", headers=auth("diagnostics")).json()
    events = _events(client)["events"]

    began = iso(now - timedelta(minutes=300))
    assert status["status"] == "OUTAGE"
    assert status["outage"]["outage_id"] == created["outage_id"]
    assert status["outage"]["started_at"] == began
    started = next(e for e in events if e["type"] == "OUTAGE_STARTED")  # type: ignore[union-attr]
    assert (started["started_at"], started["emitted_at"]) == (began, began)
