"""The reference-first intake transaction."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from skylink_care.db.actions import ActionRepository, ActionStatus
from skylink_care.db.contacts import (
    COMPLAINT_REGISTER,
    ContactRepository,
    NewContact,
    RequestIdReusedError,
)
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.messages import MessageRepository
from skylink_care.domain.models import Channel
from skylink_care.domain.reference import is_valid_reference

RECEIVED = datetime(2026, 11, 15, 4, 30, tzinfo=UTC)


def new_contact(request_id: str | None = None, text: str = "My internet is down") -> NewContact:
    return NewContact(
        channel=Channel.CHAT,
        client_request_id=request_id or uuid4().hex,
        text=text,
        correlation_id=uuid4().hex,
        received_at=RECEIVED,
        session_id="sess-1",
    )


async def test_accept_issues_a_valid_reference_and_stores_the_contact(
    contacts: ContactRepository,
) -> None:
    result = await contacts.accept(new_contact())

    assert is_valid_reference(result.complaint_ref)
    assert not result.replayed
    stored = await contacts.get(result.contact_id)
    assert stored is not None
    assert stored.complaint_ref == result.complaint_ref
    assert stored.status == "RECEIVED"
    assert stored.reference_latency_ms >= 0
    assert stored.reference_issued_at.tzinfo is not None


async def test_accept_commits_message_wakeup_and_registration_together(
    contacts: ContactRepository,
    messages: MessageRepository,
    inbox: InboxRepository,
    actions: ActionRepository,
) -> None:
    result = await contacts.accept(new_contact(text="No signal since morning"))

    stored_messages = await messages.list_for_contact(result.contact_id)
    assert [(m.direction, m.author, m.body) for m in stored_messages] == [
        ("inbound", "customer", "No signal since morning")
    ]
    events = await inbox.fetch_after(result.contact_id, 0)
    assert [event.event_type for event in events] == ["contact.received"]
    registration = await actions.get_by_key(f"complaint:{result.complaint_ref}")
    assert registration is not None
    assert registration.action_type == COMPLAINT_REGISTER
    assert registration.status is ActionStatus.READY
    assert registration.lane == 1
    assert registration.request_body["complaint_ref"] == result.complaint_ref
    assert registration.request_body["customer_id"] is None


async def test_retry_with_the_same_request_id_returns_the_same_reference(
    contacts: ContactRepository, inbox: InboxRepository, actions: ActionRepository
) -> None:
    request = new_contact()

    first = await contacts.accept(request)
    second = await contacts.accept(request)

    assert second.replayed
    assert second.complaint_ref == first.complaint_ref
    assert second.contact_id == first.contact_id
    assert len(await inbox.fetch_after(first.contact_id, 0)) == 1
    assert len(await actions.list_for_contact(first.contact_id)) == 1


async def test_a_different_contact_gets_a_different_reference(contacts: ContactRepository) -> None:
    first = await contacts.accept(new_contact())
    second = await contacts.accept(new_contact())

    assert first.complaint_ref != second.complaint_ref


async def test_reusing_a_request_id_for_a_different_request_is_rejected(
    contacts: ContactRepository,
) -> None:
    request_id = uuid4().hex
    first = await contacts.accept(new_contact(request_id, text="My internet is down"))

    with pytest.raises(RequestIdReusedError) as raised:
        await contacts.accept(new_contact(request_id, text="Something else entirely"))

    assert raised.value.complaint_ref == first.complaint_ref


async def test_twenty_simultaneous_identical_requests_create_one_contact(
    contacts: ContactRepository, db: object
) -> None:
    request = new_contact()

    results = await asyncio.gather(*(contacts.accept(request) for _ in range(20)))

    assert len({result.complaint_ref for result in results}) == 1
    assert len({result.contact_id for result in results}) == 1
    assert sum(1 for result in results if not result.replayed) == 1


async def test_fifty_simultaneous_distinct_requests_get_fifty_references(
    contacts: ContactRepository,
) -> None:
    results = await asyncio.gather(*(contacts.accept(new_contact()) for _ in range(50)))

    references = {result.complaint_ref for result in results}
    assert len(references) == 50
    assert all(is_valid_reference(reference) for reference in references)


async def test_contact_can_be_found_by_reference(contacts: ContactRepository) -> None:
    result = await contacts.accept(new_contact())

    found = await contacts.get_by_reference(result.complaint_ref)

    assert found is not None
    assert found.contact_id == result.contact_id


async def test_update_sets_workflow_columns_and_rejects_others(
    contacts: ContactRepository,
) -> None:
    result = await contacts.accept(new_contact())

    await contacts.update(result.contact_id, status="IN_PROGRESS", customer_id="C0000001")
    stored = await contacts.get(result.contact_id)

    assert stored is not None
    assert (stored.status, stored.customer_id) == ("IN_PROGRESS", "C0000001")
    with pytest.raises(ValueError, match="not updatable"):
        await contacts.update(result.contact_id, complaint_ref="SKL-000000-000000-0")


async def test_repeat_contacts_by_a_customer_are_counted(contacts: ContactRepository) -> None:
    for _ in range(3):
        result = await contacts.accept(new_contact())
        await contacts.update(result.contact_id, customer_id="C0000042")

    count = await contacts.count_recent_for_customer(
        "C0000042", since=datetime(2026, 11, 8, tzinfo=UTC)
    )

    assert count == 3
