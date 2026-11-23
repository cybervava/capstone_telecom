"""The demo console: its page, and the two reads it needs beyond the simulator controls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.mock_oss.conftest import auth, idem
from tests.mock_oss.test_billing_field import appt_body, credit_body
from tests.mock_oss.test_incident_notify import incident_body, message_body

Admin = Callable[..., dict[str, Any]]


def _activity(client: TestClient, query: str) -> dict[str, Any]:
    resp = client.get(f"/admin/v1/activity?{query}", headers=auth("admin"))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _notice(client: TestClient, **overrides: Any) -> None:
    resp = client.post("/notify/v1/messages", json=message_body(**overrides), headers=idem())
    assert resp.status_code == 202, resp.text


@pytest.mark.parametrize("path", ["/console/", "/console/console.js", "/console/console.css"])
def test_console_files_need_no_key_and_carry_strict_headers(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_console_page_is_the_console(client: TestClient) -> None:
    page = client.get("/console/")
    assert page.headers["content-type"].startswith("text/html")
    assert "<title>SkyLink Demo Console</title>" in page.text


def test_gateway_answers_are_left_as_they_were(client: TestClient) -> None:
    assert "Content-Security-Policy" not in client.get("/health").headers


@pytest.mark.parametrize(("client_name", "status"), [(None, 401), ("executor", 403)])
def test_console_reads_need_the_admin_key(
    client: TestClient, client_name: str | None, status: int
) -> None:
    headers = auth(client_name) if client_name else {}
    for path in ("/admin/v1/activity?customer_id=C0000001", "/admin/v1/line-faults"):
        assert client.get(path, headers=headers).status_code == status


def test_line_faults_in_force_are_listed(client: TestClient, admin: Admin) -> None:
    admin("line-faults", {"service_id": "S0000002", "fault": True})
    admin("line-faults", {"service_id": "S0000001", "fault": True, "result": "INCONCLUSIVE"})
    admin("line-faults", {"service_id": "S0000004", "fault": True})
    admin("line-faults", {"service_id": "S0000004", "fault": False})
    listed = client.get("/admin/v1/line-faults", headers=auth("admin"))
    assert listed.json() == {
        "line_faults": [
            {"service_id": "S0000001", "result": "INCONCLUSIVE"},
            {"service_id": "S0000002", "result": "FAIL"},
        ]
    }


def test_activity_needs_something_to_look_at(client: TestClient) -> None:
    resp = client.get("/admin/v1/activity", headers=auth("admin"))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "FILTER_REQUIRED"


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("outage_id=OUT-NOPE", "OUTAGE_NOT_FOUND"),
        ("customer_id=C9999999", "CUSTOMER_NOT_FOUND"),
        ("service_id=S9999999", "SERVICE_NOT_FOUND"),
        ("node_id=NOPE", "NODE_NOT_FOUND"),
    ],
)
def test_activity_of_something_unknown(client: TestClient, query: str, code: str) -> None:
    resp = client.get(f"/admin/v1/activity?{query}", headers=auth("admin"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == code


def test_activity_of_an_outage_says_who_was_told(client: TestClient, admin: Admin) -> None:
    outage_id = str(
        admin("outages", {"node_id": "BLR-OLT-0002", "cause": "FIBRE_CUT"})["outage_id"]
    )
    # C0000003, on that node, said no to outage notices; C0000004 said yes.
    _notice(client, customer_id="C0000004", outage_id=outage_id, template_id="outage_started.v1")
    _notice(client, customer_id="C0000003", outage_id=outage_id, template_id="outage_started.v1")
    _notice(client, customer_id="C0000004", outage_id=outage_id, template_id="outage_resolved.v1")
    _notice(client, customer_id="C0000001", outage_id="OUT-20261115-0099")

    outage = _activity(client, f"outage_id={outage_id}")["outage"]

    assert outage["outage_id"] == outage_id
    assert outage["notices"] == [
        {"template_id": "outage_resolved.v1", "sent": 1, "customers": 1},
        {"template_id": "outage_started.v1", "sent": 2, "customers": 2},
    ]
    assert outage["sent_without_consent"] == 1
    assert outage["declined_on_affected_nodes"] == 1
    assert [(m["customer_id"], m["template_id"]) for m in outage["latest"]] == [
        ("C0000004", "outage_resolved.v1"),
        ("C0000003", "outage_started.v1"),
        ("C0000004", "outage_started.v1"),
    ]


def test_activity_of_a_customer_a_line_and_a_node(client: TestClient) -> None:
    assert client.post("/billing/v1/credits", json=credit_body(), headers=idem()).status_code == 201
    assert (
        client.post("/field/v1/appointments", json=appt_body(), headers=idem()).status_code == 201
    )
    incident = client.post("/incident/v1/incidents", json=incident_body(), headers=idem()).json()
    linked = client.post(
        f"/incident/v1/incidents/{incident['incident_id']}/complaints",
        json={"complaint_ref": "CMP-1", "contact_id": "H0000001", "customer_id": None},
        headers=idem(),
    )
    assert linked.status_code == 201
    _notice(client)

    found = _activity(client, "customer_id=C0000001&service_id=S0000001&node_id=BLR-OLT-0001")

    assert found["outage"] is None
    assert [c["amount_inr"] for c in found["customer"]["credits"]] == ["812.50"]
    assert [m["template_id"] for m in found["customer"]["messages"]] == ["OUTAGE_ACK"]
    assert [a["slot_id"] for a in found["service"]["appointments"]] == ["SL-560038-20261116-AM"]
    assert [(i["incident_id"], i["complaint_refs"]) for i in found["node"]["incidents"]] == [
        (incident["incident_id"], ["CMP-1"])
    ]


def test_activity_lists_are_bounded(client: TestClient) -> None:
    for n in range(3):
        _notice(client, body=f"notice {n}")
    found = _activity(client, "customer_id=C0000001&limit=2")
    assert [m["body"] for m in found["customer"]["messages"]] == ["notice 2", "notice 1"]
    too_many = client.get("/admin/v1/activity?customer_id=C0000001&limit=51", headers=auth("admin"))
    assert too_many.status_code == 422
