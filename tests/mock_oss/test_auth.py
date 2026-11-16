"""Auth, scopes, settings and request-id behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mock_oss.auth import CLIENT_SCOPES
from mock_oss.config import CLIENT_ENV_VARS, Settings, parse_delay
from mock_oss.main import create_app
from tests.mock_oss.conftest import KEYS, auth

# (method, path, json body or None, scope, idempotent?)
ENDPOINTS: list[tuple[str, str, dict[str, object] | None, str, bool]] = [
    ("GET", "/network/v1/nodes/BLR-OLT-0001/status", None, "network:read", False),
    ("POST", "/network/v1/line-tests", {"service_id": "S0000001"}, "network:test", False),
    ("GET", "/network/v1/events", None, "network:feed", False),
    ("GET", "/crm/v1/customers/lookup?customer_id=C0000001", None, "crm:profile", False),
    ("GET", "/crm/v1/customers/C0000001", None, "crm:profile", False),
    ("GET", "/crm/v1/customers/C0000001/consent", None, "crm:consent", False),
    ("POST", "/crm/v1/customers/C0000001/verify", {"phone_last4": "0001"}, "crm:verify", False),
    ("GET", "/billing/v1/accounts/C0000001/credits", None, "billing:read", False),
    (
        "POST",
        "/billing/v1/credits",
        {
            "customer_id": "C0000001",
            "service_id": "S0000001",
            "amount_inr": "10.00",
            "reason_code": "OUTAGE",
            "outage_id": None,
            "policy_version": "v0",
            "complaint_ref": "CMP-1",
            "approved_by": None,
        },
        "billing:write",
        True,
    ),
    ("GET", "/billing/v1/credits/by-key/none", None, "billing:read", False),
    ("GET", "/field/v1/slots?pincode=560038", None, "field:read", False),
    ("GET", "/field/v1/appointments?service_id=S0000001", None, "field:read", False),
    ("GET", "/field/v1/appointments/by-key/none", None, "field:read", False),
    ("POST", "/field/v1/appointments/AP-none/cancel", None, "field:write", False),
    ("GET", "/incident/v1/incidents", None, "incident:read", False),
    ("GET", "/incident/v1/incidents/by-key/none", None, "incident:read", False),
    (
        "POST",
        "/incident/v1/incidents",
        {
            "node_id": "BLR-OLT-0001",
            "service_type": "broadband",
            "kind": "SUSPECTED_NODE_FAULT",
            "outage_id": None,
            "summary": "x",
        },
        "incident:write",
        True,
    ),
    ("GET", "/incident/v1/complaints/CMP-none", None, "incident:read", False),
    (
        "POST",
        "/notify/v1/messages",
        {
            "customer_id": "C0000001",
            "channel": "sms",
            "template_id": "T1",
            "body": "hi",
            "outage_id": None,
        },
        "notify:write",
        True,
    ),
    ("GET", "/notify/v1/messages?customer_id=C0000001", None, "notify:write", False),
    ("GET", "/admin/v1/state", None, "admin", False),
    ("GET", "/admin/v1/outages", None, "admin", False),
    ("GET", "/admin/v1/chaos", None, "admin", False),
]

CASES = [(client, ep) for client in sorted(CLIENT_ENV_VARS) for ep in ENDPOINTS]


def _call(
    client: TestClient, ep: tuple[str, str, dict[str, object] | None, str, bool], name: str, n: int
) -> int:
    method, path, body, _, idempotent = ep
    headers = auth(name)
    if idempotent:
        headers["Idempotency-Key"] = f"scope-{name}-{n}"
    return client.request(method, path, json=body, headers=headers).status_code


@pytest.mark.parametrize(("name", "endpoint"), CASES, ids=[f"{c}-{e[1]}" for c, e in CASES])
def test_scope_matrix(
    client: TestClient, name: str, endpoint: tuple[str, str, dict[str, object] | None, str, bool]
) -> None:
    status = _call(client, endpoint, name, CASES.index((name, endpoint)))
    if endpoint[3] in CLIENT_SCOPES[name]:
        assert status not in (401, 403)
    else:
        assert status == 403


def test_scope_table_matches_contract() -> None:
    assert CLIENT_SCOPES["triage"] == frozenset({"crm:profile", "crm:verify"})
    # The executor asks one thing of the CRM: may this customer be sent a notice.
    assert {s for s in CLIENT_SCOPES["executor"] if s.startswith("crm:")} == {"crm:consent"}
    # Nobody but triage reads a profile or checks what a caller knows.
    for client, scopes in CLIENT_SCOPES.items():
        if client not in {"triage", "admin"}:
            assert not scopes & {"crm:profile", "crm:verify"}, client
    # An identity nobody uses is a key somebody can lose.
    assert "notification" not in CLIENT_SCOPES
    assert CLIENT_SCOPES["ingest"] == frozenset({"network:feed"})
    assert "network:test" not in CLIENT_SCOPES["executor"]
    every = frozenset().union(*(v for k, v in CLIENT_SCOPES.items() if k != "admin"))
    assert CLIENT_SCOPES["admin"] == every | {"admin"}


def test_forbidden_error_body(client: TestClient) -> None:
    resp = client.get("/network/v1/nodes/BLR-OLT-0001/status", headers=auth("triage"))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "nope"}, {"X-API-Key": ""}])
def test_missing_or_unknown_key_is_401(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.get("/crm/v1/customers/C0000001", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_client_with_unset_key_cannot_authenticate(app_without_triage: TestClient) -> None:
    resp = app_without_triage.get("/crm/v1/customers/C0000001", headers=auth("triage"))
    assert resp.status_code == 401
    ok = app_without_triage.get("/crm/v1/customers/C0000001/consent", headers=auth("executor"))
    assert ok.status_code == 200


@pytest.fixture
def app_without_triage(settings: Settings, app: object) -> TestClient:
    keys = {k: v for k, v in KEYS.items() if k != "triage"}
    reduced = Settings(
        db_path=settings.db_path,
        host=settings.host,
        port=settings.port,
        line_test_delay_ms=(0, 0),
        api_keys=keys,
    )
    return TestClient(create_app(reduced))


def test_settings_from_env_reads_keys_and_ignores_empty(tmp_path: Path) -> None:
    env = {
        "MOCK_OSS_DB": str(tmp_path / "x.sqlite"),
        "MOCK_OSS_PORT": "9000",
        "MOCK_OSS_LINE_TEST_DELAY_MS": "0",
        "OSS_KEY_TRIAGE": "abc",
        "OSS_KEY_ADMIN": "",
    }
    s = Settings.from_env(env)
    assert s.port == 9000
    assert s.host == "127.0.0.1"
    assert s.db_path == tmp_path / "x.sqlite"
    assert s.line_test_delay_ms == (0, 0)
    assert s.api_keys == {"triage": "abc"}


def test_settings_defaults() -> None:
    s = Settings.from_env({})
    assert s.db_path == Path("var/mock_oss.sqlite")
    assert s.port == 8081
    assert s.line_test_delay_ms == (300, 1500)
    assert s.api_keys == {}


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", (0, 0)), ("300-1500", (300, 1500)), ("250", (250, 250))]
)
def test_parse_delay(raw: str, expected: tuple[int, int]) -> None:
    assert parse_delay(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "5-1", "-3", "1-2-3"])
def test_parse_delay_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="MOCK_OSS_LINE_TEST_DELAY_MS"):
        parse_delay(raw)


def test_invalid_port_rejected() -> None:
    with pytest.raises(ValueError, match="MOCK_OSS_PORT"):
        Settings.from_env({"MOCK_OSS_PORT": "http"})


def test_request_id_echoed_and_generated(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "req-123"})
    assert resp.headers["X-Request-ID"] == "req-123"
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) >= 16


def test_health_no_auth(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body == {"status": "ok", "seeded": True, "customers": 12, "last_event_seq": 0}


def test_health_unseeded(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        assert c.get("/health").json()["seeded"] is False


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    resp = client.get("/nope", headers=auth("admin"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_validation_error_envelope(client: TestClient) -> None:
    resp = client.post("/network/v1/line-tests", json={}, headers=auth("diagnostics"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
