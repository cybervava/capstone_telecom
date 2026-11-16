"""The operations queue: what needs a person and belongs to no contact."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from skylink_care.db.database import Database
from skylink_care.db.ops import OpsClosure, OpsRepository

NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)


async def raised(ops: OpsRepository, **overrides: object) -> bool:
    fields: dict[str, object] = {
        "kind": "ACTION_FAILED",
        "subject": "notify:OUT-1:outage_started.v1",
        "summary": "Customers on outage OUT-1 could not be told that it started.",
        "details": {"action_type": "NOTIFY_SEND", "error": "INVALID: bad template"},
    }
    return await ops.raise_item(**{**fields, **overrides})  # type: ignore[arg-type]


async def test_trouble_is_queued_once_and_counted_when_it_is_seen_again(db: Database) -> None:
    ops = OpsRepository(db)

    first = await raised(ops)
    again = await raised(ops, details={"action_type": "NOTIFY_SEND", "error": "TIMEOUT: slow"})

    (item,) = await ops.list_open()
    assert (first, again) == (True, False)
    assert item.occurrences == 2
    assert item.status == "OPEN"
    # How it began stays on the item; what was seen last is kept beside it.
    assert item.details["error"] == "INVALID: bad template"
    assert item.details["latest"]["error"] == "TIMEOUT: slow"
    assert item.last_seen_at >= item.created_at
    assert await ops.count_open() == 1


async def test_different_trouble_is_queued_separately_and_the_urgent_comes_first(
    db: Database,
) -> None:
    ops = OpsRepository(db)

    await raised(ops, subject="notify:OUT-1:outage_started.v1", priority=3)
    await raised(ops, kind="FEED_NEEDS_ATTENTION", subject="feed", priority=1)
    await raised(ops, subject="notify:OUT-2:outage_started.v1", priority=3)

    assert [item.subject for item in await ops.list_open()] == [
        "feed",
        "notify:OUT-1:outage_started.v1",
        "notify:OUT-2:outage_started.v1",
    ]


async def test_item_is_closed_once_with_who_closed_it_and_what_they_noted(db: Database) -> None:
    ops = OpsRepository(db)
    await raised(ops)
    (item,) = await ops.list_open()

    async with db.transaction() as conn:
        closed = await ops.close(
            conn, item.item_id, closed_by="ops:asha", note="template fixed", now=NOW
        )
    async with db.transaction() as conn:
        again = await ops.close(conn, item.item_id, closed_by="ops:ravi", note=None, now=NOW)
        unknown = await ops.close(conn, uuid4(), closed_by="ops:ravi", note=None, now=NOW)

    assert closed.outcome is OpsClosure.CLOSED
    assert closed.item is not None
    assert (closed.item.status, closed.item.closed_by) == ("DONE", "ops:asha")
    assert again.outcome is OpsClosure.ALREADY_DONE
    assert unknown.outcome is OpsClosure.NOT_FOUND
    assert await ops.list_open() == []


async def test_the_same_trouble_after_an_item_was_closed_is_queued_afresh(db: Database) -> None:
    ops = OpsRepository(db)
    await raised(ops)
    (item,) = await ops.list_open()
    async with db.transaction() as conn:
        await ops.close(conn, item.item_id, closed_by="ops:asha", note=None, now=NOW)

    assert await raised(ops) is True
    (new,) = await ops.list_open()
    assert new.item_id != item.item_id
    assert new.occurrences == 1


async def test_a_long_summary_is_cut_and_the_list_is_bounded(db: Database) -> None:
    ops = OpsRepository(db)

    await raised(ops, summary="x" * 5000)
    for number in range(5):
        await raised(ops, subject=f"notify:OUT-{number + 10}:outage_started.v1")

    (first, *_) = await ops.list_open()
    assert len(first.summary) <= 2000
    assert len(await ops.list_open(limit=3)) == 3


async def test_what_an_item_carries_is_cut_to_a_handful_of_short_facts(db: Database) -> None:
    ops = OpsRepository(db)
    much = {f"key{number}": "x" * 5000 for number in range(50)}

    await raised(ops, details={**much, "attempts": 3, "nested": {"a": [1, 2, 3]}})

    (item,) = await ops.list_open()
    assert len(item.details) <= 20
    assert all(len(str(value)) <= 500 for value in item.details.values())


async def test_open_items_are_counted_beyond_what_a_list_shows(db: Database) -> None:
    ops = OpsRepository(db)
    for number in range(5):
        await raised(ops, subject=f"write:KIND_{number}")

    assert len(await ops.list_open(limit=2)) == 2
    assert await ops.count_open() == 5
