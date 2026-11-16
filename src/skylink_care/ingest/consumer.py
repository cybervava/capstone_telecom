"""NOC event feed consumer.

Reads the ordered change feed from Network Monitoring, applies each event to the outage read
model and runs its reactions, all in one transaction with the feed cursor. Every successful
answer from the feed, even an empty one, is a heartbeat. When the feed cannot be read the error
is recorded and the heartbeat is left to age: that ageing is how the rest of the platform knows
its picture of the network is going stale.

The feed is another system's output and is not taken on trust. A sequence that runs backwards is
refused. A hole in the sequence, or an outage event that cannot be applied, marks the read model
as having a gap: later events are still applied, but the model no longer counts as evidence until
someone has reconciled it, because the lost event may have been a resolution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import structlog

from skylink_care.adapters.clients import NetworkFeedReader
from skylink_care.adapters.models import EventPage, NetworkEventDto
from skylink_care.adapters.result import ErrorKind, Ok
from skylink_care.clock import Clock
from skylink_care.db.database import Connection, Database
from skylink_care.db.ops import OpsRepository
from skylink_care.db.read_model import Change, ReadModelRepository
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.ingest.reactions import OutageReactions

log = structlog.get_logger(__name__)

PAGE_SIZE = 200
LONG_POLL_SECONDS = 20
ERROR_BACKOFF_SECONDS = 2.0
# The kind of an operations item raised for a feed that cannot go on by itself.
FEED_NEEDS_ATTENTION = "FEED_NEEDS_ATTENTION"
# Failures that waiting does not cure: a key that is refused, an endpoint that is gone, a page
# that cannot be read. The same answer will come at the next poll, and the one after.
_NOT_A_MATTER_OF_WAITING = frozenset(
    {
        ErrorKind.PROTOCOL,
        ErrorKind.UNAUTHORIZED,
        ErrorKind.FORBIDDEN,
        ErrorKind.NOT_FOUND,
        ErrorKind.GONE,
        ErrorKind.INVALID,
    }
)
# A feed that is merely out of reach is a person's to look at once it has been so for this
# long, and the same standing trouble is put to the queue no more often than this.
OUT_OF_REACH_ALERT = timedelta(minutes=10)
RAISE_AGAIN_AFTER = timedelta(minutes=1)
# What a person does about each trouble, as far as that can be said here.
_WHAT_TO_DO = {
    "gap": (
        " Reconcile the open outages with the NOC by hand, then run `care-admin feed-clear-gap`."
    ),
    "malformed outage event": (
        " Reconcile the open outages with the NOC by hand, then run `care-admin feed-clear-gap`."
    ),
    "refused": " The feed's sequence does not continue what was applied: was the source reset?",
    "rejected": " Check the feed's key and address (OSS_KEY_INGEST, CARE_OSS_BASE_URL).",
}


@dataclass(frozen=True)
class PollOutcome:
    applied: int = 0
    declared: int = 0
    resolved: int = 0
    notifications: int = 0
    contacts_woken: int = 0
    # Events that could not be applied (see ``Change.MALFORMED``).
    malformed: int = 0
    error: str | None = None
    # The page was full: more events are waiting and the next poll should not wait.
    more: bool = False


class FeedConsumer:
    def __init__(
        self,
        feed: NetworkFeedReader,
        database: Database,
        read_model: ReadModelRepository,
        reactions: OutageReactions,
        *,
        now: Clock,
        page_size: int = PAGE_SIZE,
        ops: OpsRepository | None = None,
    ) -> None:
        self._feed = feed
        self._db = database
        self._read_model = read_model
        self._reactions = reactions
        self._now = now
        self._page_size = page_size
        # Where a feed goes that cannot go on by itself.
        self._ops = ops or OpsRepository(database)
        self._failing_since: datetime | None = None
        self._raised_at: dict[str, datetime] = {}

    async def poll_once(self, *, wait_seconds: int = 0) -> PollOutcome:
        """Fetch one page of events and apply it. A failed read is reported, never raised."""
        correlation_id = f"ingest-{uuid4().hex[:12]}"
        state = await self._read_model.feed_state()
        result = await self._feed.events(
            after_seq=state.applied_seq,
            limit=self._page_size,
            wait_seconds=wait_seconds,
            correlation_id=correlation_id,
        )
        if not isinstance(result, Ok):
            error = f"{result.kind.value}: {result.code or result.message}"
            await self._read_model.record_error(error, at=self._now())
            log.warning("feed_poll_failed", error=error, applied_seq=state.applied_seq)
            await self._failed_to_read(result.kind, error, state.applied_seq)
            return PollOutcome(error=result.kind.value)
        self._failing_since = None
        page = result.value
        refusal = _refuse(page, state.applied_seq)
        if refusal is not None:
            # Nothing is applied and the heartbeat is not refreshed: a feed that contradicts
            # its own history is not evidence that anything is current.
            await self._read_model.record_error(refusal, at=self._now())
            log.error("feed_page_refused", error=refusal, applied_seq=state.applied_seq)
            await self._needs_a_person("refused", refusal, applied_seq=state.applied_seq)
            return PollOutcome(error=refusal.split(":", 1)[0])
        applied, totals = await self._apply(page, state.applied_seq, correlation_id)
        if not page.events and page.last_seq > applied:
            # The feed says it holds more than it gave us. The model stays "behind" and is not
            # trusted meanwhile; this line is how anyone learns why.
            log.warning("feed_withholds_events", applied_seq=applied, last_seq=page.last_seq)
        return PollOutcome(
            applied=totals["applied"],
            declared=totals["declared"],
            resolved=totals["resolved"],
            notifications=totals["notifications"],
            contacts_woken=totals["woken"],
            malformed=totals["malformed"],
            more=len(page.events) >= self._page_size,
        )

    async def _apply(
        self, page: EventPage, applied: int, correlation_id: str
    ) -> tuple[int, dict[str, int]]:
        """Apply the page's events, their consequences and the new cursor in one transaction.
        Returns the cursor and what was done."""
        totals = dict.fromkeys(
            ("applied", "declared", "resolved", "notifications", "woken", "malformed"), 0
        )
        async with self._db.transaction() as conn:
            for event in sorted(page.events, key=lambda item: item.seq):
                if event.seq <= applied:
                    continue  # delivered before
                if applied and event.seq != applied + 1:
                    # Sequence numbers are the source's; a hole means an event was dropped or
                    # withheld. It cannot be recovered here, and it may have been a resolution.
                    detail = f"expected seq {applied + 1}, received {event.seq}"
                    log.error("feed_sequence_gap", detail=detail)
                    await self._read_model.record_gap(conn, detail, at=self._now())
                    await self._needs_a_person("gap", detail, applied_seq=applied, conn=conn)
                change = await self._read_model.apply(conn, event)
                if change.change is Change.MALFORMED:
                    await self._malformed(conn, event, change.detail)
                    totals["malformed"] += 1
                summary = await self._reactions.on_change(
                    conn, change, correlation_id=correlation_id, now=self._now()
                )
                applied = event.seq
                totals["applied"] += 1
                totals["declared"] += change.change is Change.DECLARED
                totals["resolved"] += change.change is Change.RESOLVED
                totals["notifications"] += summary.notifications
                totals["woken"] += summary.contacts_woken
            await self._read_model.record_poll(
                conn,
                applied_seq=applied,
                source_last_seq=page.last_seq,
                heartbeat_at=page.generated_at,
                polled_at=self._now(),
            )
        return applied, totals

    async def _malformed(
        self, conn: Connection, event: NetworkEventDto, detail: str | None
    ) -> None:
        """An event that cannot be applied is never dropped quietly."""
        log.error(
            "feed_event_malformed",
            seq=event.seq,
            event_id=event.event_id,
            type=event.type,
            detail=detail,
        )
        if event.type.startswith("OUTAGE_"):
            # It may have been the resolution of an outage the model still shows as open.
            await self._read_model.record_gap(
                conn, f"malformed {event.type} at seq {event.seq}: {detail}", at=self._now()
            )
        hides_an_outage = event.type.startswith("OUTAGE_")
        await self._needs_a_person(
            "malformed outage event" if hides_an_outage else "malformed event",
            f"{event.type} at seq {event.seq} could not be applied: {detail}",
            applied_seq=event.seq,
            conn=conn,
            seq=event.seq,
            event_type=event.type,
        )

    async def _failed_to_read(self, kind: ErrorKind, error: str, applied_seq: int) -> None:
        """A poll that got no page. Out of reach is a matter of waiting, for a while; a
        refusal, or a page that cannot be read, is not."""
        now = self._now()
        self._failing_since = self._failing_since or now
        if kind in _NOT_A_MATTER_OF_WAITING:
            what = "unreadable" if kind is ErrorKind.PROTOCOL else "rejected"
            await self._needs_a_person(what, error, applied_seq=applied_seq)
        elif now - self._failing_since >= OUT_OF_REACH_ALERT:
            since = self._failing_since.isoformat(timespec="seconds")
            await self._needs_a_person(
                "out of reach", f"{error}; no page since {since}", applied_seq=applied_seq
            )

    async def _needs_a_person(
        self,
        what: str,
        detail: str,
        *,
        applied_seq: int,
        conn: Connection | None = None,
        **more: Any,
    ) -> None:
        """Queue the feed for operations staff: one item for one kind of trouble.

        Raising the item must never be what stops the feed. Inside the page's transaction it
        has a savepoint of its own, so that a queue that refuses it costs the item and not
        the page; outside one, a failure is logged. Trouble that stands (the same refusal at
        every poll) is put to the queue once a minute, not once a poll.
        """
        now = self._now()
        if conn is None:
            last = self._raised_at.get(what)
            if last is not None and now - last < RAISE_AGAIN_AFTER:
                return
            self._raised_at[what] = now
        untrusted = what != "malformed event"
        said = str(redact_for_log(detail))  # a gateway's own text can quote what it was sent
        try:
            async with _savepoint(conn):
                await self._ops.raise_item(
                    kind=FEED_NEEDS_ATTENTION,
                    subject=f"feed:{what}",
                    summary=_summary(what, said, untrusted=untrusted),
                    details={"detail": said, "applied_seq": applied_seq, **more},
                    priority=1 if untrusted else 3,
                    conn=conn,
                )
        except Exception:
            log.exception("ops_item_not_raised", subject=f"feed:{what}")

    async def catch_up(self, *, max_pages: int = 1000) -> PollOutcome:
        """Poll without waiting until the feed has nothing more. Returns the last page's outcome."""
        outcome = PollOutcome()
        for _ in range(max_pages):
            outcome = await self.poll_once()
            if outcome.error is not None or not outcome.more:
                break
        return outcome

    async def run_forever(self, stop: asyncio.Event) -> None:
        wait_seconds = 0  # the first poll answers at once, so the heartbeat starts immediately
        stopping: asyncio.Future[Any] = asyncio.ensure_future(stop.wait())
        try:
            while not stop.is_set():
                outcome = await self._poll_unless_stopped(wait_seconds, stopping)
                if outcome is None:
                    return
                if outcome.error is not None:
                    await asyncio.wait({stopping}, timeout=ERROR_BACKOFF_SECONDS)
                wait_seconds = 0 if outcome.more or outcome.error else LONG_POLL_SECONDS
        finally:
            stopping.cancel()

    async def _poll_unless_stopped(
        self, wait_seconds: int, stopping: asyncio.Future[Any]
    ) -> PollOutcome | None:
        """One poll, abandoned at once when the process is asked to stop (None).

        A long poll can sit idle for many seconds. Abandoning it is safe at any point: events are
        applied in one transaction, so a cancelled poll has either committed or done nothing.
        """
        poll = asyncio.ensure_future(self.poll_once(wait_seconds=wait_seconds))
        either: set[asyncio.Future[Any]] = {poll, stopping}
        try:
            await asyncio.wait(either, return_when=asyncio.FIRST_COMPLETED)
        finally:
            abandoned = not poll.done()
            if abandoned:
                poll.cancel()  # also when this task itself is being cancelled
        if abandoned:
            (leftover,) = await asyncio.gather(poll, return_exceptions=True)
            if isinstance(leftover, Exception):
                log.error("feed_poll_failed_while_stopping", error=repr(leftover))
            return None
        try:
            return poll.result()
        except Exception as exc:
            # The database, usually. The cursor did not move, so nothing is lost; but an event
            # that fails every time holds up everything behind it, so the failure is recorded
            # where an operator looks, not only in the log.
            log.exception("feed_consumer_error")
            await self._record_unexpected(exc)
            return PollOutcome(error="unexpected")

    async def _record_unexpected(self, error: Exception) -> None:
        """A page that failed while it was being applied: nothing of it was kept, the cursor
        did not move, and the same page will fail again. That is the trouble that holds up
        everything behind it, so it goes where a person looks."""
        try:
            await self._read_model.record_error(
                f"UNEXPECTED: {type(error).__name__}", at=self._now()
            )
            state = await self._read_model.feed_state()
        except Exception:
            log.exception("feed_error_not_recorded")
            return
        await self._needs_a_person(
            "stuck",
            f"a page could not be applied ({type(error).__name__}) and will be tried again",
            applied_seq=state.applied_seq,
        )


def _summary(what: str, detail: str, *, untrusted: bool) -> str:
    """What a person reads about the feed: the trouble, what it means, what to do."""
    meaning = (
        " Until it is put right, what the feed last said about the network is not used as evidence."
        if untrusted
        else ""
    )
    return (
        f"The network event feed needs attention ({what}): {detail}.{meaning}"
        f"{_WHAT_TO_DO.get(what, '')}"
    )


@asynccontextmanager
async def _savepoint(conn: Connection | None) -> AsyncIterator[None]:
    """A savepoint inside the caller's transaction, or nothing when there is none."""
    if conn is None:
        yield
        return
    async with conn.transaction():
        yield


def _refuse(page: EventPage, applied_seq: int) -> str | None:
    """Why a page must not be applied at all, or None when it may be."""
    if page.last_seq < applied_seq:
        return (
            f"SEQUENCE_REGRESSION: feed reports last_seq {page.last_seq},"
            f" but seq {applied_seq} was already applied"
        )
    beyond = [event.seq for event in page.events if event.seq > page.last_seq]
    if beyond:
        return f"PROTOCOL: event seq {max(beyond)} is beyond the page's last_seq {page.last_seq}"
    return None
