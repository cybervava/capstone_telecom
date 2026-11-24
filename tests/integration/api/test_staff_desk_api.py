"""What the staff desk needs from the API: the queue with its context, closing an item, and a
staff sign-in for local demos that is not open to whoever can reach the port."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.messages import MessageRepository
from tests.integration.api.test_api import SECRET, token
from tests.support.credits import NOW, a_contact

AGENT = token(Role.AGENT, subject="agent:anna")
STAFF_KEY = "local-demo-staff-key-0123456789"


@pytest.fixture
async def client(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test", now=lambda: NOW))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


@pytest.fixture
async def dev(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    """The API as `make dev` runs it: the token endpoint exists and a staff key is set."""
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="dev", dev_staff_key=STAFF_KEY))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


async def queued(db: Database, contacts: ContactRepository, reason: str = "LINE_FAULT") -> Any:
    contact_id = await a_contact(contacts)
    await HandoffRepository(db).open(
        contact_id, reason=reason, summary=f"Reason: {reason}. Line test: FAIL.", priority=3
    )
    return contact_id


async def queue(client: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get("/v1/handoffs", headers=headers)
    assert response.status_code == 200
    return list(response.json()["data"])


async def told_by_staff(db: Database, contact_id: Any) -> list[Any]:
    """What the customer was told by a person's action, apart from the rest of the thread."""
    thread = await MessageRepository(db).list_for_contact(contact_id)
    return [message for message in thread if message.author == "staff"]


# --- the queue -----------------------------------------------------------------------------------


