"""What the customer's page is told about the network: nothing that has stopped being true."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from tests.integration.api.test_api import SECRET, token
from tests.support.credits import NOW, a_contact

AGENT = token(Role.AGENT, subject="agent:anna")
MAX_AGE = 60


@pytest.fixture
async def client(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    deps = ApiDeps(
        database=db, jwt_secret=SECRET, env="test", now=lambda: NOW, status_max_age_seconds=MAX_AGE
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(deps)), base_url="http://care.test"
    ) as http:
        yield http


async def shown(
    client: httpx.AsyncClient, contacts: ContactRepository, **found: Any
) -> dict[str, Any]:
    contact_id = await a_contact(contacts)
    await contacts.update(contact_id, **found)
    response = await client.get(f"/v1/contacts/{contact_id}", headers=AGENT)
    assert response.status_code == 200
    return dict(response.json()["data"])


async def test_none_reported_is_shown_only_while_the_check_is_recent(
    client: httpx.AsyncClient, contacts: ContactRepository
) -> None:
    """Found by review: the page went on saying "none reported" for as long as the contact
    lived, through a later outage and after the same words had been stopped in a message."""
    just_now = await shown(
        client,
        contacts,
        network_state="CONFIRMED_CLEAR",
        network_checked_at=NOW - timedelta(seconds=MAX_AGE),
    )
    a_while_ago = await shown(
        client,
        contacts,
        network_state="CONFIRMED_CLEAR",
        network_checked_at=NOW - timedelta(seconds=MAX_AGE + 1),
    )
    undated = await shown(client, contacts, network_state="CONFIRMED_CLEAR")

    assert just_now["network_state"] == "CONFIRMED_CLEAR"
    assert a_while_ago["network_state"] == "UNKNOWN"
    assert undated["network_state"] == "UNKNOWN"
    assert a_while_ago["network_checked_at"] is not None  # when it was last looked at is kept


@pytest.mark.parametrize("state", ["ACTIVE", "UNKNOWN"])
async def test_an_outage_or_an_unverified_status_is_shown_as_last_found_with_its_time(
    client: httpx.AsyncClient, contacts: ContactRepository, state: str
) -> None:
    """Neither tells the customer that all is well, so neither is taken back by the clock."""
    long_ago = NOW - timedelta(hours=3)

    view = await shown(
        client, contacts, network_state=state, network_checked_at=long_ago, outage_id="OUT-1"
    )

    assert view["network_state"] == state
    assert view["network_checked_at"].startswith(long_ago.isoformat()[:19])


async def test_contact_nobody_has_looked_at_the_network_for_shows_nothing(
    client: httpx.AsyncClient, contacts: ContactRepository
) -> None:
    view = await shown(client, contacts)

    assert (view["network_state"], view["network_checked_at"]) == (None, None)
