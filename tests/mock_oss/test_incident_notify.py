"""Incident Management and the Notification gateway."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.mock_oss.conftest import auth, idem


def incident_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "node_id": "BLR-OLT-0001",
        "service_type": "broadband",
        "kind": "NETWORK_OUTAGE",
        "outage_id": None,
        "summary": "Many customers report no internet",
    }
    return {**body, **overrides}


def _incident(client: TestClient, key: str | None = None, **overrides: Any) -> dict[str, Any]:
    resp = client.post(
        "/incident/v1/incidents", json=incident_body(**overrides), headers=idem(key=key)
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def test_create_incident(client: TestClient) -> None:
    body = _incident(client)
    assert body["incident_id"].startswith("INC-")
    assert body["status"] == "OPEN"
    assert body["complaint_refs"] == []
    assert body["created_at"] == "2026-11-15T04:00:00Z"


@pytest.mark.parametrize(
    ("overrides", "status"),
    [({"kind": "OTHER"}, 422), ({"service_type": "tv"}, 422), ({"node_id": "NOPE"}, 404)],
)
def test_create_incident_errors(client: TestClient, overrides: dict[str, str], status: int) -> None:
    resp = client.post("/incident/v1/incidents", json=incident_body(**overrides), headers=idem())
    assert resp.status_code == status


def test_no_dedup_by_node(client: TestClient) -> None:
    first = _incident(client, key="a")
    second = _incident(client, key="b")
    assert first["incident_id"] != second["incident_id"]
    listed = client.get(
        "/incident/v1/incidents?node_id=BLR-OLT-0001&status=OPEN", headers=auth("executor")
    )
    assert len(listed.json()["incidents"]) == 2
    other = client.get("/incident/v1/incidents?node_id=MUM-OLT-0001", headers=auth("executor"))
    assert other.json()["incidents"] == []


def test_get_incident_and_by_key(client: TestClient) -> None:
    made = _incident(client, key="inc-k")
    got = client.get(f"/incident/v1/incidents/{made['incident_id']}", headers=auth("executor"))
    assert got.json() == made
    by_key = client.get("/incident/v1/incidents/by-key/inc-k", headers=auth("executor"))
    assert by_key.json() == made
    assert client.get("/incident/v1/incidents/INC-x", headers=auth("executor")).status_code == 404
    missing = client.get("/incident/v1/incidents/by-key/zz", headers=auth("executor"))
    assert missing.status_code == 404


def _link(client: TestClient, incident_id: str, ref: str, key: str | None = None) -> Any:
    return client.post(
        f"/incident/v1/incidents/{incident_id}/complaints",
        json={"complaint_ref": ref, "contact_id": "H0000001", "customer_id": None},
        headers=idem(key=key),
    )


def test_link_complaints(client: TestClient) -> None:
    inc = _incident(client)
    first = _link(client, inc["incident_id"], "CMP-1")
    assert first.status_code == 201
    assert first.json()["complaint_ref"] == "CMP-1"
    duplicate = _link(client, inc["incident_id"], "CMP-1")
    assert duplicate.status_code == 200
    _link(client, inc["incident_id"], "CMP-2")
    got = client.get(f"/incident/v1/incidents/{inc['incident_id']}", headers=auth("executor"))
    assert got.json()["complaint_refs"] == ["CMP-1", "CMP-2"]
    state = client.get("/admin/v1/state", headers=auth("admin")).json()
    assert state["tables"]["incident_complaints"] == 2


def test_link_unknown_incident(client: TestClient) -> None:
    resp = _link(client, "INC-nope", "CMP-1")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "INCIDENT_NOT_FOUND"


def test_link_by_key(client: TestClient) -> None:
    inc = _incident(client)
    made = _link(client, inc["incident_id"], "CMP-9", key="lk")
    found = client.get(
        f"/incident/v1/incidents/{inc['incident_id']}/complaints/by-key/lk",
        headers=auth("executor"),
    )
    assert found.status_code == 200
    assert found.json() == made.json()


def complaint_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "complaint_ref": "CMP-2026-0001",
        "received_at": "2026-11-15T09:40:00+05:30",
        "channel": "chat",
        "category": "outage",
        "customer_id": "C0000001",
    }
    return {**body, **overrides}


def test_register_complaint(client: TestClient) -> None:
    resp = client.post("/incident/v1/complaints", json=complaint_body(), headers=idem(key="c1"))
    assert resp.status_code == 201
    assert resp.json() == {
        "complaint_ref": "CMP-2026-0001",
        "registered_at": "2026-11-15T04:00:00Z",
    }
    got = client.get("/incident/v1/complaints/CMP-2026-0001", headers=auth("executor"))
    assert got.status_code == 200
    assert got.json()["received_at"] == "2026-11-15T04:10:00Z"
    assert got.json()["customer_id"] == "C0000001"
    by_key = client.get("/incident/v1/complaints/by-key/c1", headers=auth("executor"))
    assert by_key.json() == got.json()


def test_register_complaint_same_body_new_key(client: TestClient) -> None:
    client.post("/incident/v1/complaints", json=complaint_body(), headers=idem())
    again = client.post("/incident/v1/complaints", json=complaint_body(), headers=idem())
    assert again.status_code == 200
    state = client.get("/admin/v1/state", headers=auth("admin")).json()
    assert state["tables"]["complaints"] == 1


def test_register_complaint_conflict(client: TestClient) -> None:
    client.post("/incident/v1/complaints", json=complaint_body(), headers=idem())
    resp = client.post(
        "/incident/v1/complaints", json=complaint_body(category="billing"), headers=idem()
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "COMPLAINT_REF_CONFLICT"


def test_complaint_not_found(client: TestClient) -> None:
    resp = client.get("/incident/v1/complaints/nope", headers=auth("executor"))
    assert resp.status_code == 404


def test_complaint_requires_aware_timestamp(client: TestClient) -> None:
    resp = client.post(
        "/incident/v1/complaints",
        json=complaint_body(received_at="2026-11-15T09:40:00"),
        headers=idem(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- notify


def message_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "customer_id": "C0000001",
        "channel": "sms",
        "template_id": "OUTAGE_ACK",
        "body": "We are aware of an outage in your area.",
        "outage_id": "OUT-20261115-0001",
    }
    return {**body, **overrides}


def test_send_and_list_messages(client: TestClient) -> None:
    resp = client.post("/notify/v1/messages", json=message_body(), headers=idem(key="m1"))
    assert resp.status_code == 202
    body = resp.json()
    assert body["message_id"].startswith("MSG-")
    assert body["status"] == "ACCEPTED"
    assert body["created_at"] == "2026-11-15T04:00:00Z"
    client.post("/notify/v1/messages", json=message_body(channel="push"), headers=idem())
    client.post("/notify/v1/messages", json=message_body(outage_id=None), headers=idem())
    by_outage = client.get(
        "/notify/v1/messages?outage_id=OUT-20261115-0001", headers=auth("executor")
    )
    msgs = by_outage.json()["messages"]
    assert [m["channel"] for m in msgs] == ["push", "sms"]
    assert msgs[1]["message_id"] == body["message_id"]
    limited = client.get(
        "/notify/v1/messages?customer_id=C0000001&limit=1", headers=auth("executor")
    )
    assert len(limited.json()["messages"]) == 1
    by_key = client.get("/notify/v1/messages/by-key/m1", headers=auth("executor"))
    assert by_key.json()["message_id"] == body["message_id"]


@pytest.mark.parametrize(
    ("overrides", "status"),
    [({"channel": "email"}, 422), ({"customer_id": "C9999999"}, 404)],
)
def test_send_message_errors(client: TestClient, overrides: dict[str, str], status: int) -> None:
    resp = client.post("/notify/v1/messages", json=message_body(**overrides), headers=idem())
    assert resp.status_code == status


@pytest.mark.parametrize("query", ["", "?limit=5", "?customer_id=C0000001&limit=0"])
def test_list_messages_needs_filter(client: TestClient, query: str) -> None:
    resp = client.get(f"/notify/v1/messages{query}", headers=auth("executor"))
    assert resp.status_code in (400, 422)


def test_preferred_channel_is_resolved_from_the_customers_consent_record(
    client: TestClient,
) -> None:
    """The caller need not know the channel: the gateway uses the one the customer chose."""
    for customer_id in ("C0000001", "C0000002"):
        resp = client.post(
            "/notify/v1/messages",
            json=message_body(customer_id=customer_id, channel="preferred"),
            headers=idem(),
        )
        assert resp.status_code == 202

    sms = client.get("/notify/v1/messages?customer_id=C0000001", headers=auth("executor"))
    push = client.get("/notify/v1/messages?customer_id=C0000002", headers=auth("executor"))
    assert [m["channel"] for m in sms.json()["messages"]] == ["sms"]
    assert [m["channel"] for m in push.json()["messages"]] == ["push"]


def test_preferred_channel_without_a_usable_channel_is_refused(client: TestClient) -> None:
    resp = client.post(
        "/notify/v1/messages",
        json=message_body(customer_id="C0000003", channel="preferred"),
        headers=idem(),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NO_PREFERRED_CHANNEL"
    listed = client.get("/notify/v1/messages?customer_id=C0000003", headers=auth("executor"))
    assert listed.json()["messages"] == []
