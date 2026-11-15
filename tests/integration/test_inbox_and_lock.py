"""Inbox scheduling and the per-contact lock."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from uuid import UUID, uuid4

from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.messages import MessageRepository
from skylink_care.domain.models import Channel


async def make_contact(contacts: ContactRepository) -> UUID:
    result = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="hello",
            correlation_id=uuid4().hex,
            received_at=datetime(2026, 11, 15, 4, 30, tzinfo=UTC),
        )
    )
    return result.contact_id


async def test_the_same_event_is_queued_only_once(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await make_contact(contacts)

    first = await inbox.add(contact_id, "customer.message", dedupe_key="msg:1", payload={"n": 1})
    again = await inbox.add(contact_id, "customer.message", dedupe_key="msg:1", payload={"n": 1})

    assert (first, again) == (True, False)
    assert len(await inbox.fetch_after(contact_id, 0)) == 2  # contact.received + the message


async def test_events_come_back_in_order_after_the_cursor(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await make_contact(contacts)
    await inbox.add(contact_id, "customer.message", dedupe_key="msg:1")
    await inbox.add(contact_id, "approval.decided", dedupe_key="approval:1")

    everything = await inbox.fetch_after(contact_id, 0)
    after_first = await inbox.fetch_after(contact_id, everything[0].seq)

    assert [event.event_type for event in everything] == [
        "contact.received",
        "customer.message",
        "approval.decided",
    ]
    assert [event.event_type for event in after_first] == ["customer.message", "approval.decided"]
    assert [event.seq for event in everything] == sorted(event.seq for event in everything)


async def test_longest_waiting_contact_is_scheduled_first(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    older = await make_contact(contacts)
    newer = await make_contact(contacts)

    assert await inbox.pending_contacts(10) == [older, newer]
    assert await inbox.pending_contacts(1) == [older]


async def test_applied_events_no_longer_schedule_the_contact(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await make_contact(contacts)
    last = (await inbox.fetch_after(contact_id, 0))[-1].seq

    await inbox.mark_applied(contact_id, last)

    assert await inbox.pending_contacts(10) == []
    await inbox.add(contact_id, "customer.message", dedupe_key="msg:2")
    assert await inbox.pending_contacts(10) == [contact_id]


async def test_repeated_failures_dead_letter_the_contact(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await make_contact(contacts)

    outcomes = [
        await inbox.record_failure(contact_id, f"boom {n}", max_attempts=3, backoff_seconds=0.0)
        for n in range(3)
    ]

    assert [outcome.dead_lettered for outcome in outcomes] == [False, False, True]
    assert [outcome.attempts for outcome in outcomes] == [1, 2, 3]
    assert await inbox.pending_contacts(10) == []
    assert await inbox.fetch_after(contact_id, 0) == []
    assert await inbox.count_dead() == 1


async def test_a_failed_contact_is_not_retried_before_its_back_off_has_passed(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    failing = await make_contact(contacts)
    healthy = await make_contact(contacts)

    await inbox.record_failure(failing, "llm timeout", max_attempts=5, backoff_seconds=0.3)
    # A new event for the failing contact does not jump the queue past its back-off.
    await inbox.add(failing, "customer.message", dedupe_key="msg:again")
    during = await inbox.pending_contacts(10)
    await asyncio.sleep(0.4)
    after = await inbox.pending_contacts(10)

    assert during == [healthy]
    assert after == [failing, healthy]


async def test_back_off_doubles_with_each_failure(
    contacts: ContactRepository, inbox: InboxRepository, db: Database
) -> None:
    contact_id = await make_contact(contacts)

    delays = []
    for _ in range(3):
        await inbox.record_failure(contact_id, "boom", max_attempts=9, backoff_seconds=10.0)
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT extract(epoch FROM max(retry_after) - clock_timestamp()) AS wait"
                " FROM inbox WHERE contact_id = %s",
                (contact_id,),
            )
            row = await cursor.fetchone()
        assert row is not None
        delays.append(round(float(row["wait"]) / 10.0))

    assert delays == [1, 2, 4]


async def test_events_for_one_contact_are_numbered_in_commit_order(
    contacts: ContactRepository, inbox: InboxRepository, db: Database
) -> None:
    """A writer that started first but commits late must not end up behind the cursor."""
    contact_id = await make_contact(contacts)
    first_inside = asyncio.Event()
    release_first = asyncio.Event()

    async def slow_writer() -> None:
        async with db.transaction() as conn:
            await inbox.add(contact_id, "customer.message", dedupe_key="slow", conn=conn)
            first_inside.set()
            await release_first.wait()

    slow = asyncio.create_task(slow_writer())
    await first_inside.wait()
    fast = asyncio.create_task(inbox.add(contact_id, "customer.message", dedupe_key="fast"))
    await asyncio.sleep(0.15)
    # The second writer is waiting for the first: it has not been numbered or committed yet.
    visible_while_blocked = [e.seq for e in await inbox.fetch_after(contact_id, 0)]
    release_first.set()
    await asyncio.gather(slow, fast)

    events = await inbox.fetch_after(contact_id, 0)
    assert len(visible_while_blocked) == 1  # only contact.received
    assert len(events) == 3
    assert [e.seq for e in events] == sorted(e.seq for e in events)
    assert not fast.cancelled()


async def test_only_one_holder_gets_the_contact_lock(
    db: Database, contacts: ContactRepository
) -> None:
    contact_id = await make_contact(contacts)

    async with db.contact_lock(contact_id) as first:
        async with db.contact_lock(contact_id) as second:
            assert (first, second) == (True, False)
        async with db.contact_lock(uuid4()) as other_contact:
            assert other_contact
    async with db.contact_lock(contact_id) as after_release:
        assert after_release


async def test_lock_is_released_when_the_block_raises(
    db: Database, contacts: ContactRepository
) -> None:
    contact_id = await make_contact(contacts)

    try:
        async with db.contact_lock(contact_id):
            raise RuntimeError("worker failed mid-run")
    except RuntimeError:
        pass

    async with db.contact_lock(contact_id) as reacquired:
        assert reacquired


async def test_outbound_message_is_stored_once_per_dedupe_key(
    contacts: ContactRepository, messages: MessageRepository
) -> None:
    contact_id = await make_contact(contacts)
    kwargs = {"direction": "outbound", "author": "agent:triage", "dedupe_key": "turn-1:reply"}

    first = await messages.add(contact_id, body="We are checking.", **kwargs)
    rerun = await messages.add(contact_id, body="A different wording.", **kwargs)

    stored = await messages.list_for_contact(contact_id)
    assert (first, rerun) == (True, False)
    assert [m.body for m in stored if m.direction == "outbound"] == ["We are checking."]


async def test_contact_is_handed_off_once_per_reason(
    contacts: ContactRepository, handoffs: HandoffRepository
) -> None:
    contact_id = await make_contact(contacts)

    first = await handoffs.open(contact_id, reason="IDENTITY_UNVERIFIED", summary="s", priority=3)
    again = await handoffs.open(contact_id, reason="IDENTITY_UNVERIFIED", summary="s")
    other = await handoffs.open(contact_id, reason="MAPPING_UNRESOLVED", summary="s")

    queue = await handoffs.list_open()
    assert (first, again, other) == (True, False, True)
    assert [item.reason for item in queue] == ["IDENTITY_UNVERIFIED", "MAPPING_UNRESOLVED"]


async def test_a_completed_handoff_does_not_block_a_new_one_for_the_same_reason(
    contacts: ContactRepository, handoffs: HandoffRepository, db: Database
) -> None:
    contact_id = await make_contact(contacts)
    await handoffs.open(contact_id, reason="NETWORK_STATUS_UNVERIFIED", summary="first")
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE handoffs SET status = 'DONE' WHERE contact_id = %s", (contact_id,)
        )

    reopened = await handoffs.open(contact_id, reason="NETWORK_STATUS_UNVERIFIED", summary="again")

    assert reopened
    assert [item.summary for item in await handoffs.list_open()] == ["again"]


async def test_contact_locks_do_not_use_up_the_query_pool(database_conninfo: str) -> None:
    """Many contacts locked at once must still leave connections for their own queries."""
    database = await Database.open(database_conninfo, max_size=2, lock_connections=6)
    try:
        async with AsyncExitStack() as stack:
            held = [
                await stack.enter_async_context(database.contact_lock(uuid4())) for _ in range(6)
            ]
            async with database.connection() as conn:
                cursor = await conn.execute("SELECT 1 AS ok")
                row = await cursor.fetchone()
        assert all(held)
        assert row == {"ok": 1}
    finally:
        await database.close()
