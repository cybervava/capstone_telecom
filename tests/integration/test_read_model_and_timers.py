"""Outage read model and timers: each event and each timer takes effect exactly once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from skylink_care.adapters.models import NetworkEventDto
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.read_model import Change, ReadModelRepository
from skylink_care.db.timers import CLOSE_QUIET, RECHECK_NETWORK, TimerRepository
from skylink_care.domain.models import Channel
from tests.integration.test_audit_and_reference import seed_reference

T0 = datetime(2026, 11, 15, 4, 30, tzinfo=UTC)
NODE = "BLR-OLT-0001"


def outage_event(seq: int, kind: str = "OUTAGE_STARTED", **overrides: Any) -> NetworkEventDto:
    fields: dict[str, Any] = {
        "seq": seq,
        "event_id": f"evt-{seq}",
        "type": kind,
        "emitted_at": T0 + timedelta(minutes=seq),
        "node_ids": (NODE,),
        "outage_id": "OUT-1",
        "version": 1,
        "service_types": ("broadband",),
        "cause": "FIBRE_CUT",
        "planned": False,
        "started_at": T0,
        "eta": T0 + timedelta(hours=3),
        "eta_source": "noc_estimate",
    }
    return NetworkEventDto(**{**fields, **overrides})


def alarm_event(seq: int, kind: str, *, node_id: str, alarm_id: str = "ALM-1") -> NetworkEventDto:
    return NetworkEventDto(
        seq=seq,
        event_id=f"evt-{seq}",
        type=kind,  # type: ignore[arg-type]
        emitted_at=T0 + timedelta(minutes=seq),
        node_ids=(node_id,),
        alarm_id=alarm_id,
        alarm_type="LOS",
        severity="critical",
    )


async def apply(db: Database, model: ReadModelRepository, *events: NetworkEventDto) -> list[Change]:
    changes = []
    async with db.transaction() as conn:
        for event in events:
            changes.append((await model.apply(conn, event)).change)
    return changes


# --- outages -------------------------------------------------------------------------------------


async def test_first_event_declares_the_outage_and_later_versions_update_it(db: Database) -> None:
    model = ReadModelRepository(db)

    changes = await apply(
        db,
        model,
        outage_event(1),
        outage_event(2, "OUTAGE_UPDATED", version=2, eta=T0 + timedelta(hours=1)),
    )

    assert changes == [Change.DECLARED, Change.UPDATED]
    stored = await model.get_outage("OUT-1")
    assert stored is not None
    assert (stored.version, stored.eta, stored.is_open) == (2, T0 + timedelta(hours=1), True)


async def test_duplicate_and_late_events_change_nothing(db: Database) -> None:
    model = ReadModelRepository(db)
    newest = outage_event(2, "OUTAGE_UPDATED", version=2, eta=T0 + timedelta(hours=1))

    changes = await apply(db, model, outage_event(1), newest, newest, outage_event(3, version=1))

    assert changes == [Change.DECLARED, Change.UPDATED, Change.IGNORED, Change.IGNORED]
    stored = await model.get_outage("OUT-1")
    assert stored is not None
    assert (stored.version, stored.eta) == (2, T0 + timedelta(hours=1))


async def test_resolution_is_reported_once_and_ends_the_outage_for_its_nodes(db: Database) -> None:
    model = ReadModelRepository(db)
    resolved = outage_event(2, "OUTAGE_RESOLVED", version=2, resolved_at=T0 + timedelta(minutes=40))

    async with db.transaction() as conn:
        await model.apply(conn, outage_event(1, node_ids=(NODE, "BLR-OLT-0002")))
        change = await model.apply(conn, resolved)
        again = await model.apply(conn, resolved)

    assert (change.change, again.change) == (Change.RESOLVED, Change.IGNORED)
    # The resolution names every node the outage ever covered, not only those on the last event.
    assert change.node_ids == ("BLR-OLT-0001", "BLR-OLT-0002")
    assert change.outage is not None
    assert change.outage.resolved_at == T0 + timedelta(minutes=40)
    assert (await model.view(NODE, "broadband")).active_outage is None


async def test_outage_first_heard_of_when_already_over_counts_as_resolved(db: Database) -> None:
    model = ReadModelRepository(db)

    (change,) = await apply(
        db, model, outage_event(5, "OUTAGE_RESOLVED", version=3, resolved_at=T0)
    )

    assert change is Change.RESOLVED
    assert (await model.stats())["open_outages"] == 0


async def test_outage_event_without_its_key_fields_is_reported_as_malformed(
    db: Database,
) -> None:
    model = ReadModelRepository(db)

    async with db.transaction() as conn:
        no_version = await model.apply(conn, outage_event(1, version=None))
        no_id = await model.apply(conn, outage_event(2, "OUTAGE_RESOLVED", outage_id=None))

    assert (no_version.change, no_version.detail) == (
        Change.MALFORMED,
        "OUTAGE_STARTED without version",
    )
    assert (no_id.change, no_id.detail) == (Change.MALFORMED, "OUTAGE_RESOLVED without outage_id")
    assert (await model.stats())["outages"] == 0


async def test_update_that_does_not_restate_a_fact_does_not_blank_it(db: Database) -> None:
    model = ReadModelRepository(db)
    planned = outage_event(1, cause="PLANNED_MAINTENANCE", planned=True)
    terse = outage_event(2, "OUTAGE_UPDATED", version=2, cause=None, planned=None, eta=None)

    await apply(db, model, planned, terse)

    stored = await model.get_outage("OUT-1")
    assert stored is not None
    # Still planned maintenance; the estimate, which the update withdrew, is gone.
    assert (stored.planned, stored.cause, stored.eta) == (True, "PLANNED_MAINTENANCE", None)


async def test_update_reports_the_nodes_it_adds_to_an_outage(db: Database) -> None:
    model = ReadModelRepository(db)
    wider = outage_event(2, "OUTAGE_UPDATED", version=2, node_ids=(NODE, "BLR-OLT-0002"))

    async with db.transaction() as conn:
        declared = await model.apply(conn, outage_event(1))
        widened = await model.apply(conn, wider)

    assert declared.new_node_ids == (NODE,)
    assert widened.change is Change.UPDATED
    assert widened.new_node_ids == ("BLR-OLT-0002",)
    assert widened.node_ids == (NODE, "BLR-OLT-0002")


async def test_outage_declared_on_the_aggregation_node_covers_the_nodes_below_it(
    db: Database,
) -> None:
    await seed_reference(db)
    model = ReadModelRepository(db)

    await apply(db, model, outage_event(1, node_ids=("BLR-AGG-0001",)))

    covered = await model.view(NODE, "broadband")
    assert covered.active_outage is not None
    assert covered.active_outage.outage_id == "OUT-1"


# --- alarms --------------------------------------------------------------------------------------


async def test_critical_alarm_on_the_aggregation_node_marks_the_access_nodes_below_it(
    db: Database,
) -> None:
    await seed_reference(db)
    model = ReadModelRepository(db)

    await apply(db, model, alarm_event(1, "ALARM_RAISED", node_id="BLR-AGG-0001"))

    assert (await model.view(NODE, "broadband")).critical_alarm
    assert await model.has_critical_alarm("BLR-OLT-0002")
    assert not await model.has_critical_alarm("BLR-OLT-4040")  # no such node, nothing above it


async def test_alarm_clearing_is_reported_once_with_its_node(db: Database) -> None:
    await seed_reference(db)
    model = ReadModelRepository(db)
    cleared = alarm_event(2, "ALARM_CLEARED", node_id=NODE)

    async with db.transaction() as conn:
        await model.apply(conn, alarm_event(1, "ALARM_RAISED", node_id=NODE))
        first = await model.apply(conn, cleared)
        again = await model.apply(conn, cleared)
        unknown = await model.apply(
            conn, alarm_event(3, "ALARM_CLEARED", node_id=NODE, alarm_id="ALM-9")
        )

    assert (first.change, first.node_ids) == (Change.ALARM_CLEARED, (NODE,))
    assert (again.change, unknown.change) == (Change.IGNORED, Change.IGNORED)
    assert not (await model.view(NODE, "broadband")).critical_alarm
    assert (await model.stats())["active_alarms"] == 0


async def test_alarm_of_unstated_severity_is_treated_as_critical(db: Database) -> None:
    """An alarm we know less about must not make a node look healthier."""
    await seed_reference(db)
    model = ReadModelRepository(db)
    unstated = alarm_event(1, "ALARM_RAISED", node_id=NODE).model_copy(update={"severity": None})
    minor = alarm_event(2, "ALARM_RAISED", node_id="BLR-OLT-0002", alarm_id="ALM-2").model_copy(
        update={"severity": "minor"}
    )

    await apply(db, model, unstated, minor)

    assert await model.has_critical_alarm(NODE)
    assert not await model.has_critical_alarm("BLR-OLT-0002")


async def test_alarm_naming_several_nodes_is_held_and_cleared_on_each(db: Database) -> None:
    await seed_reference(db)
    model = ReadModelRepository(db)
    both = (NODE, "BLR-OLT-0002")
    raised = alarm_event(1, "ALARM_RAISED", node_id=NODE).model_copy(update={"node_ids": both})

    async with db.transaction() as conn:
        await model.apply(conn, raised)
        alarmed = [await model.has_critical_alarm(node, conn=conn) for node in both]
        cleared = await model.apply(conn, alarm_event(2, "ALARM_CLEARED", node_id=NODE))

    assert alarmed == [True, True]
    assert (cleared.change, cleared.node_ids) == (Change.ALARM_CLEARED, both)
    assert (await model.stats())["active_alarms"] == 0


async def test_alarm_without_an_id_is_malformed_not_silently_dropped(db: Database) -> None:
    model = ReadModelRepository(db)
    nameless = alarm_event(1, "ALARM_RAISED", node_id=NODE).model_copy(update={"alarm_id": None})

    async with db.transaction() as conn:
        change = await model.apply(conn, nameless)

    assert change.change is Change.MALFORMED


# --- feed state ----------------------------------------------------------------------------------


async def test_cursor_never_moves_backwards_and_an_error_leaves_the_heartbeat_alone(
    db: Database,
) -> None:
    model = ReadModelRepository(db)
    async with db.transaction() as conn:
        await model.record_poll(
            conn, applied_seq=7, source_last_seq=9, heartbeat_at=T0, polled_at=T0
        )
    behind = await model.feed_state()

    async with db.transaction() as conn:
        await model.record_poll(
            conn,
            applied_seq=3,  # an older page delivered again
            source_last_seq=9,
            heartbeat_at=T0 + timedelta(seconds=20),
            polled_at=T0 + timedelta(seconds=20),
        )
    await model.record_error("TIMEOUT: feed did not answer", at=T0 + timedelta(seconds=45))
    state = await model.feed_state()

    assert (behind.applied_seq, behind.caught_up) == (7, False)
    assert state.applied_seq == 7
    assert state.heartbeat_at == T0 + timedelta(seconds=20)
    assert state.last_error == "TIMEOUT: feed did not answer"
    # A view built while the consumer is behind says so: its heartbeat is not proof of currency.
    assert not (await model.view(NODE, "broadband")).caught_up


async def test_marked_gap_keeps_the_first_cause_and_withdraws_trust_until_cleared(
    db: Database,
) -> None:
    model = ReadModelRepository(db)
    async with db.transaction() as conn:
        await model.record_poll(
            conn, applied_seq=5, source_last_seq=5, heartbeat_at=T0, polled_at=T0
        )
        await model.record_gap(conn, "expected seq 3, received 5", at=T0)
        await model.record_gap(conn, "a later problem", at=T0 + timedelta(minutes=5))

    state = await model.feed_state()
    distrusted = await model.view(NODE, "broadband")
    cleared = await model.clear_gap()
    again = await model.clear_gap()

    assert (state.caught_up, state.intact) == (True, False)
    assert (state.gap_at, state.gap_detail) == (T0, "expected seq 3, received 5")
    # Fully read, but known to have lost an event: not a current picture.
    assert not distrusted.caught_up
    assert (cleared, again) == (True, False)
    assert (await model.view(NODE, "broadband")).caught_up


# --- timers --------------------------------------------------------------------------------------


async def make_contact(contacts: ContactRepository) -> UUID:
    result = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="hello",
            correlation_id=uuid4().hex,
            received_at=T0,
        )
    )
    return result.contact_id


async def timer_events(inbox: InboxRepository, contact_id: UUID) -> list[dict[str, Any]]:
    events = await inbox.fetch_unapplied(contact_id)
    return [event.payload for event in events if event.event_type == "timer.fired"]


async def test_due_timer_becomes_one_inbox_event_and_never_fires_again(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    timers = TimerRepository(db)
    contact_id = await make_contact(contacts)
    await timers.schedule(
        contact_id, RECHECK_NETWORK, fire_at=T0 + timedelta(seconds=60), dedupe_key="recheck:1"
    )

    early = await timers.fire_due(T0 + timedelta(seconds=59))
    fired = await timers.fire_due(T0 + timedelta(seconds=60))
    again = await timers.fire_due(T0 + timedelta(hours=1))

    assert (early, fired, again) == (0, 1, 0)
    assert await timer_events(inbox, contact_id) == [{"kind": RECHECK_NETWORK, "key": "recheck:1"}]


async def test_cancelled_timer_does_not_fire_and_cancelling_can_be_limited_to_one_kind(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    timers = TimerRepository(db)
    contact_id = await make_contact(contacts)
    due = T0 + timedelta(seconds=60)
    await timers.schedule(contact_id, RECHECK_NETWORK, fire_at=due, dedupe_key="recheck:1")
    await timers.schedule(contact_id, CLOSE_QUIET, fire_at=due, dedupe_key="close:1")

    cancelled = await timers.cancel(contact_id, CLOSE_QUIET)
    await timers.fire_due(due)

    assert cancelled == 1
    assert await timer_events(inbox, contact_id) == [{"kind": RECHECK_NETWORK, "key": "recheck:1"}]
    assert await timers.cancel(contact_id) == 0  # nothing is pending any more


async def test_step_that_cancels_and_arms_ends_armed_however_often_it_is_repeated(
    db: Database, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    """A workflow step re-run after a crash cancels the timer its first run armed, then arms it."""
    timers = TimerRepository(db)
    contact_id = await make_contact(contacts)
    due = T0 + timedelta(seconds=60)

    for _ in range(3):
        await timers.cancel(contact_id, CLOSE_QUIET)
        armed = await timers.schedule(contact_id, CLOSE_QUIET, fire_at=due, dedupe_key="close:4")
        assert armed
    fired = await timers.fire_due(due)

    assert fired == 1
    assert await timer_events(inbox, contact_id) == [{"kind": CLOSE_QUIET, "key": "close:4"}]
    # Once fired it stays fired: arming the same key again does not bring it back.
    assert not await timers.schedule(contact_id, CLOSE_QUIET, fire_at=due, dedupe_key="close:4")
    assert await timers.fire_due(due + timedelta(hours=1)) == 0
