"""Feed consumer and its reactions against real PostgreSQL, with a scripted feed.

What is being checked: an event and all of its consequences commit together with the cursor, a
page delivered twice has no second effect, and a feed that cannot be read is recorded as such.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from skylink_care.adapters.models import EventPage, NetworkEventDto
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.db.actions import INCIDENT_OPEN, ActionRepository, ActionStatus, NewAction
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.episodes import EpisodeRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.read_model import ReadModelRepository
from skylink_care.db.reference import ReferenceRepository
from skylink_care.domain.models import Channel
from skylink_care.ingest import consumer as consumer_module
from skylink_care.ingest.consumer import FeedConsumer
from skylink_care.ingest.reactions import OutageReactions
from tests.integration.test_audit_and_reference import seed_reference
from tests.integration.test_read_model_and_timers import alarm_event, outage_event

NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)
NODE = "BLR-OLT-0001"
STARTED_KEY = "notify:C1:OUT-1:outage_started.v1"


def page(*events: NetworkEventDto, last_seq: int | None = None) -> Result[EventPage]:
    highest = max((event.seq for event in events), default=0)
    return Ok(
        EventPage(events=events, last_seq=last_seq or highest, generated_at=NOW), status_code=200
    )


class ScriptedFeed:
    """Answers each poll with the next scripted page, then with empty pages."""

    def __init__(self, *pages: Result[EventPage]) -> None:
        self._pages = list(pages)
        self.asked_after: list[int] = []

    async def events(
        self, *, after_seq: int, limit: int = 200, wait_seconds: int = 0, correlation_id: str
    ) -> Result[EventPage]:
        self.asked_after.append(after_seq)
        return self._pages.pop(0) if self._pages else page(last_seq=after_seq)


def build(db: Database, feed: Any, **options: Any) -> FeedConsumer:
    read_model = ReadModelRepository(db)
    reactions = OutageReactions(
        ReferenceRepository(db),
        ActionRepository(db),
        EpisodeRepository(db),
        ContactRepository(db),
        read_model,
        announce_within=timedelta(minutes=15),
    )
    return FeedConsumer(feed, db, read_model, reactions, now=lambda: NOW, **options)


async def waiting_contact(contacts: ContactRepository, **columns: Any) -> UUID:
    intake = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="internet down",
            correlation_id=uuid4().hex,
            received_at=NOW,
        )
    )
    await contacts.update(
        intake.contact_id, **{"status": "AWAITING_NETWORK", "node_id": NODE, **columns}
    )
    return intake.contact_id


async def events_of(inbox: InboxRepository, contact_id: UUID) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.event_type, event.payload)
        for event in await inbox.fetch_unapplied(contact_id)
        if event.event_type.startswith("outage.")
    ]


async def test_declared_outage_updates_the_model_notifies_and_wakes_in_one_step(
    db: Database, contacts: ContactRepository, inbox: InboxRepository, actions: ActionRepository
) -> None:
    await seed_reference(db)
    waiting = await waiting_contact(contacts)
    already_told = await waiting_contact(contacts, outage_id="OUT-1")
    elsewhere = await waiting_contact(contacts, node_id="BLR-OLT-0002")
    consumer = build(db, ScriptedFeed(page(outage_event(1))))

    outcome = await consumer.poll_once()

    assert (outcome.applied, outcome.declared, outcome.error) == (1, 1, None)
    # C1 is the only active customer mapped to this node alone: C2 is mapped to two nodes and
    # C3 is suspended, and telling an unaffected customer is worse than telling one late.
    assert outcome.notifications == 1
    notice = await actions.get_by_key(STARTED_KEY)
    assert notice is not None
    assert (notice.action_type, notice.lane, notice.status) == (
        "NOTIFY_SEND",
        9,
        ActionStatus.READY,
    )
    assert notice.request_body["channel"] == "preferred"
    assert notice.contact_id is None
    assert await events_of(inbox, waiting) == [("outage.declared", {"outage_id": "OUT-1"})]
    assert await events_of(inbox, already_told) == []
    assert await events_of(inbox, elsewhere) == []
    state = await ReadModelRepository(db).feed_state()
    assert (state.applied_seq, state.heartbeat_at, state.caught_up) == (1, NOW, True)


async def test_page_delivered_twice_and_out_of_order_has_no_second_effect(
    db: Database, contacts: ContactRepository, inbox: InboxRepository, actions: ActionRepository
) -> None:
    await seed_reference(db)
    waiting = await waiting_contact(contacts)
    update = outage_event(2, "OUTAGE_UPDATED", version=2)
    feed = ScriptedFeed(page(update, outage_event(1)), page(outage_event(1), update))
    consumer = build(db, feed)

    first = await consumer.poll_once()
    second = await consumer.poll_once()

    assert (first.applied, first.declared, first.notifications) == (2, 1, 1)
    assert (second.applied, second.declared, second.notifications, second.contacts_woken) == (
        0,
        0,
        0,
        0,
    )
    assert feed.asked_after == [0, 2]
    assert len(await events_of(inbox, waiting)) == 1
    stored = await ReadModelRepository(db).get_outage("OUT-1")
    assert stored is not None
    assert stored.version == 2


async def test_failure_part_way_through_a_page_applies_none_of_it(
    db: Database, actions: ActionRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_reference(db)
    consumer = build(
        db,
        ScriptedFeed(
            page(outage_event(1), outage_event(2, outage_id="OUT-2", node_ids=("BLR-OLT-0002",)))
        ),
    )
    reactions = consumer._reactions
    real = reactions.on_change

    async def fail_on_the_second_outage(conn: Any, change: Any, **kwargs: Any) -> Any:
        if change.outage is not None and change.outage.outage_id == "OUT-2":
            raise ConnectionError("database connection lost")
        return await real(conn, change, **kwargs)

    monkeypatch.setattr(reactions, "on_change", fail_on_the_second_outage)

    with pytest.raises(ConnectionError):
        await consumer.poll_once()

    model = ReadModelRepository(db)
    assert (await model.feed_state()).applied_seq == 0
    assert (await model.stats())["outages"] == 0
    assert await actions.get_by_key(STARTED_KEY) is None


async def test_feed_that_cannot_be_read_is_recorded_and_the_heartbeat_keeps_ageing(
    db: Database,
) -> None:
    feed = ScriptedFeed(
        page(last_seq=0), Err(ErrorKind.TIMEOUT, "no answer in time", status_code=504)
    )
    consumer = build(db, feed)

    healthy = await consumer.poll_once()
    failed = await consumer.poll_once()

    assert (healthy.error, failed.error) == (None, "TIMEOUT")
    state = await ReadModelRepository(db).feed_state()
    assert state.heartbeat_at == NOW  # from the good poll; the failed one did not refresh it
    assert state.last_error == "TIMEOUT: no answer in time"


async def test_catching_up_reads_page_after_page_until_the_feed_is_drained(db: Database) -> None:
    await seed_reference(db)
    feed = ScriptedFeed(
        page(outage_event(1), outage_event(2, "OUTAGE_UPDATED", version=2), last_seq=3),
        page(outage_event(3, "OUTAGE_RESOLVED", version=3, resolved_at=NOW), last_seq=3),
    )
    consumer = build(db, feed, page_size=2)

    outcome = await consumer.catch_up()

    assert (outcome.resolved, outcome.more) == (1, False)
    assert feed.asked_after == [0, 2]
    assert (await ReadModelRepository(db).feed_state()).applied_seq == 3


async def test_hole_in_the_sequence_is_applied_past_but_the_model_is_no_longer_trusted(
    db: Database,
) -> None:
    """The lost event may have been a resolution, so what the model shows stops being evidence."""
    await seed_reference(db)
    consumer = build(db, ScriptedFeed(page(outage_event(1)), page(outage_event(5, version=2))))
    model = ReadModelRepository(db)

    await consumer.poll_once()
    trusted_before = (await model.view(NODE, "broadband")).caught_up
    outcome = await consumer.poll_once()

    assert outcome.applied == 1  # later events are not held up
    state = await model.feed_state()
    assert (state.applied_seq, state.caught_up, state.intact) == (5, True, False)
    assert state.gap_detail == "expected seq 2, received 5"
    assert trusted_before
    assert not (await model.view(NODE, "broadband")).caught_up


async def test_outage_event_that_cannot_be_applied_marks_a_gap_and_is_counted(
    db: Database,
) -> None:
    await seed_reference(db)
    unusable = outage_event(2, "OUTAGE_RESOLVED", version=None)
    nameless_alarm = alarm_event(3, "ALARM_RAISED", node_id=NODE).model_copy(
        update={"alarm_id": None}
    )
    consumer = build(db, ScriptedFeed(page(outage_event(1), unusable, nameless_alarm)))

    outcome = await consumer.poll_once()

    assert (outcome.applied, outcome.malformed) == (3, 2)
    state = await ReadModelRepository(db).feed_state()
    # Only the outage event can hide a resolution; a nameless alarm cannot.
    assert state.gap_detail == "malformed OUTAGE_RESOLVED at seq 2: OUTAGE_RESOLVED without version"


async def test_feed_whose_sequence_runs_backwards_is_refused_and_not_taken_as_a_heartbeat(
    db: Database,
) -> None:
    """A source restored from a backup would otherwise look healthy while saying nothing new."""
    await seed_reference(db)
    earlier = NOW - timedelta(minutes=10)
    first = Ok(
        EventPage(events=(outage_event(7),), last_seq=7, generated_at=earlier), status_code=200
    )
    consumer = build(db, ScriptedFeed(first, page(outage_event(2, outage_id="OUT-2"), last_seq=2)))

    await consumer.poll_once()
    outcome = await consumer.poll_once()

    assert outcome.error == "SEQUENCE_REGRESSION"
    model = ReadModelRepository(db)
    state = await model.feed_state()
    assert (state.applied_seq, state.heartbeat_at) == (7, earlier)
    assert state.last_error is not None
    assert state.last_error.startswith("SEQUENCE_REGRESSION")
    assert await model.get_outage("OUT-2") is None


async def test_page_that_contradicts_itself_is_refused(db: Database) -> None:
    consumer = build(db, ScriptedFeed(page(outage_event(9), last_seq=3)))

    outcome = await consumer.poll_once()

    assert outcome.error == "PROTOCOL"
    assert (await ReadModelRepository(db).feed_state()).applied_seq == 0


async def test_outage_named_on_the_aggregation_node_reaches_the_customers_below_it(
    db: Database, contacts: ContactRepository, inbox: InboxRepository, actions: ActionRepository
) -> None:
    await seed_reference(db)
    waiting = await waiting_contact(contacts)
    consumer = build(db, ScriptedFeed(page(outage_event(1, node_ids=("BLR-AGG-0001",)))))

    outcome = await consumer.poll_once()

    assert (outcome.notifications, outcome.contacts_woken) == (1, 1)
    assert await actions.get_by_key(STARTED_KEY) is not None
    assert await events_of(inbox, waiting) == [("outage.declared", {"outage_id": "OUT-1"})]


async def test_update_that_widens_an_outage_tells_only_the_newly_affected(
    db: Database, contacts: ContactRepository, inbox: InboxRepository, actions: ActionRepository
) -> None:
    await seed_reference(db)
    on_new_node = await waiting_contact(contacts)
    wider = outage_event(2, "OUTAGE_UPDATED", version=2, node_ids=("BLR-OLT-0002", NODE))
    eta_only = outage_event(3, "OUTAGE_UPDATED", version=3, node_ids=("BLR-OLT-0002", NODE))
    consumer = build(
        db,
        ScriptedFeed(
            page(outage_event(1, node_ids=("BLR-OLT-0002",))), page(wider), page(eta_only)
        ),
    )

    first = await consumer.poll_once()
    widened = await consumer.poll_once()
    unchanged = await consumer.poll_once()

    assert (first.notifications, first.contacts_woken) == (0, 0)  # nobody is mapped there alone
    assert (widened.notifications, widened.contacts_woken) == (1, 1)
    assert (unchanged.notifications, unchanged.contacts_woken) == (0, 0)
    assert await events_of(inbox, on_new_node) == [("outage.declared", {"outage_id": "OUT-1"})]


async def test_conversation_whose_turn_is_running_is_told_as_well(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    """It may be about to tell the customer what this event has just made untrue."""
    await seed_reference(db)
    mid_turn = await waiting_contact(contacts, status="IN_PROGRESS", outage_id="OUT-1")
    handed_off = await waiting_contact(contacts, status="HANDOFF", outage_id="OUT-1")
    resolved = outage_event(2, "OUTAGE_RESOLVED", version=2, resolved_at=NOW)
    consumer = build(db, ScriptedFeed(page(outage_event(1), resolved)))

    await consumer.poll_once()

    assert await events_of(inbox, mid_turn) == [("outage.resolved", {"outage_id": "OUT-1"})]
    assert await events_of(inbox, handed_off) == []


@pytest.mark.parametrize(
    ("emitted_at", "announced"),
    [
        (NOW - timedelta(minutes=14), 1),
        (NOW - timedelta(minutes=16), 0),  # learnt too late to be news
        (NOW + timedelta(minutes=10), 0),  # stamped in the future: not trusted to be current
    ],
)
async def test_outage_is_announced_only_when_its_event_is_current(
    db: Database, actions: ActionRepository, emitted_at: datetime, announced: int
) -> None:
    await seed_reference(db)
    consumer = build(db, ScriptedFeed(page(outage_event(1, emitted_at=emitted_at))))

    outcome = await consumer.poll_once()

    assert (outcome.declared, outcome.notifications) == (1, announced)
    assert (await ReadModelRepository(db).get_outage("OUT-1")) is not None


async def test_node_the_reference_data_does_not_know_reaches_nobody_and_breaks_nothing(
    db: Database,
) -> None:
    await seed_reference(db)
    consumer = build(db, ScriptedFeed(page(outage_event(1, node_ids=("ZZZ-OLT-9999",)))))

    outcome = await consumer.poll_once()

    assert (outcome.applied, outcome.declared, outcome.notifications) == (1, 1, 0)


async def test_resolution_reaches_only_those_who_were_told_and_wakes_who_waits(
    db: Database, contacts: ContactRepository, inbox: InboxRepository, actions: ActionRepository
) -> None:
    await seed_reference(db)
    waiting = await waiting_contact(contacts, outage_id="OUT-1")
    resolved = outage_event(2, "OUTAGE_RESOLVED", version=2, resolved_at=NOW)
    consumer = build(db, ScriptedFeed(page(outage_event(1)), page(resolved)))
    await consumer.poll_once()

    outcome = await consumer.poll_once()

    assert (outcome.resolved, outcome.notifications, outcome.contacts_woken) == (1, 1, 1)
    started = await actions.get_by_key(STARTED_KEY)
    restored = await actions.get_by_key("notify:C1:OUT-1:outage_resolved.v1")
    assert started is not None
    assert restored is not None
    assert restored.depends_on == started.action_id  # never before, or without, the first one
    assert await events_of(inbox, waiting) == [("outage.resolved", {"outage_id": "OUT-1"})]


async def test_resolution_of_an_outage_nobody_was_told_about_notifies_nobody(
    db: Database, actions: ActionRepository
) -> None:
    await seed_reference(db)
    over_already = outage_event(4, "OUTAGE_RESOLVED", version=3, resolved_at=NOW)
    consumer = build(db, ScriptedFeed(page(over_already)))

    outcome = await consumer.poll_once()

    assert (outcome.resolved, outcome.notifications) == (1, 0)
    assert await actions.get_by_key("notify:C1:OUT-1:outage_resolved.v1") is None


async def test_declared_outage_upgrades_the_suspected_ticket_and_resolution_closes_it(
    db: Database, actions: ActionRepository, episodes: EpisodeRepository
) -> None:
    await seed_reference(db)
    suspected = (await episodes.open_or_join(NODE, "broadband", outage_id=None)).episode
    ticket = await actions.propose(_incident_open(suspected.episode_id))
    resolved = outage_event(2, "OUTAGE_RESOLVED", version=2, resolved_at=NOW)
    consumer = build(db, ScriptedFeed(page(outage_event(1)), page(resolved)))

    await consumer.poll_once()
    upgrade = await actions.get_by_key(f"incident-update:{suspected.episode_id}:OUT-1")
    await consumer.poll_once()
    closing = await actions.get_by_key(f"incident-resolve:{suspected.episode_id}")

    assert upgrade is not None
    assert upgrade.request_body == {"kind": "NETWORK_OUTAGE", "outage_id": "OUT-1"}
    assert upgrade.depends_on == ticket.action.action_id
    assert closing is not None
    assert closing.request_body == {"status": "RESOLVED"}
    assert await episodes.find_open(NODE, "broadband") is None


async def test_no_ticket_is_touched_for_an_episode_that_never_had_one(
    db: Database, actions: ActionRepository, episodes: EpisodeRepository
) -> None:
    await seed_reference(db)
    suspected = (await episodes.open_or_join(NODE, "broadband", outage_id=None)).episode
    consumer = build(db, ScriptedFeed(page(outage_event(1))))

    await consumer.poll_once()

    assert await actions.get_by_key(f"incident-update:{suspected.episode_id}:OUT-1") is None
    upgraded = await episodes.find_open(NODE, "broadband")
    assert upgraded is not None
    assert upgraded.outage_id == "OUT-1"  # the episode itself still learns its outage


async def test_cleared_alarm_ends_the_suspected_episode_unless_another_alarm_stands(
    db: Database, actions: ActionRepository, episodes: EpisodeRepository
) -> None:
    await seed_reference(db)
    suspected = (await episodes.open_or_join(NODE, "broadband", outage_id=None)).episode
    await actions.propose(_incident_open(suspected.episode_id))
    consumer = build(
        db,
        ScriptedFeed(
            page(
                alarm_event(1, "ALARM_RAISED", node_id="BLR-AGG-0001", alarm_id="ALM-AGG"),
                alarm_event(2, "ALARM_RAISED", node_id=NODE, alarm_id="ALM-OLT"),
                alarm_event(3, "ALARM_CLEARED", node_id="BLR-AGG-0001", alarm_id="ALM-AGG"),
            ),
            page(alarm_event(4, "ALARM_CLEARED", node_id=NODE, alarm_id="ALM-OLT")),
        ),
    )

    await consumer.poll_once()
    still_suspected = await episodes.find_open(NODE, "broadband")
    await consumer.poll_once()

    assert still_suspected is not None  # the node's own alarm was still standing
    assert await episodes.find_open(NODE, "broadband") is None
    closing = await actions.get_by_key(f"incident-resolve:{suspected.episode_id}")
    assert closing is not None
    assert closing.request_body == {"status": "RESOLVED"}


async def test_stopping_abandons_a_long_poll_at_once(db: Database) -> None:
    class SilentFeed:
        async def events(self, **_: Any) -> Result[EventPage]:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    consumer = build(db, SilentFeed())
    stop = asyncio.Event()

    running = asyncio.create_task(consumer.run_forever(stop))
    await asyncio.sleep(0.05)
    stop.set()

    await asyncio.wait_for(running, timeout=1.0)


async def test_loop_survives_an_unexpected_failure_and_keeps_polling(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consumer_module, "ERROR_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(consumer_module, "LONG_POLL_SECONDS", 0)
    stop = asyncio.Event()

    class FlakyFeed:
        calls = 0

        async def events(self, *, after_seq: int, **_: Any) -> Result[EventPage]:
            FlakyFeed.calls += 1
            if FlakyFeed.calls == 1:
                raise RuntimeError("bug in the client")
            if FlakyFeed.calls >= 3:
                stop.set()
            return page(last_seq=after_seq)

    await asyncio.wait_for(build(db, FlakyFeed()).run_forever(stop), timeout=2.0)

    assert FlakyFeed.calls >= 3
    state = await ReadModelRepository(db).feed_state()
    assert state.heartbeat_at == NOW
    assert state.last_error is None  # a later good poll cleared the recorded failure


async def test_unexpected_failure_is_recorded_where_an_operator_looks(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consumer_module, "ERROR_BACKOFF_SECONDS", 0.01)
    stop = asyncio.Event()

    class BrokenFeed:
        async def events(self, **_: Any) -> Result[EventPage]:
            stop.set()
            raise RuntimeError("bug in the client")

    await asyncio.wait_for(build(db, BrokenFeed()).run_forever(stop), timeout=2.0)

    state = await ReadModelRepository(db).feed_state()
    assert state.last_error == "UNEXPECTED: RuntimeError"
    assert state.heartbeat_at is None


def _incident_open(episode_id: UUID) -> NewAction:
    return NewAction(
        action_key=f"incident:{episode_id}",
        action_type=INCIDENT_OPEN,
        requested_by="agent:diagnostics",
        episode_id=episode_id,
        correlation_id="corr-1",
        request_body={"node_id": NODE, "service_type": "broadband", "kind": "SUSPECTED_NODE_FAULT"},
    )
