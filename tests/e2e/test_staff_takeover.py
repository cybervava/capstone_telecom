"""A person takes a conversation over from the assistant: the human override, on record."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.db.database import Database
from tests.e2e.test_edges_and_degraded_mode import customer_with
from tests.integration.api.test_api import SECRET, token
from tests.support.platform import Platform
from tests.support.stack import Simulator

AGENT = token(Role.AGENT, subject="agent-anna")
WHY = "Customer phoned the shop in tears, 98450 12345; I will call her back myself."
# The outage each conversation of a test is about.
OUTAGES: dict[Any, str] = {}


@pytest.fixture
async def api(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


async def in_the_assistants_hands(platform: Platform, simulator: Simulator) -> Any:
    """A customer in an outage has been told of it; the assistant is watching for its end."""
    customer = await customer_with(simulator)
    outage = await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    intake = await platform.contact(
        "my internet is down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()
    OUTAGES[intake.contact_id] = outage["outage_id"]
    return intake


async def take_over(api: httpx.AsyncClient, contact_id: Any, **body: Any) -> httpx.Response:
    return await api.post(
        f"/v1/contacts/{contact_id}/takeover", json={"reason": WHY, **body}, headers=AGENT
    )


async def test_a_person_takes_the_conversation_and_the_assistant_lets_go(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient
) -> None:
    intake = await in_the_assistants_hands(platform, simulator)
    before = await platform.replies(intake.contact_id)

    taken = await take_over(api, intake.contact_id)
    await platform.say(intake.contact_id, "any news? it is still down")
    await platform.settle()

    assert taken.status_code == 200
    assert taken.json()["data"]["taken_by"] == "agent-anna"
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "HANDOFF"
    after = await platform.replies(intake.contact_id)
    assert len(after) == len(before) + 1  # the customer is told once, and the assistant is silent
    assert "A member of our team has taken over this conversation" in after[-1]
    assert intake.complaint_ref in after[-1]
    reasons = {item.reason: item for item in await platform.runtime.handoffs.list_open()}
    assert set(reasons) == {"STAFF_TOOK_OVER", "CUSTOMER_FOLLOW_UP"}
    assert "Taken over by agent-anna (agent)" in reasons["STAFF_TOOK_OVER"].summary
    # The assistant's wake-ups for the conversation are cancelled, and the state it was
    # parked with is gone once the next event has been seen.
    async with platform.runtime.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS waiting FROM timers"
            " WHERE contact_id = %s AND NOT cancelled AND fired_at IS NULL",
            (intake.contact_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["waiting"] == 0
    parked = await platform.checkpointer.aget_tuple(
        {"configurable": {"thread_id": str(intake.contact_id)}}
    )
    assert parked is None


async def test_the_override_is_on_the_audit_trail_with_who_and_why(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient
) -> None:
    intake = await in_the_assistants_hands(platform, simulator)

    await take_over(api, intake.contact_id)

    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    (entry,) = [record for record in trail if record.step == "staff.takeover"]
    assert (entry.actor, entry.inputs["role"]) == ("staff:agent-anna", "agent")
    assert entry.outcome == "AWAITING_NETWORK"  # what the assistant was doing with it
    assert "I will call her back myself" in entry.outputs["reason"]
    assert "98450" not in entry.outputs["reason"]  # a person's note, masked like any other
    (item,) = [
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "STAFF_TOOK_OVER"
    ]
    assert "98450" not in item.summary
    assert "Still under way and not stopped by this: " in item.summary


async def test_the_customer_is_not_told_who_took_the_conversation(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient
) -> None:
    """The name a member of staff signs in with is for the trail and the staff queue. What
    the customer's channel is served must not carry it."""
    customer = await customer_with(simulator)
    owner = token(Role.CUSTOMER, customer_id=customer["customer_id"])
    created = await api.post(
        "/v1/contacts",
        json={"channel": "chat", "client_request_id": uuid4().hex, "text": "my internet is down"},
        headers=owner,
    )
    contact_id = created.json()["data"]["contact_id"]
    await platform.settle()

    taken = await take_over(api, contact_id)
    seen = await api.get(f"/v1/contacts/{contact_id}", headers=owner)

    assert taken.status_code == 200
    assert seen.status_code == 200
    assert "agent-anna" not in seen.text
    assert "taken over this conversation" in seen.text
    assert "phoned the shop" not in seen.text  # nor why


async def test_what_was_under_way_still_reaches_the_customer(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient
) -> None:
    """Taking a conversation over does not un-register its complaint or lose its outage:
    when the outage ends, the conversation's owner hears of it through the staff queue."""
    intake = await in_the_assistants_hands(platform, simulator)
    await take_over(api, intake.contact_id)
    said = len(await platform.replies(intake.contact_id))

    await simulator.resolve_outage(OUTAGES[intake.contact_id])
    await platform.feed()
    await platform.settle()

    assert len(await platform.replies(intake.contact_id)) == said  # the assistant stays silent
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "HANDOFF"


@pytest.mark.parametrize(
    ("body", "status"),
    [({"reason": "no"}, 422), ({"reason": None}, 422), ({"reason": WHY, "extra": 1}, 422)],
)
async def test_a_takeover_without_a_reason_is_not_one(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient, body: Any, status: int
) -> None:
    intake = await in_the_assistants_hands(platform, simulator)

    refused = await api.post(f"/v1/contacts/{intake.contact_id}/takeover", json=body, headers=AGENT)

    assert refused.status_code == status
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status != "HANDOFF"


async def test_there_is_nothing_to_take_over_twice_or_where_there_is_no_conversation(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient
) -> None:
    intake = await in_the_assistants_hands(platform, simulator)

    first = await take_over(api, intake.contact_id)
    again = await take_over(api, intake.contact_id)
    nowhere = await take_over(api, uuid4())

    assert (first.status_code, again.status_code, nowhere.status_code) == (200, 409, 404)
    assert again.json()["error"]["message"] == "a person already has this conversation"
    taken = [
        item
        for item in await platform.runtime.handoffs.list_open()
        if item.reason == "STAFF_TOOK_OVER"
    ]
    assert len(taken) == 1


async def test_a_turn_in_progress_is_not_interrupted(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient
) -> None:
    """The assistant holds the conversation's lock while it answers. A takeover in that
    moment is refused, not applied underneath a turn that would then undo it."""
    intake = await in_the_assistants_hands(platform, simulator)

    async with platform.runtime.db.contact_lock(intake.contact_id) as held:
        assert held
        refused = await take_over(api, intake.contact_id)
    afterwards = await take_over(api, intake.contact_id)

    assert refused.status_code == 409
    assert "right now" in refused.json()["error"]["message"]
    assert afterwards.status_code == 200


@pytest.mark.parametrize("role", [Role.OPS, Role.CUSTOMER, Role.GUEST, Role.CHANNEL])
async def test_only_an_agent_or_a_supervisor_takes_a_conversation_over(
    platform: Platform, simulator: Simulator, api: httpx.AsyncClient, role: Role
) -> None:
    intake = await in_the_assistants_hands(platform, simulator)
    claims: dict[str, Any] = {}
    if role is Role.CUSTOMER:
        claims["customer_id"] = "C0000001"
    if role is Role.GUEST:
        claims["session_id"] = "s-1"

    refused = await api.post(
        f"/v1/contacts/{intake.contact_id}/takeover",
        json={"reason": WHY},
        headers=token(role, **claims),
    )

    assert refused.status_code == 403
