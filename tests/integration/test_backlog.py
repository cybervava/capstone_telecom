"""What is waiting for the orchestrator: the measurement that surge mode rests on."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from skylink_care.db import inbox as inbox_module
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.inbox import Backlog, InboxRepository
from tests.integration.test_inbox_and_lock import make_contact


async def queued_ago(db: Database, contact_id: UUID, seconds: float) -> None:
    """Make this contact's events as old as if they had been queued that long ago."""
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE inbox SET created_at = clock_timestamp() - make_interval(secs => %s)"
            " WHERE contact_id = %s",
            (seconds, contact_id),
        )


async def test_with_nothing_waiting_the_backlog_is_empty(inbox: InboxRepository) -> None:
    assert await inbox.backlog() == Backlog(contacts=0, oldest_seconds=0.0)


async def test_a_waiting_contact_is_counted_once_and_the_longest_wait_is_given(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    older = await make_contact(contacts)
    await make_contact(contacts)
    # A second event for the same contact: still one contact waiting.
    await inbox.add(older, "customer.message", dedupe_key="msg:1")
    await queued_ago(db, older, 40)

    backlog = await inbox.backlog()

    assert backlog.contacts == 2
    assert 40 <= backlog.oldest_seconds < 45


async def test_events_that_were_applied_or_given_up_on_do_not_wait(
    contacts: ContactRepository, inbox: InboxRepository
) -> None:
    served = await make_contact(contacts)
    events = await inbox.fetch_after(served, 0)
    await inbox.mark_applied(served, events[-1].seq)
    abandoned = await make_contact(contacts)
    outcome = await inbox.record_failure(abandoned, "boom", max_attempts=1, backoff_seconds=0.0)
    assert outcome.dead_lettered

    assert await inbox.backlog() == Backlog(contacts=0, oldest_seconds=0.0)


async def test_a_contact_held_for_a_retry_waits_only_from_the_end_of_its_hold(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    """A run that failed is tried again later. Until then nothing can be done for the contact,
    and that is not the orchestrator being behind."""
    contact_id = await make_contact(contacts)
    await queued_ago(db, contact_id, 120)
    await inbox.record_failure(contact_id, "boom", max_attempts=5, backoff_seconds=30.0)

    held = await inbox.backlog()

    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE inbox SET retry_after = clock_timestamp() - interval '3 seconds'"
            " WHERE contact_id = %s",
            (contact_id,),
        )
    due = await inbox.backlog()

    assert held == Backlog(contacts=0, oldest_seconds=0.0)
    assert due.contacts == 1
    # Since the hold ended, not since the event was first queued two minutes ago.
    assert 3 <= due.oldest_seconds < 8


async def test_the_count_gives_up_rather_than_hold_a_connection(
    db: Database,
    contacts: ContactRepository,
    inbox: InboxRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The queue is longest to count just when the database has least to spare. A count that
    cannot finish is abandoned, and the connection it used is fit for the next one."""
    await make_contact(contacts)
    monkeypatch.setattr(inbox_module, "BACKLOG_TIMEOUT_MS", 150)

    async with db.transaction() as blocker:
        await blocker.execute("LOCK TABLE inbox IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(psycopg.errors.QueryCanceled):
            await inbox.backlog()

    assert (await inbox.backlog()).contacts == 1