async def test_queue_names_the_complaint_and_where_the_contact_stands(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    contact = await contacts.get(contact_id)
    assert contact is not None

    (item,) = await queue(client, AGENT)

    assert item["contact_id"] == str(contact_id)
    assert item["complaint_ref"] == contact.complaint_ref
    assert item["contact_status"] == contact.status
    assert (item["reason"], item["priority"], item["status"]) == ("LINE_FAULT", 3, "OPEN")


async def test_item_is_closed_once_with_who_closed_it_and_leaves_the_queue(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    (item,) = await queue(client, AGENT)
    url = f"/v1/handoffs/{item['handoff_id']}/done"

    done = await client.post(
        url, json={"note": "Called the customer; visit booked."}, headers=AGENT
    )
    again = await client.post(url, json={}, headers=AGENT)

    assert (done.status_code, again.status_code) == (200, 409)
    assert done.json()["data"]["status"] == "DONE"
    assert await queue(client, AGENT) == []
    trail = await AuditRepository(db).for_contact(contact_id)
    record = next(entry for entry in trail if entry.step == "handoff.done")
    assert (record.actor, record.outcome) == ("staff:agent:anna", "LINE_FAULT")
    assert record.outputs["note"] == "Called the customer; visit booked."


async def test_customer_is_told_once_that_the_case_was_dealt_with_in_the_closers_words(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    contact = await contacts.get(contact_id)
    assert contact is not None
    (item,) = await queue(client, AGENT)
    url = f"/v1/handoffs/{item['handoff_id']}/done"
    note = "We replaced the faulty line card; your connection tested fine at 14:05."

    await client.post(url, json={"note": note}, headers=AGENT)
    await client.post(url, json={"note": "A second try."}, headers=AGENT)

    (told,) = await told_by_staff(db, contact_id)
    assert (told.direction, told.author) == ("outbound", "staff")
    assert told.meta["template_id"] == "dealt_with_by_staff.v1"
    assert note in told.body
    assert contact.complaint_ref in told.body
    # Who closed it is on the staff's side of the record, not the customer's.
    assert "anna" not in told.body


async def test_customer_is_told_the_case_was_dealt_with_when_no_note_is_given(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    (item,) = await queue(client, AGENT)

    await client.post(f"/v1/handoffs/{item['handoff_id']}/done", json={}, headers=AGENT)

    (told,) = await told_by_staff(db, contact_id)
    assert told.meta["template_id"] == "dealt_with_by_staff.v1"
    assert "note" not in told.body.lower()


async def test_refused_closure_tells_the_customer_nothing(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    (item,) = await queue(client, AGENT)

    await client.post(f"/v1/handoffs/{item['handoff_id']}/done", json={}, headers=token(Role.OPS))

    assert await told_by_staff(db, contact_id) == []


async def test_closing_a_reason_lets_the_same_reason_be_queued_again_later(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    (item,) = await queue(client, AGENT)
    await client.post(f"/v1/handoffs/{item['handoff_id']}/done", json={}, headers=AGENT)

    reopened = await HandoffRepository(db).open(
        contact_id, reason="LINE_FAULT", summary="Reason: LINE_FAULT. Again.", priority=3
    )

    assert reopened
    assert len(await queue(client, AGENT)) == 1


@pytest.mark.parametrize(
    "role", [Role.CUSTOMER, Role.GUEST, Role.CHANNEL, Role.OPS], ids=lambda role: role.value
)
async def test_only_agents_and_supervisors_close_items(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository, role: Role
) -> None:
    await queued(db, contacts)
    (item,) = await queue(client, AGENT)
    claims = {"customer_id": "C0000001"} if role is Role.CUSTOMER else {}
    url = f"/v1/handoffs/{item['handoff_id']}/done"

    refused = await client.post(url, json={}, headers=token(role, **claims))

    assert refused.status_code == 403
    assert (await client.post(url, json={})).status_code == 401
    assert len(await queue(client, AGENT)) == 1


async def test_unknown_item_and_malformed_note_are_refused(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    await queued(db, contacts)
    (item,) = await queue(client, AGENT)
    missing = "/v1/handoffs/00000000-0000-0000-0000-000000000000/done"

    assert (await client.post(missing, json={}, headers=AGENT)).status_code == 404
    too_long = await client.post(
        f"/v1/handoffs/{item['handoff_id']}/done", json={"note": "x" * 501}, headers=AGENT
    )
    extra = await client.post(
        f"/v1/handoffs/{item['handoff_id']}/done", json={"status": "OPEN"}, headers=AGENT
    )
    assert (too_long.status_code, extra.status_code) == (422, 422)


async def test_note_is_kept_with_the_item_and_masked_in_the_audit_trail(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await queued(db, contacts)
    (item,) = await queue(client, AGENT)
    note = "Called 9876543210 and mailed a.kumar@example.com; visit agreed."

    await client.post(f"/v1/handoffs/{item['handoff_id']}/done", json={"note": note}, headers=AGENT)

    trail = await AuditRepository(db).for_contact(contact_id)
    recorded = next(entry for entry in trail if entry.step == "handoff.done").outputs["note"]
    assert recorded == "Called <PHONE> and mailed <EMAIL>; visit agreed."
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT closed_note, closed_by, closed_at FROM handoffs WHERE handoff_id = %s",
            (item["handoff_id"],),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert (row["closed_note"], row["closed_by"], row["closed_at"]) == (note, "agent:anna", NOW)


# --- the console's pages -------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["/", "/staff.html"])
async def test_no_form_of_the_console_can_fall_back_to_a_get(
    client: httpx.AsyncClient, page: str
) -> None:
    """If the page's script fails to load, a form that submits by GET would put what was typed
    into it (the staff key, a customer's message) into the address bar and the access log."""
    response = await client.get(page)

    assert response.status_code == 200
    forms = re.findall(r"<form\b[^>]*>", response.text)
    assert forms
    assert all('method="post"' in form for form in forms), forms


@pytest.mark.parametrize("asset", ["/", "/staff.html", "/staff.js", "/shared.js", "/app.js"])
async def test_console_assets_are_checked_with_the_server_before_every_use(
    client: httpx.AsyncClient, asset: str
) -> None:
    """A new page must never run with a script the browser kept from before."""
    response = await client.get(asset)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert (await client.get("/health")).headers["Cache-Control"] == "no-cache"
    api = await client.get("/v1/handoffs", headers=AGENT)
    assert api.headers["Cache-Control"] == "no-store"


# --- staff sign-in for local demos ---------------------------------------------------------------


@pytest.mark.parametrize("role", ["agent", "supervisor", "ops", "channel"])
async def test_staff_token_needs_the_staff_key(dev: httpx.AsyncClient, role: str) -> None:
    without = await dev.post("/v1/dev/token", json={"role": role})
    wrong = await dev.post("/v1/dev/token", json={"role": role, "staff_key": "guess" * 6})
    right = await dev.post("/v1/dev/token", json={"role": role, "staff_key": STAFF_KEY})

    assert (without.status_code, wrong.status_code, right.status_code) == (403, 403, 200)
    assert right.json()["data"]["role"] == role
    assert STAFF_KEY not in without.text + wrong.text + right.text


async def test_staff_token_carries_the_name_given_so_decisions_are_attributable(
    dev: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    await queued(db, contacts)
    signed_in = await dev.post(
        "/v1/dev/token", json={"role": "agent", "staff_key": STAFF_KEY, "name": "anna.k"}
    )
    headers = {"Authorization": f"Bearer {signed_in.json()['data']['token']}"}
    (item,) = await queue(dev, headers)

    await dev.post(f"/v1/handoffs/{item['handoff_id']}/done", json={}, headers=headers)

    trail = await AuditRepository(db).for_contact(item["contact_id"])
    assert [r.actor for r in trail if r.step == "handoff.done"] == ["staff:agent:anna.k"]


@pytest.mark.parametrize("name", ["", "A", "anna k", "<script>", "x" * 41, "../ops"])
async def test_staff_name_must_be_a_plain_short_handle(dev: httpx.AsyncClient, name: str) -> None:
    response = await dev.post(
        "/v1/dev/token", json={"role": "agent", "staff_key": STAFF_KEY, "name": name}
    )

    assert response.status_code == 422


async def test_customer_and_guest_tokens_need_no_key_and_gain_nothing_from_one(
    dev: httpx.AsyncClient,
) -> None:
    guest = await dev.post("/v1/dev/token", json={"role": "guest"})
    customer = await dev.post(
        "/v1/dev/token",
        json={"role": "customer", "customer_id": "C0000009", "staff_key": STAFF_KEY},
    )

    assert (guest.status_code, customer.status_code) == (200, 200)
    headers = {"Authorization": f"Bearer {customer.json()['data']['token']}"}
    assert (await dev.get("/v1/handoffs", headers=headers)).status_code == 403


async def test_without_a_staff_key_configured_no_staff_token_is_minted(db: Database) -> None:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="dev"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        staff = await http.post("/v1/dev/token", json={"role": "supervisor", "staff_key": ""})
        guest = await http.post("/v1/dev/token", json={"role": "guest"})

    assert (staff.status_code, guest.status_code) == (403, 200)
