"""Outage read model: what the NOC event feed has told the platform.

Events are applied in sequence order inside one transaction together with the feed cursor, so an
event is applied exactly once. Within one outage a lower or equal version never overwrites a
higher one, which makes a duplicated or late event harmless.

The model is only as good as the feed it was built from. When an event is known to be lost or
unusable (a hole in the sequence, an outage event without its key fields) the model is marked as
having a gap: it may be missing a resolution, so it stops counting as evidence until an operator
has reconciled it. Later events are still applied, so nothing else is lost in the meantime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from skylink_care.adapters.models import NetworkEventDto
from skylink_care.db.base import Repository
from skylink_care.db.database import Connection
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import FeedView

FEED = "noc"
_OUTAGE_EVENTS = frozenset({"OUTAGE_STARTED", "OUTAGE_UPDATED", "OUTAGE_RESOLVED"})
# An alarm whose severity the source did not state is treated as critical: an unknown alarm
# must not make a node look healthier than a known one would.
UNKNOWN_SEVERITY = "unknown"
_SUSPICIOUS_SEVERITIES = ("critical", UNKNOWN_SEVERITY)
_OUTAGE_COLUMNS = "outage_id, version, cause, planned, started_at, eta, eta_source, resolved_at"


class Change(StrEnum):
    DECLARED = "DECLARED"  # the platform has just learnt of this outage
    UPDATED = "UPDATED"
    RESOLVED = "RESOLVED"
    ALARM_CLEARED = "ALARM_CLEARED"  # an alarm the read model held as active has ended
    IGNORED = "IGNORED"  # duplicate, older than what is already known, or nothing to act on
    MALFORMED = "MALFORMED"  # the event lacks what is needed to apply it


@dataclass(frozen=True)
class OutageChange:
    change: Change
    outage: OutageInfo | None = None
    # Every node the outage covers, and those this event added to it.
    node_ids: tuple[str, ...] = ()
    new_node_ids: tuple[str, ...] = ()
    # When the source emitted the event. Late events are applied but not announced to customers.
    emitted_at: datetime | None = None
    # For MALFORMED: what was missing.
    detail: str | None = None


@dataclass(frozen=True)
class FeedState:
    applied_seq: int
    source_last_seq: int
    heartbeat_at: datetime | None
    polled_at: datetime | None
    last_error: str | None
    gap_at: datetime | None
    gap_detail: str | None

    @property
    def caught_up(self) -> bool:
        return self.applied_seq >= self.source_last_seq

    @property
    def intact(self) -> bool:
        """No event is known to have been lost since the model was last reconciled."""
        return self.gap_at is None


class ReadModelRepository(Repository):
    async def feed_state(self, *, conn: Connection | None = None) -> FeedState:
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "SELECT applied_seq, source_last_seq, heartbeat_at, polled_at, last_error,"
                " gap_at, gap_detail FROM feed_state WHERE feed = %s",
                (FEED,),
            )
            row = await cursor.fetchone()
        if row is None:  # pragma: no cover - the row is created by the migration
            raise RuntimeError("feed_state row is missing")
        return FeedState(**row)

    async def record_poll(
        self,
        conn: Connection,
        *,
        applied_seq: int,
        source_last_seq: int,
        heartbeat_at: datetime,
        polled_at: datetime,
    ) -> None:
        """Advance the cursor and the heartbeat. Same transaction as the events it covers."""
        await conn.execute(
            "UPDATE feed_state SET applied_seq = greatest(applied_seq, %s), source_last_seq = %s,"
            " heartbeat_at = %s, polled_at = %s, last_error = NULL, last_error_at = NULL"
            " WHERE feed = %s",
            (applied_seq, source_last_seq, heartbeat_at, polled_at, FEED),
        )

    async def record_error(self, error: str, *, at: datetime) -> None:
        """The feed could not be read. The heartbeat is left alone, so staleness keeps growing."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE feed_state SET last_error = %s, last_error_at = %s WHERE feed = %s",
                (error[:500], at, FEED),
            )

    async def record_gap(self, conn: Connection, detail: str, *, at: datetime) -> None:
        """An event was lost or unusable. The first gap is kept: it is where to start looking."""
        await conn.execute(
            "UPDATE feed_state SET gap_at = COALESCE(gap_at, %s),"
            " gap_detail = COALESCE(gap_detail, %s) WHERE feed = %s",
            (at, detail[:500], FEED),
        )

    async def clear_gap(self) -> bool:
        """An operator has reconciled the model with the NOC. Returns whether a gap was marked."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE feed_state SET gap_at = NULL, gap_detail = NULL"
                " WHERE feed = %s AND gap_at IS NOT NULL",
                (FEED,),
            )
            return cursor.rowcount == 1

    async def apply(self, conn: Connection, event: NetworkEventDto) -> OutageChange:
        """Apply one feed event. Returns what changed, for the reactions that follow."""
        if event.type in _OUTAGE_EVENTS:
            return await self._apply_outage(conn, event)
        if event.type == "ALARM_RAISED":
            return await self._raise_alarm(conn, event)
        return await self._clear_alarm(conn, event)

    async def _raise_alarm(self, conn: Connection, event: NetworkEventDto) -> OutageChange:
        if not event.alarm_id or not event.node_ids:
            return OutageChange(Change.MALFORMED, detail="alarm without alarm_id or node_ids")
        for node_id in event.node_ids:
            await conn.execute(
                "INSERT INTO node_alarms (alarm_id, node_id, alarm_type, severity, raised_at,"
                " last_event_seq) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (alarm_id, node_id) DO NOTHING",
                (
                    event.alarm_id,
                    node_id,
                    event.alarm_type or "UNKNOWN",
                    event.severity or UNKNOWN_SEVERITY,
                    event.emitted_at,
                    event.seq,
                ),
            )
        return OutageChange(Change.IGNORED)

    async def _clear_alarm(self, conn: Connection, event: NetworkEventDto) -> OutageChange:
        if not event.alarm_id:
            return OutageChange(Change.MALFORMED, detail="alarm clear without alarm_id")
        cursor = await conn.execute(
            "UPDATE node_alarms SET cleared_at = %s, last_event_seq = %s"
            " WHERE alarm_id = %s AND cleared_at IS NULL RETURNING node_id",
            (event.emitted_at, event.seq, event.alarm_id),
        )
        cleared = tuple(sorted(row["node_id"] for row in await cursor.fetchall()))
        if not cleared:
            return OutageChange(Change.IGNORED)  # never seen, or cleared before
        return OutageChange(Change.ALARM_CLEARED, node_ids=cleared, emitted_at=event.emitted_at)

    async def _apply_outage(self, conn: Connection, event: NetworkEventDto) -> OutageChange:
        missing = [
            name
            for name, value in (
                ("outage_id", event.outage_id),
                ("version", event.version),
                ("started_at", event.started_at),
            )
            if value is None
        ]
        if missing or event.outage_id is None:
            return OutageChange(
                Change.MALFORMED, detail=f"{event.type} without {', '.join(missing)}"
            )
        cursor = await conn.execute(
            "SELECT version, resolved_at FROM outages WHERE outage_id = %s FOR UPDATE",
            (event.outage_id,),
        )
        known = await cursor.fetchone()
        if known is not None and event.version is not None and event.version <= known["version"]:
            return OutageChange(Change.IGNORED)
        outage = await self._upsert_outage(conn, event)
        new_nodes = await self._add_nodes(conn, event.outage_id, event.node_ids)
        node_ids = await self._nodes_of(conn, event.outage_id)
        was_open = known is not None and known["resolved_at"] is None
        if outage.resolved_at is not None:
            # Also covers an outage first heard of when it was already over.
            change = Change.RESOLVED if (was_open or known is None) else Change.IGNORED
        elif known is None:
            change = Change.DECLARED
        else:
            change = Change.UPDATED
        return OutageChange(change, outage, node_ids, new_nodes, emitted_at=event.emitted_at)

    async def _upsert_outage(self, conn: Connection, event: NetworkEventDto) -> OutageInfo:
        """Store the newer version. A fact the event does not restate is kept, not blanked."""
        cursor = await conn.execute(
            "INSERT INTO outages (outage_id, version, cause, planned, service_types, started_at,"  # noqa: S608 - constant column list
            " eta, eta_source, resolved_at, last_event_seq)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (outage_id) DO UPDATE SET version = EXCLUDED.version,"
            " cause = COALESCE(%s, outages.cause), planned = COALESCE(%s, outages.planned),"
            " eta = EXCLUDED.eta, eta_source = EXCLUDED.eta_source,"
            " resolved_at = EXCLUDED.resolved_at, last_event_seq = EXCLUDED.last_event_seq,"
            " updated_at = clock_timestamp()"
            f" RETURNING {_OUTAGE_COLUMNS}",
            (
                event.outage_id,
                event.version,
                event.cause or "UNKNOWN",
                bool(event.planned),
                list(event.service_types),
                event.started_at,
                event.eta,
                event.eta_source,
                event.resolved_at,
                event.seq,
                event.cause,
                event.planned,
            ),
        )
        row = await cursor.fetchone()
        if row is None:  # pragma: no cover - an upsert always returns its row
            raise RuntimeError("outage upsert returned nothing")
        return OutageInfo(**row)

    async def _add_nodes(
        self, conn: Connection, outage_id: str, node_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Attach nodes to the outage and return those that were not attached before."""
        added = []
        for node_id in node_ids:
            cursor = await conn.execute(
                "INSERT INTO outage_nodes (outage_id, node_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING RETURNING node_id",
                (outage_id, node_id),
            )
            if await cursor.fetchone() is not None:
                added.append(node_id)
        return tuple(added)

    async def _nodes_of(self, conn: Connection, outage_id: str) -> tuple[str, ...]:
        cursor = await conn.execute(
            "SELECT node_id FROM outage_nodes WHERE outage_id = %s ORDER BY node_id", (outage_id,)
        )
        return tuple(row["node_id"] for row in await cursor.fetchall())

    async def get_outage(self, outage_id: str) -> OutageInfo | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_OUTAGE_COLUMNS} FROM outages WHERE outage_id = %s",  # noqa: S608
                (outage_id,),
            )
            row = await cursor.fetchone()
        return OutageInfo(**row) if row else None

    async def has_critical_alarm(self, node_id: str, *, conn: Connection | None = None) -> bool:
        """An uncleared critical alarm on the node or on the aggregation node above it."""
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "SELECT 1 FROM node_alarms a"
                " WHERE a.cleared_at IS NULL AND a.severity = ANY(%s)"
                " AND (a.node_id = %s OR a.node_id ="
                "      (SELECT parent_node_id FROM nodes WHERE node_id = %s))"
                " LIMIT 1",
                (list(_SUSPICIOUS_SEVERITIES), node_id, node_id),
            )
            return await cursor.fetchone() is not None

    async def view(self, node_id: str, service_type: str) -> FeedView:
        """Evidence about one node for the network decision (implements ``FeedViewSource``).

        An outage declared on the aggregation node above this node covers it too.
        """
        async with self._db.connection() as conn:
            state = await self.feed_state(conn=conn)
            cursor = await conn.execute(
                "SELECT o.outage_id, o.version, o.cause, o.planned, o.started_at, o.eta,"
                " o.eta_source, o.resolved_at"
                " FROM outages o JOIN outage_nodes n ON n.outage_id = o.outage_id"
                " WHERE o.resolved_at IS NULL AND (n.node_id = %s OR n.node_id ="
                "       (SELECT parent_node_id FROM nodes WHERE node_id = %s))"
                " ORDER BY o.started_at DESC, o.outage_id DESC LIMIT 1",
                (node_id, node_id),
            )
            outage_row = await cursor.fetchone()
            alarmed = await self.has_critical_alarm(node_id, conn=conn)
        return FeedView(
            heartbeat_at=state.heartbeat_at,
            active_outage=OutageInfo(**outage_row) if outage_row else None,
            critical_alarm=alarmed,
            # Behind, or known to have lost an event: either way not a current picture.
            caught_up=state.caught_up and state.intact,
        )

    async def stats(self) -> dict[str, Any]:
        """Counts for operators and tests."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT (SELECT count(*) FROM outages WHERE resolved_at IS NULL) AS open_outages,"
                " (SELECT count(*) FROM outages) AS outages,"
                " (SELECT count(*) FROM node_alarms WHERE cleared_at IS NULL) AS active_alarms"
            )
            row = await cursor.fetchone()
        return dict(row) if row else {}
