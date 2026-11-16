"""The operations queue over the API: staff see it, operations staff and supervisors close it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from skylink_care.api import ops as ops_api
from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.db.database import Database
from skylink_care.db.ops import OpsRepository
from tests.integration.api.test_api import SECRET, token
from tests.support.credits import NOW

OPS = token(Role.OPS, subject="ops:asha")
SUPERVISOR = token(Role.SUPERVISOR, subject="supervisor:meera")
AGENT = token(Role.AGENT, subject="agent:anna")


@pytest.fixture
async def client(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test", now=lambda: NOW))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


async def trouble(db: Database, subject: str = "notify:OUT-1:outage_started.v1") -> None:
    await OpsRepository(db).raise_item(
        kind="ACTION_FAILED",
        subject=subject,
        summary="Enterprise write NOTIFY_SEND is FAILED after 1 attempt(s): INVALID: bad template.",
        details={"action_type": "NOTIFY_SEND", "last_action_key": f"{subject}:C1"},
        priority=2,
    )


async def queue(client: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get("/v1/ops", headers=headers)
    assert response.status_code == 200
    return list(response.json()["data"]["items"])


@pytest.mark.parametrize("headers", [OPS, SUPERVISOR, AGENT], ids=["ops", "supervisor", "agent"])
async def test_staff_see_what_is_open_with_how_often_it_was_seen(
    client: httpx.AsyncClient, db: Database, headers: dict[str, str]
) -> None:
    await trouble(db)
    await trouble(db)

    (item,) = await queue(client, headers)

    assert (item["kind"], item["subject"]) == ("ACTION_FAILED", "notify:OUT-1:outage_started.v1")
    assert (item["occurrences"], item["priority"], item["status"]) == (2, 2, "OPEN")
    assert "NOTIFY_SEND" in item["summary"]
    assert item["details"]["action_type"] == "NOTIFY_SEND"


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.GUEST, Role.CHANNEL])
async def test_nobody_but_staff_sees_the_queue(
    client: httpx.AsyncClient, db: Database, role: Role
) -> None:
    await trouble(db)
    claims = {"customer_id": "C0000001"} if role is Role.CUSTOMER else {}

    assert (await client.get("/v1/ops", headers=token(role, **claims))).status_code == 403
    assert (await client.get("/v1/ops")).status_code == 401


@pytest.mark.parametrize("headers", [OPS, SUPERVISOR], ids=["ops", "supervisor"])
async def test_item_is_closed_once_with_who_closed_it_and_leaves_the_queue(
    client: httpx.AsyncClient, db: Database, headers: dict[str, str]
) -> None:
    await trouble(db)
    (item,) = await queue(client, OPS)
    url = f"/v1/ops/{item['item_id']}/done"

    closed = await client.post(url, json={"note": "template fixed and re-sent"}, headers=headers)
    again = await client.post(url, json={}, headers=headers)

    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "DONE"
    assert again.status_code == 409
    assert await queue(client, OPS) == []


async def test_an_agent_sees_the_queue_and_cannot_close_it(
    client: httpx.AsyncClient, db: Database
) -> None:
    """What is wrong with the platform is for those who run it, or for a supervisor."""
    await trouble(db)
    (item,) = await queue(client, AGENT)
    url = f"/v1/ops/{item['item_id']}/done"

    assert (await client.post(url, json={}, headers=AGENT)).status_code == 403
    assert (await client.post(url, json={})).status_code == 401
    assert len(await queue(client, OPS)) == 1


async def test_unknown_item_and_malformed_requests_are_refused(
    client: httpx.AsyncClient, db: Database
) -> None:
    await trouble(db)
    (item,) = await queue(client, OPS)
    url = f"/v1/ops/{item['item_id']}/done"
    missing = "/v1/ops/00000000-0000-0000-0000-000000000000/done"

    assert (await client.post(missing, json={}, headers=OPS)).status_code == 404
    assert (await client.post("/v1/ops/not-an-id/done", json={}, headers=OPS)).status_code == 422
    too_long = await client.post(url, json={"note": "x" * 501}, headers=OPS)
    extra = await client.post(url, json={"status": "OPEN"}, headers=OPS)
    assert (too_long.status_code, extra.status_code) == (422, 422)
    assert len(await queue(client, OPS)) == 1


async def test_closing_is_recorded_with_who_did_it_and_the_note_masked(
    client: httpx.AsyncClient, db: Database
) -> None:
    await trouble(db)
    (item,) = await queue(client, OPS)
    note = "Called the vendor on 9876543210; template fixed."

    await client.post(f"/v1/ops/{item['item_id']}/done", json={"note": note}, headers=OPS)

    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT actor, outcome, inputs, outputs FROM audit_log"
            " WHERE step = 'ops.done' AND correlation_id = %s",
            (item["item_id"],),
        )
        (entry,) = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT closed_note, closed_by, closed_at FROM ops_items WHERE item_id = %s",
            (item["item_id"],),
        )
        row = await cursor.fetchone()
    assert entry["actor"] == "staff:ops:asha"
    assert entry["outcome"] == "ACTION_FAILED"
    assert entry["outputs"]["note"] == "Called the vendor on <PHONE>; template fixed."
    assert row is not None
    assert (row["closed_note"], row["closed_by"], row["closed_at"]) == (note, "ops:asha", NOW)


async def test_the_total_is_given_with_the_list_so_a_long_queue_is_not_taken_for_a_short_one(
    client: httpx.AsyncClient, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by review: the list is cut, and the page showed the length of the cut."""
    monkeypatch.setattr(ops_api, "LIST_LIMIT", 2)
    for number in range(5):
        await trouble(db, subject=f"notify:OUT-{number}:outage_started.v1")

    response = await client.get("/v1/ops", headers=OPS)

    data = response.json()["data"]
    assert (len(data["items"]), data["total"]) == (2, 5)
