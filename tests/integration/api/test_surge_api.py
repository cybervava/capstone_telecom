"""Under load: the reference is issued as always, the customer is told that the reply will take
longer, staff can see how far behind the assistant is, and nobody's first contact is limited."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.api.ratelimit import RateLimiter
from skylink_care.db.database import Database
from skylink_care.db.inbox import Backlog, InboxRepository
from skylink_care.domain.reference import is_valid_reference
from skylink_care.surge import SurgeGauge, SurgePolicy
from tests.integration.api.test_api import CUSTOMER, SECRET, contact_body, token

BEHIND = Backlog(contacts=400, oldest_seconds=95.0)


async def gauge_reading(backlog: Backlog) -> SurgeGauge:
    async def read() -> Backlog:
        return backlog

    gauge = SurgeGauge(read, SurgePolicy())
    await gauge.measure()
    return gauge


def client_for(db: Database, **deps: Any) -> httpx.AsyncClient:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test", **deps))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://care.test")


async def test_under_load_intake_still_issues_the_reference_and_says_the_reply_will_take_longer(
    db: Database,
) -> None:
    async with client_for(db, surge=await gauge_reading(BEHIND)) as http:
        response = await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)

    data = response.json()["data"]
    assert response.status_code == 201
    assert is_valid_reference(data["complaint_ref"])
    assert data["reply_delayed"] is True


async def test_with_no_load_nothing_is_said_about_a_delay(db: Database) -> None:
    async with client_for(db) as http:
        response = await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)

    assert response.status_code == 201
    assert response.json()["data"]["reply_delayed"] is False


async def test_a_gauge_that_fails_never_costs_the_customer_the_reference(db: Database) -> None:
    class Broken(SurgeGauge):
        def behind(self) -> bool:
            raise RuntimeError("the gauge is broken")

    async with client_for(db, surge=Broken(InboxRepository(db).backlog, SurgePolicy())) as http:
        response = await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)

    data = response.json()["data"]
    assert response.status_code == 201
    assert is_valid_reference(data["complaint_ref"])
    assert data["reply_delayed"] is False


async def test_the_contact_and_a_follow_up_message_say_the_same(db: Database) -> None:
    async with client_for(db, surge=await gauge_reading(BEHIND)) as http:
        created = await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)
        contact_id = created.json()["data"]["contact_id"]
        page = await http.get(f"/v1/contacts/{contact_id}", headers=CUSTOMER)
        follow_up = await http.post(
            f"/v1/contacts/{contact_id}/messages",
            json={"client_message_id": uuid4().hex, "text": "any news?"},
            headers=CUSTOMER,
        )

    assert page.json()["data"]["reply_delayed"] is True
    assert follow_up.status_code == 202
    assert follow_up.json()["data"]["reply_delayed"] is True


# --- what staff see -------------------------------------------------------------------------------


async def test_staff_see_how_far_behind_the_assistant_is(db: Database) -> None:
    """Measured when they ask, not taken from the last measurement. No worker runs here, so
    every contact that is opened waits."""
    policy = SurgePolicy(waiting_contacts=3, refresh_seconds=0.0)
    async with client_for(db, surge=SurgeGauge(InboxRepository(db).backlog, policy)) as http:
        ops = token(Role.OPS)
        quiet = await http.get("/v1/ops/load", headers=ops)
        for n in range(3):
            await http.post(
                "/v1/contacts",
                json=contact_body(),
                headers=token(Role.CUSTOMER, customer_id=f"C00007{n:02d}"),
            )
        busy = await http.get("/v1/ops/load", headers=ops)

    assert quiet.status_code == 200
    assert quiet.json()["data"] == {"waiting_contacts": 0, "oldest_seconds": 0, "behind": False}
    load = busy.json()["data"]
    assert (load["waiting_contacts"], load["behind"]) == (3, True)
    assert 0 <= load["oldest_seconds"] < 30


async def test_a_backlog_that_cannot_be_counted_is_said_to_be_unknown_not_a_server_error(
    db: Database,
) -> None:
    """The desk shows "could not be read" for any failure. The API still owes an honest status:
    the service is there and one figure is not, for now."""

    async def unreadable() -> Backlog:
        raise ConnectionError("the database is restarting")

    gauge = SurgeGauge(unreadable, SurgePolicy())
    async with client_for(db, surge=gauge) as http:
        first = await http.get("/v1/ops/load", headers=token(Role.OPS))
        again = await http.get("/v1/ops/load", headers=token(Role.OPS))

    assert (first.status_code, again.status_code) == (503, 503)
    assert "restarting" not in first.text  # what failed is for the log, not for the client
    assert "backlog" in first.json()["error"]["message"]


async def test_however_many_desks_ask_the_backlog_is_counted_once_in_an_interval(
    db: Database,
) -> None:
    counted = 0

    async def read() -> Backlog:
        nonlocal counted
        counted += 1
        return BEHIND

    async with client_for(db, surge=SurgeGauge(read, SurgePolicy())) as http:
        answers = [
            (await http.get("/v1/ops/load", headers=token(role))).json()["data"]
            for role in (Role.OPS, Role.AGENT, Role.SUPERVISOR) * 4
        ]

    assert counted == 1
    assert all(answer == answers[0] for answer in answers)
    assert answers[0] == {"waiting_contacts": 400, "oldest_seconds": 95, "behind": True}


@pytest.mark.parametrize("role", [Role.AGENT, Role.SUPERVISOR, Role.OPS], ids=lambda r: r.value)
async def test_every_staff_role_may_read_the_load(db: Database, role: Role) -> None:
    async with client_for(db) as http:
        response = await http.get("/v1/ops/load", headers=token(role))

    assert response.status_code == 200


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.GUEST, Role.CHANNEL], ids=lambda r: r.value)
async def test_nobody_on_the_customer_side_may(db: Database, role: Role) -> None:
    claims = {"customer_id": "C0000001"} if role is Role.CUSTOMER else {}
    async with client_for(db) as http:
        response = await http.get("/v1/ops/load", headers=token(role, **claims))

    assert response.status_code == 403


# --- a first contact is never limited, whichever way it comes in ---------------------------------


def from_line(number: int, **overrides: Any) -> dict[str, Any]:
    return contact_body(
        channel="call_transcript", caller_phone=f"+9198000{number:05d}", **overrides
    )


async def test_a_channel_that_relays_many_callers_is_not_limited_as_one_caller(
    db: Database,
) -> None:
    """A contact centre opens the contacts of everyone who calls it. Each caller's first
    contact gets its reference; only one line that keeps calling is held back."""
    limit = RateLimiter(capacity=2, refill_per_second=0.0)
    centre = token(Role.CHANNEL, subject="contact-centre-a")
    async with client_for(db, contact_limit=limit) as http:
        callers = [
            (await http.post("/v1/contacts", json=from_line(n), headers=centre)).status_code
            for n in range(20)
        ]
        one_line = [
            (await http.post("/v1/contacts", json=from_line(99), headers=centre)).status_code
            for _ in range(4)
        ]

    assert callers == [201] * 20
    assert one_line == [201, 201, 429, 429]


async def test_a_channel_as_a_whole_is_bounded_whoever_it_names(db: Database) -> None:
    """A channel can name whom it likes, so naming a new caller every time would otherwise
    never meet a limit. The bound on the channel is far above a contact centre's traffic; it
    is what stops an integration that runs away."""
    whole_channel = RateLimiter(capacity=3, refill_per_second=0.0)
    runaway = token(Role.CHANNEL, subject="contact-centre-a")
    async with client_for(db, channel_limit=whole_channel) as http:
        flood = [
            (await http.post("/v1/contacts", json=from_line(n), headers=runaway)).status_code
            for n in range(5)
        ]
        another_channel = await http.post(
            "/v1/contacts",
            json=from_line(50),
            headers=token(Role.CHANNEL, subject="contact-centre-b"),
        )
        a_customer = await http.post("/v1/contacts", json=contact_body(), headers=CUSTOMER)

    assert flood == [201, 201, 201, 429, 429]
    assert (another_channel.status_code, a_customer.status_code) == (201, 201)


async def test_one_line_is_one_caller_however_its_number_is_written(db: Database) -> None:
    limit = RateLimiter(capacity=2, refill_per_second=0.0)
    centre = token(Role.CHANNEL, subject="contact-centre-a")
    written = ["+919800000007", "919800000007", "9800000007"]
    async with client_for(db, contact_limit=limit) as http:
        codes = [
            (
                await http.post(
                    "/v1/contacts",
                    json=contact_body(channel="call_transcript", caller_phone=number),
                    headers=centre,
                )
            ).status_code
            for number in written
        ]

    assert codes == [201, 201, 429]


async def test_a_channel_that_names_a_session_is_limited_by_session(db: Database) -> None:
    limit = RateLimiter(capacity=1, refill_per_second=0.0)
    bridge = token(Role.CHANNEL, subject="chat-bridge")
    async with client_for(db, contact_limit=limit) as http:
        sessions = [
            (
                await http.post(
                    "/v1/contacts", json=contact_body(session_id=f"wa-{n}"), headers=bridge
                )
            ).status_code
            for n in range(5)
        ]
        again = await http.post(
            "/v1/contacts", json=contact_body(session_id="wa-0"), headers=bridge
        )

    assert sessions == [201] * 5
    assert again.status_code == 429


async def test_a_channel_that_names_nobody_is_limited_as_itself(db: Database) -> None:
    limit = RateLimiter(capacity=2, refill_per_second=0.0)
    centre = token(Role.CHANNEL, subject="contact-centre-a")
    async with client_for(db, contact_limit=limit) as http:
        flood = [
            (await http.post("/v1/contacts", json=contact_body(), headers=centre)).status_code
            for _ in range(4)
        ]

    assert flood == [201, 201, 429, 429]


async def test_two_channels_do_not_share_a_callers_limit(db: Database) -> None:
    limit = RateLimiter(capacity=1, refill_per_second=0.0)
    async with client_for(db, contact_limit=limit) as http:
        first = await http.post(
            "/v1/contacts", json=from_line(7), headers=token(Role.CHANNEL, subject="centre-a")
        )
        other = await http.post(
            "/v1/contacts", json=from_line(7), headers=token(Role.CHANNEL, subject="centre-b")
        )

    assert (first.status_code, other.status_code) == (201, 201)


async def test_a_request_id_still_belongs_to_the_channel_whichever_caller_it_names(
    db: Database,
) -> None:
    """The limit is counted for each caller a channel names; a request is told apart for the
    channel as a whole. The same request id with another caller is a reuse, not a new contact."""
    centre = token(Role.CHANNEL, subject="contact-centre-a")
    request_id = uuid4().hex
    async with client_for(db) as http:
        first = await http.post(
            "/v1/contacts", json=from_line(1, client_request_id=request_id), headers=centre
        )
        reused = await http.post(
            "/v1/contacts", json=from_line(2, client_request_id=request_id), headers=centre
        )

    assert first.status_code == 201
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "REQUEST_ID_REUSED"
