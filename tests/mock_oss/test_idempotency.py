"""Idempotency semantics on every creating POST."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.mock_oss.conftest import auth
from tests.mock_oss.test_billing_field import appt_body, credit_body
from tests.mock_oss.test_incident_notify import complaint_body, incident_body, message_body


@dataclass(frozen=True)
class Creator:
    name: str
    path: Callable[[TestClient], str]
    body: Callable[[], dict[str, Any]]
    altered: Callable[[], dict[str, Any]]
    status: int
    table: str
    lookup: Callable[[str], str] | None


def _incident_path(client: TestClient) -> str:
    resp = client.post(
        "/incident/v1/incidents",
        json=incident_body(),
        headers={**auth("executor"), "Idempotency-Key": "parent-incident"},
    )
    return f"/incident/v1/incidents/{resp.json()['incident_id']}/complaints"


def _link_body() -> dict[str, Any]:
    return {"complaint_ref": "CMP-L", "contact_id": "H1", "customer_id": None}


CREATORS = [
    Creator(
        "credit",
        lambda _: "/billing/v1/credits",
        credit_body,
        lambda: credit_body(amount_usd="1.00"),
        201,
        "credits",
        lambda k: f"/billing/v1/credits/by-key/{k}",
    ),
    Creator(
        "appointment",
        lambda _: "/field/v1/appointments",
        appt_body,
        lambda: appt_body(reason="other"),
        201,
        "appointments",
        lambda k: f"/field/v1/appointments/by-key/{k}",
    ),
    Creator(
        "incident",
        lambda _: "/incident/v1/incidents",
        incident_body,
        lambda: incident_body(summary="other"),
        201,
        "incidents",
        lambda k: f"/incident/v1/incidents/by-key/{k}",
    ),
    Creator(
        "link",
        _incident_path,
        _link_body,
        lambda: {**_link_body(), "contact_id": "H2"},
        201,
        "incident_complaints",
        None,
    ),
    Creator(
        "complaint",
        lambda _: "/incident/v1/complaints",
        complaint_body,
        lambda: complaint_body(channel="call"),
        201,
        "complaints",
        lambda k: f"/incident/v1/complaints/by-key/{k}",
    ),
    Creator(
        "message",
        lambda _: "/notify/v1/messages",
        message_body,
        lambda: message_body(body="other"),
        202,
        "messages",
        lambda k: f"/notify/v1/messages/by-key/{k}",
    ),
]

IDS = [c.name for c in CREATORS]


def _count(client: TestClient, table: str) -> int:
    count: int = client.get("/admin/v1/state", headers=auth("admin")).json()["tables"][table]
    return count


@pytest.mark.parametrize("creator", CREATORS, ids=IDS)
def test_missing_key_is_400(client: TestClient, creator: Creator) -> None:
    path = creator.path(client)
    before = _count(client, creator.table)
    resp = client.post(path, json=creator.body(), headers=auth("executor"))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert _count(client, creator.table) == before


@pytest.mark.parametrize("creator", CREATORS, ids=IDS)
def test_replay_returns_original(client: TestClient, creator: Creator) -> None:
    path = creator.path(client)
    headers = {**auth("executor"), "Idempotency-Key": "same"}
    first = client.post(path, json=creator.body(), headers=headers)
    assert first.status_code == creator.status, first.text
    count = _count(client, creator.table)
    replay = client.post(path, json=creator.body(), headers=headers)
    assert replay.status_code == creator.status
    assert replay.json() == first.json()
    assert replay.headers["Idempotent-Replay"] == "true"
    assert _count(client, creator.table) == count


@pytest.mark.parametrize("creator", CREATORS, ids=IDS)
def test_reused_key_with_different_body_is_409(client: TestClient, creator: Creator) -> None:
    path = creator.path(client)
    headers = {**auth("executor"), "Idempotency-Key": "reuse"}
    assert client.post(path, json=creator.body(), headers=headers).status_code == creator.status
    count = _count(client, creator.table)
    resp = client.post(path, json=creator.altered(), headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert _count(client, creator.table) == count


@pytest.mark.parametrize("creator", [c for c in CREATORS if c.lookup], ids=lambda c: c.name)
def test_lookup_by_key(client: TestClient, creator: Creator) -> None:
    path = creator.path(client)
    headers = {**auth("executor"), "Idempotency-Key": "lookup-me"}
    client.post(path, json=creator.body(), headers=headers)
    assert creator.lookup is not None
    resp = client.get(creator.lookup("lookup-me"), headers=auth("admin"))
    assert resp.status_code == 200
    missing = client.get(creator.lookup("never-used"), headers=auth("admin"))
    assert missing.status_code == 404


def test_keys_are_scoped_per_endpoint(client: TestClient) -> None:
    headers = {**auth("executor"), "Idempotency-Key": "shared"}
    credit = client.post("/billing/v1/credits", json=credit_body(), headers=headers)
    incident = client.post("/incident/v1/incidents", json=incident_body(), headers=headers)
    assert credit.status_code == 201
    assert incident.status_code == 201
    assert "Idempotent-Replay" not in incident.headers


def test_link_key_scoped_per_incident(client: TestClient) -> None:
    a = client.post(
        "/incident/v1/incidents",
        json=incident_body(),
        headers={**auth("executor"), "Idempotency-Key": "ia"},
    ).json()["incident_id"]
    b = client.post(
        "/incident/v1/incidents",
        json=incident_body(),
        headers={**auth("executor"), "Idempotency-Key": "ib"},
    ).json()["incident_id"]
    headers = {**auth("executor"), "Idempotency-Key": "lk"}
    first = client.post(
        f"/incident/v1/incidents/{a}/complaints", json=_link_body(), headers=headers
    )
    second = client.post(
        f"/incident/v1/incidents/{b}/complaints", json=_link_body(), headers=headers
    )
    assert first.status_code == 201
    assert second.status_code == 409


def test_semantically_equal_body_replays(client: TestClient) -> None:
    headers = {**auth("executor"), "Idempotency-Key": "norm"}
    first = client.post("/billing/v1/credits", json=credit_body(), headers=headers)
    reordered = dict(reversed(list(credit_body().items())))
    replay = client.post("/billing/v1/credits", json=reordered, headers=headers)
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json() == first.json()
