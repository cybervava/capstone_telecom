"""The care API over real HTTP semantics (ASGI transport) and real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role, WeakSecretError, issue_token
from skylink_care.api.ratelimit import RateLimiter
from skylink_care.db.actions import ActionRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.inbox import InboxRepository
from skylink_care.domain.reference import is_valid_reference

SECRET = "test-signing-secret-of-sufficient-length-0123456789"


def token(role: Role, **claims: Any) -> dict[str, str]:
    value = issue_token(SECRET, subject=claims.pop("subject", "tester"), role=role, **claims)
    return {"Authorization": f"Bearer {value}"}


def contact_body(**overrides: Any) -> dict[str, Any]:
    return {
        "channel": "chat",
        "client_request_id": uuid4().hex,
        "text": "My internet is down",
        **overrides,
    }


@pytest.fixture
async def client(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


CUSTOMER = token(Role.CUSTOMER, customer_id="C0000001")


# --- intake --------------------------------------------------------------------------------------


async def test_intake_returns_a_reference_immediately_in_the_envelope(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert is_valid_reference(body["data"]["complaint_ref"])
    assert body["data"]["status"] == "RECEIVED"
    assert body["data"]["replayed"] is False
    assert response.headers["X-Request-ID"]


async def test_intake_needs_no_enterprise_system_or_model(
    client: httpx.AsyncClient, db: Database
) -> None:
    """No worker is running and no gateway exists in this test: the reference is still issued."""
    response = await client.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)

    data = response.json()["data"]
    contact = await ContactRepository(db).get(data["contact_id"])
    assert contact is not None
    assert contact.authenticated_customer_id == "C0000001"
    assert contact.reference_latency_ms < 2000
    assert len(await InboxRepository(db).pending_contacts(10)) == 1
    registration = await ActionRepository(db).get_by_key(f"complaint:{data['complaint_ref']}")
    assert registration is not None


async def test_retry_returns_the_same_reference_with_200(client: httpx.AsyncClient) -> None:
    body = contact_body()

    first = await client.post("/v1/contacts", json=body, headers=CUSTOMER)
    again = await client.post("/v1/contacts", json=body, headers=CUSTOMER)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["data"]["complaint_ref"] == first.json()["data"]["complaint_ref"]
    assert again.json()["data"]["replayed"] is True


async def test_reused_request_id_with_a_different_request_is_a_conflict(
    client: httpx.AsyncClient,
) -> None:
    request_id = uuid4().hex
    first = await client.post(
        "/v1/contacts", json=contact_body(client_request_id=request_id), headers=CUSTOMER
    )

    clash = await client.post(
        "/v1/contacts",
        json=contact_body(client_request_id=request_id, text="something different"),
        headers=CUSTOMER,
    )

    assert clash.status_code == 409
    error = clash.json()["error"]
    assert error["code"] == "REQUEST_ID_REUSED"
    assert error["complaint_ref"] == first.json()["data"]["complaint_ref"]


async def test_request_ids_of_different_callers_do_not_collide(client: httpx.AsyncClient) -> None:
    body = contact_body(client_request_id="same-id-0001")

    one = await client.post("/v1/contacts", json=body, headers=CUSTOMER)
    two = await client.post(
        "/v1/contacts", json=body, headers=token(Role.CUSTOMER, customer_id="C0000002")
    )

    assert (one.status_code, two.status_code) == (201, 201)
    assert one.json()["data"]["complaint_ref"] != two.json()["data"]["complaint_ref"]


@pytest.mark.parametrize(
    "bad",
    [
        {"text": ""},
        {"text": "x" * 20_001},
        {"channel": "fax"},
        {"client_request_id": "short"},
        {"client_request_id": "has spaces in it"},
        {"unexpected": "field"},
    ],
    ids=["empty", "too-long", "channel", "short-id", "bad-id", "extra-field"],
)
async def test_invalid_input_is_rejected_with_field_level_detail(
    client: httpx.AsyncClient, bad: dict[str, Any]
) -> None:
    response = await client.post("/v1/contacts", json=contact_body(**bad), headers=CUSTOMER)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["fields"]


# --- authentication and authorisation ------------------------------------------------------------


async def test_every_endpoint_needs_a_token(client: httpx.AsyncClient) -> None:
    for method, path in [
        ("POST", "/v1/contacts"),
        ("GET", f"/v1/contacts/{uuid4()}"),
        ("POST", f"/v1/contacts/{uuid4()}/messages"),
        ("GET", f"/v1/contacts/{uuid4()}/audit"),
        ("GET", "/v1/handoffs"),
    ]:
        response = await client.request(method, path, json={})
        assert response.status_code == 401, path
        assert response.json()["success"] is False


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer not-a-token"},
        {
            "Authorization": "Bearer "
            + issue_token("another-secret-entirely-0123456789abcdef", subject="x", role=Role.OPS)
        },
        {
            "Authorization": "Bearer "
            + issue_token(
                SECRET,
                subject="x",
                role=Role.OPS,
                now=datetime.now(UTC) - timedelta(hours=3),
            )
        },
    ],
    ids=["garbage", "wrong-signature", "expired"],
)
async def test_bad_tokens_are_refused(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    assert (await client.get("/v1/handoffs", headers=headers)).status_code == 401


async def test_customers_cannot_reach_staff_endpoints(client: httpx.AsyncClient) -> None:
    created = await client.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)
    contact_id = created.json()["data"]["contact_id"]

    assert (await client.get("/v1/handoffs", headers=CUSTOMER)).status_code == 403
    assert (
        await client.get(f"/v1/contacts/{contact_id}/audit", headers=CUSTOMER)
    ).status_code == 403
    assert (await client.get("/v1/handoffs", headers=token(Role.AGENT))).status_code == 200


async def test_staff_cannot_open_contacts_as_customers(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/contacts", json=contact_body(), headers=token(Role.SUPERVISOR)
    )

    assert response.status_code == 403


async def test_a_customer_cannot_read_another_customers_contact(client: httpx.AsyncClient) -> None:
    created = await client.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)
    contact_id = created.json()["data"]["contact_id"]
    other = token(Role.CUSTOMER, customer_id="C0000002")

    mine = await client.get(f"/v1/contacts/{contact_id}", headers=CUSTOMER)
    theirs = await client.get(f"/v1/contacts/{contact_id}", headers=other)
    post = await client.post(
        f"/v1/contacts/{contact_id}/messages",
        json={"client_message_id": uuid4().hex, "text": "hello"},
        headers=other,
    )

    assert mine.status_code == 200
    assert mine.json()["data"]["messages"][0]["body"] == "My internet is down"
    assert theirs.status_code == 404  # indistinguishable from "does not exist"
    assert post.status_code == 404


async def test_a_guest_sees_only_contacts_of_their_own_session(client: httpx.AsyncClient) -> None:
    guest = token(Role.GUEST, session_id="session-a")
    created = await client.post("/v1/contacts", json=contact_body(), headers=guest)
    contact_id = created.json()["data"]["contact_id"]

    same = await client.get(f"/v1/contacts/{contact_id}", headers=guest)
    other = await client.get(
        f"/v1/contacts/{contact_id}", headers=token(Role.GUEST, session_id="session-b")
    )

    assert (same.status_code, other.status_code) == (200, 404)


async def test_only_a_channel_may_state_the_caller_line(client: httpx.AsyncClient) -> None:
    body = contact_body(channel="call_transcript", caller_phone="+919845012345")

    as_customer = await client.post("/v1/contacts", json=body, headers=CUSTOMER)
    as_channel = await client.post("/v1/contacts", json=body, headers=token(Role.CHANNEL))

    assert as_customer.status_code == 403
    assert as_channel.status_code == 201


# --- follow-up messages --------------------------------------------------------------------------


async def test_follow_up_message_is_stored_once_and_wakes_the_workflow(
    client: httpx.AsyncClient, db: Database
) -> None:
    created = await client.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)
    contact_id = created.json()["data"]["contact_id"]
    message = {"client_message_id": uuid4().hex, "text": "it is still down"}

    first = await client.post(f"/v1/contacts/{contact_id}/messages", json=message, headers=CUSTOMER)
    again = await client.post(f"/v1/contacts/{contact_id}/messages", json=message, headers=CUSTOMER)

    assert (first.status_code, again.status_code) == (202, 202)
    assert again.json()["data"]["replayed"] is True
    events = await InboxRepository(db).fetch_after(created.json()["data"]["contact_id"], 0)
    assert [event.event_type for event in events] == ["contact.received", "customer.message"]


async def test_closed_contact_takes_no_more_messages(
    client: httpx.AsyncClient, db: Database
) -> None:
    created = await client.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)
    contact_id = created.json()["data"]["contact_id"]
    await ContactRepository(db).update(contact_id, status="CLOSED")

    response = await client.post(
        f"/v1/contacts/{contact_id}/messages",
        json={"client_message_id": uuid4().hex, "text": "hello again"},
        headers=CUSTOMER,
    )

    assert response.status_code == 409


# --- rate limits ---------------------------------------------------------------------------------


async def test_first_contact_is_never_limited_but_a_flood_from_one_caller_is(db: Database) -> None:
    app = create_app(
        ApiDeps(
            database=db,
            jwt_secret=SECRET,
            env="test",
            contact_limit=RateLimiter(capacity=2, refill_per_second=0.0),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        flood = [
            (await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)).status_code
            for _ in range(4)
        ]
        newcomers = [
            (
                await http.post(
                    "/v1/contacts",
                    json=contact_body(),
                    headers=token(Role.CUSTOMER, customer_id=f"C00009{n:02d}"),
                )
            ).status_code
            for n in range(20)
        ]

    assert flood == [201, 201, 429, 429]
    assert newcomers == [201] * 20


async def test_messages_per_contact_are_limited(db: Database) -> None:
    app = create_app(
        ApiDeps(
            database=db,
            jwt_secret=SECRET,
            env="test",
            message_limit=RateLimiter(capacity=2, refill_per_second=0.0),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        created = await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)
        contact_id = created.json()["data"]["contact_id"]
        codes = [
            (
                await http.post(
                    f"/v1/contacts/{contact_id}/messages",
                    json={"client_message_id": uuid4().hex, "text": "again"},
                    headers=CUSTOMER,
                )
            ).status_code
            for _ in range(3)
        ]

    assert codes == [202, 202, 429]


# --- hardening -----------------------------------------------------------------------------------


async def test_health_needs_no_token_and_responses_carry_security_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "ok"}, "error": None}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


async def test_dev_token_endpoint_does_not_exist_outside_dev(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/dev/token", json={"role": "ops"})

    assert response.status_code in {404, 405}


async def test_dev_token_endpoint_exists_in_dev(db: Database) -> None:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="dev"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        minted = await http.post(
            "/v1/dev/token", json={"role": "customer", "customer_id": "C0000009"}
        )
        token_value = minted.json()["data"]["token"]
        used = await http.post(
            "/v1/contacts",
            json=contact_body(),
            headers={"Authorization": f"Bearer {token_value}"},
        )

    assert minted.status_code == 200
    assert used.status_code == 201


def test_a_short_signing_secret_is_refused_at_start_up(db: Database) -> None:
    with pytest.raises(WeakSecretError):
        create_app(ApiDeps(database=db, jwt_secret="too-short", env="test"))


# --- findings from the security review, pinned ----------------------------------------------------


async def test_a_channel_sees_the_contacts_it_opened_and_no_others(
    client: httpx.AsyncClient,
) -> None:
    centre_a = token(Role.CHANNEL, subject="contact-centre-a")
    centre_b = token(Role.CHANNEL, subject="contact-centre-b")
    created = await client.post(
        "/v1/contacts", json=contact_body(channel="call_transcript"), headers=centre_a
    )
    contact_id = created.json()["data"]["contact_id"]

    own = await client.get(f"/v1/contacts/{contact_id}", headers=centre_a)
    other = await client.get(f"/v1/contacts/{contact_id}", headers=centre_b)
    write = await client.post(
        f"/v1/contacts/{contact_id}/messages",
        json={"client_message_id": uuid4().hex, "text": "injected"},
        headers=centre_b,
    )

    assert (own.status_code, other.status_code, write.status_code) == (200, 404, 404)


async def test_a_customer_cannot_place_a_contact_into_someone_elses_session(
    client: httpx.AsyncClient, db: Database
) -> None:
    created = await client.post(
        "/v1/contacts", json=contact_body(session_id="victim-session"), headers=CUSTOMER
    )

    contact = await ContactRepository(db).get(created.json()["data"]["contact_id"])
    assert contact is not None
    assert contact.session_id is None
    victim = await client.get(
        f"/v1/contacts/{contact.contact_id}", headers=token(Role.GUEST, session_id="victim-session")
    )
    assert victim.status_code == 404


async def test_api_responses_are_not_cacheable_and_request_ids_are_sanitised(
    client: httpx.AsyncClient,
) -> None:
    hostile = "x" * 300
    response = await client.post(
        "/v1/contacts", json=contact_body(), headers={**CUSTOMER, "X-Request-ID": hostile}
    )
    kept = await client.get("/health", headers={"X-Request-ID": "trace-123.abc"})

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] != hostile
    assert len(response.headers["X-Request-ID"]) == 32
    assert kept.headers["X-Request-ID"] == "trace-123.abc"
    assert "object-src 'none'" in response.headers["Content-Security-Policy"]


async def test_audit_of_a_contact_that_does_not_exist_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"/v1/contacts/{uuid4()}/audit", headers=token(Role.OPS))

    assert response.status_code == 404


async def test_the_api_schema_is_not_published(client: httpx.AsyncClient) -> None:
    assert (await client.get("/openapi.json")).status_code == 404
    assert (await client.get("/docs")).status_code == 404
