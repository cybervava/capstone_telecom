"""The approvals endpoints: who may see and decide what, and what a decision is bound to."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.db.actions import ActionRepository, ActionStatus
from skylink_care.db.approvals import ApprovalRepository, ApprovalStatus
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from tests.integration.api.test_api import SECRET, token
from tests.support.credits import NOW, a_contact, proposed

AGENT = token(Role.AGENT, subject="agent-anna")
SUPERVISOR = token(Role.SUPERVISOR, subject="sup-ravi")


@pytest.fixture
async def client(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    # The clock is pinned to the moment the fixtures' approvals were asked for.
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test", now=lambda: NOW))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


async def pending(client: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get("/v1/approvals", headers=headers)
    assert response.status_code == 200
    return list(response.json()["data"])


async def decide(
    client: httpx.AsyncClient, headers: dict[str, str], approval: dict[str, Any], **body: Any
) -> httpx.Response:
    payload = {"decision": "approve", "request_hash": approval["request_hash"], **body}
    return await client.post(
        f"/v1/approvals/{approval['approval_id']}/decision", json=payload, headers=headers
    )


async def test_approver_is_shown_the_frozen_request_and_its_hash(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts))

    (item,) = await pending(client, AGENT)

    assert item["required_role"] == "agent"
    assert item["request_hash"] == action.request_hash
    assert item["request"]["amount_usd"] == "120.50"
    assert item["request"]["outage_id"] == "OUT-1"
    assert item["status"] == "PENDING"


async def test_each_role_sees_only_what_it_may_decide(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    await proposed(db, await a_contact(contacts), level="supervisor")

    assert await pending(client, AGENT) == []
    assert len(await pending(client, SUPERVISOR)) == 1


@pytest.mark.parametrize(
    "role", [Role.CUSTOMER, Role.GUEST, Role.CHANNEL, Role.OPS], ids=lambda role: role.value
)
async def test_nobody_but_agents_and_supervisors_reaches_the_approvals(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository, role: Role
) -> None:
    await proposed(db, await a_contact(contacts))
    claims = {"customer_id": "C0000001"} if role is Role.CUSTOMER else {}
    headers = token(role, **claims)

    listed = await client.get("/v1/approvals", headers=headers)
    decided = await client.post(
        "/v1/approvals/00000000-0000-0000-0000-000000000000/decision",
        json={"decision": "approve", "request_hash": "0" * 64},
        headers=headers,
    )

    assert (listed.status_code, decided.status_code) == (403, 403)
    assert (await client.get("/v1/approvals")).status_code == 401


async def test_approval_makes_the_credit_runnable_and_is_recorded_with_who_gave_it(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id)
    (item,) = await pending(client, AGENT)

    response = await decide(client, AGENT, item, note="matches the outage record")

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["status"], data["decided_by"]) == ("APPROVED", "agent-anna")
    stored = await ActionRepository(db).get(action.action_id)
    assert stored is not None
    assert stored.status is ActionStatus.READY
    trail = await AuditRepository(db).for_contact(contact_id)
    record = next(entry for entry in trail if entry.step == "approval.decided")
    assert (record.actor, record.outcome) == ("staff:agent-anna", "APPROVED")
    assert record.inputs["request_hash"] == action.request_hash


async def test_agent_is_refused_a_decision_that_needs_a_supervisor(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    action = await proposed(db, await a_contact(contacts), level="supervisor")
    (item,) = await pending(client, SUPERVISOR)

    refused = await decide(client, AGENT, item)
    allowed = await decide(
        client, SUPERVISOR, item, decision="reject", note="the outage was planned maintenance"
    )

    assert refused.status_code == 403
    assert refused.json()["error"]["message"] == "this decision needs a supervisor"
    assert allowed.status_code == 200
    stored = await ActionRepository(db).get(action.action_id)
    assert stored is not None
    assert stored.status is ActionStatus.REJECTED


async def test_decision_must_quote_the_hash_that_was_shown_and_can_be_taken_only_once(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    await proposed(db, await a_contact(contacts))
    (item,) = await pending(client, AGENT)

    stale = await decide(client, AGENT, item, request_hash="a" * 64)
    first = await decide(client, AGENT, item)
    again = await decide(client, AGENT, item, decision="reject", note="changed my mind")
    unknown = await client.post(
        "/v1/approvals/00000000-0000-0000-0000-000000000000/decision",
        json={"decision": "approve", "request_hash": "0" * 64},
        headers=AGENT,
    )

    assert [r.status_code for r in (stale, first, again, unknown)] == [409, 200, 409, 404]
    assert "reload and decide again" in stale.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "maybe", "request_hash": "0" * 64},
        {"decision": "approve", "request_hash": "not-a-hash"},
        {"decision": "approve"},
        {"decision": "approve", "request_hash": "0" * 64, "amount_usd": "9999.00"},
        {"decision": "approve", "request_hash": "0" * 64, "note": "x" * 501},
    ],
    ids=["unknown decision", "malformed hash", "no hash", "extra field", "note too long"],
)
async def test_malformed_decision_is_rejected_at_the_boundary(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await client.post(
        "/v1/approvals/00000000-0000-0000-0000-000000000000/decision", json=body, headers=AGENT
    )

    assert response.status_code == 422


async def test_approval_past_its_time_is_neither_offered_nor_decidable(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    """Whether or not the sweep that ends expired approvals has run yet."""
    action = await proposed(db, await a_contact(contacts), ttl=timedelta(seconds=-1))
    approval = await ApprovalRepository(db).for_action(action.action_id)
    assert approval is not None
    stale_screen = {"approval_id": str(approval.approval_id), "request_hash": action.request_hash}

    assert await pending(client, AGENT) == []
    assert (await decide(client, AGENT, stale_screen)).status_code == 409


async def test_decision_that_cannot_be_recorded_in_the_trail_is_not_taken(
    db: Database, contacts: ContactRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision, its effect on the credit and its audit entry are one transaction."""
    contact_id = await a_contact(contacts)
    action = await proposed(db, contact_id)

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(AuditRepository, "append", broken)
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test", now=lambda: NOW))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://care.test") as http:
        (item,) = await pending(http, AGENT)
        response = await decide(http, AGENT, item)

    assert response.status_code == 500
    approval = await ApprovalRepository(db).for_action(action.action_id)
    assert approval is not None
    assert (approval.status, approval.decided_by) == (ApprovalStatus.PENDING, None)
    stored = await ActionRepository(db).get(action.action_id)
    assert stored is not None
    assert stored.status is ActionStatus.PENDING_APPROVAL


