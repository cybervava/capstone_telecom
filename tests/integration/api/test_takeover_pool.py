"""A takeover holds a conversation's lock and then writes. With both taken from one small
pool, a few takeovers at once would each hold a connection and wait for another."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.api.main import TAKEOVER_LOCKS
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.domain.models import Channel
from tests.integration.api.test_api import SECRET, token

# One connection for all requests: a takeover that needed two at once would stall here.
REQUEST_CONNECTIONS = 1
AT_ONCE = 6


@pytest.fixture
async def small_api(db: Database, database_conninfo: str) -> AsyncIterator[httpx.AsyncClient]:
    """The API on a pool smaller than the number of takeovers that arrive together."""
    database = await Database.open(
        database_conninfo, max_size=REQUEST_CONNECTIONS, lock_connections=TAKEOVER_LOCKS
    )
    app = create_app(ApiDeps(database=database, jwt_secret=SECRET, env="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http
    await database.close()


async def a_contact(db: Database) -> UUID:
    intake = await ContactRepository(db).accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="internet down",
            correlation_id=uuid4().hex,
            received_at=datetime.now(UTC),
        )
    )
    return intake.contact_id


async def test_more_takeovers_at_once_than_connections_all_go_through(
    db: Database, small_api: httpx.AsyncClient
) -> None:
    contacts = [await a_contact(db) for _ in range(AT_ONCE)]
    agent = token(Role.AGENT, subject="agent-anna")

    answers = await asyncio.wait_for(
        asyncio.gather(
            *(
                small_api.post(
                    f"/v1/contacts/{contact_id}/takeover",
                    json={"reason": "taking these over for the evening shift"},
                    headers=agent,
                )
                for contact_id in contacts
            )
        ),
        timeout=20,
    )

    assert [answer.status_code for answer in answers] == [200] * AT_ONCE