@pytest.mark.parametrize(
    "note", [None, "", "   ", "no", ".....", "x x", "\u200b\u200b\u200b\u200b\u200b\u200b", "12345"]
)
async def test_a_rejection_without_its_reason_is_not_taken(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository, note: str | None
) -> None:
    """The policy says the credit is owed and a person says no: they must say why."""
    action = await proposed(db, await a_contact(contacts))
    (item,) = await pending(client, AGENT)
    body = {} if note is None else {"note": note}

    refused = await decide(client, AGENT, item, decision="reject", **body)
    approved_without_a_note = await decide(client, AGENT, item)

    assert refused.status_code == 422
    assert "a rejection needs its reason" in refused.text
    assert approved_without_a_note.status_code == 200  # an approval follows the policy
    stored = await ActionRepository(db).get(action.action_id)
    assert stored is not None
    assert stored.status is not ActionStatus.REJECTED


async def test_the_reason_for_a_rejection_is_in_the_audit_trail_with_who_gave_it(
    client: httpx.AsyncClient, db: Database, contacts: ContactRepository
) -> None:
    contact_id = await a_contact(contacts)
    await proposed(db, contact_id)
    (item,) = await pending(client, SUPERVISOR)

    rejected = await decide(
        client,
        SUPERVISOR,
        item,
        decision="reject",
        note="Customer was credited by hand yesterday, see call from 98450 12345.",
    )

    assert rejected.status_code == 200
    trail = await AuditRepository(db).for_contact(contact_id)
    (entry,) = [record for record in trail if record.step == "approval.decided"]
    assert (entry.actor, entry.outcome) == ("staff:sup-ravi", "REJECTED")
    assert "credited by hand yesterday" in entry.outputs["note"]
    assert "98450" not in entry.outputs["note"]  # a person's note, masked like any other
