"""Surge mode: what the platform does differently while the orchestrator is behind.

Behind means that contacts queue for their turn: so many that the queue is long whatever its
pace, or more than the orchestrator runs at once with one of them waiting long. A single turn
that takes long is not a queue. While the orchestrator is behind

* a customer who writes in is told at once that the reply will take longer than usual (the
  complaint reference is issued as always, before anything else), and
* a message is read by the rules instead of the language model, so that a turn costs what the
  rules cost.

Nothing else changes. What may be said about the network, and what may be done on the strength
of it, needs the same proof under load as without.

The gauge is never waited on by a customer's request. Whoever asks whether the orchestrator is
behind gets the last measurement at once; one that has aged is renewed behind the caller, and
one too old to describe the present is not passed on. The API keeps its gauge fresh whether or
not anyone asks, so that the first caller after a quiet spell is answered from the present.
Staff, who ask for the figures, wait for a measurement, and however many of them ask there is
one measurement in a refresh interval.

The inbox is counted by one reader at a time, whoever asks: a second count is never started
beside one that is on its way, and a count that cannot be had within a few seconds is given
up, so that the gauge is never one more thing waiting on a database that is struggling.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from skylink_care.config import Settings
from skylink_care.db.database import Database
from skylink_care.db.inbox import Backlog, InboxRepository

log = structlog.get_logger(__name__)


class BacklogUnknownError(RuntimeError):
    """The backlog could not be measured: now, or a moment ago for another caller."""


@dataclass(frozen=True)
class SurgePolicy:
    # Behind: this many contacts wait for a turn, however fast the queue moves ...
    waiting_contacts: int = 200
    # ... or more wait than the orchestrator runs at once (its concurrency), and one of them
    # has waited this long (seconds).
    waiting_seconds: float = 15.0
    running_at_once: int = 8
    # A measurement is renewed when it is this old, and not believed any more when it is that
    # old: a process nobody has asked for a while does not answer from what it knew long ago.
    refresh_seconds: float = 2.0
    believed_seconds: float = 10.0
    # A count is given up after this long, the wait for a database connection included. The
    # statement alone is stopped by the database sooner (``db.inbox.BACKLOG_TIMEOUT_MS``).
    measure_seconds: float = 3.0

    def behind(self, backlog: Backlog) -> bool:
        queueing = backlog.contacts > self.running_at_once
        waited_long = backlog.oldest_seconds >= self.waiting_seconds
        return backlog.contacts >= self.waiting_contacts or (queueing and waited_long)


class SurgeGauge:
    def __init__(
        self,
        read: Callable[[], Awaitable[Backlog]],
        policy: SurgePolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read
        self._policy = policy
        self._clock = clock
        self._behind = False
        self._last: Backlog | None = None
        # When the count behind ``_last`` began: it describes the inbox no later than that.
        self._measured_at: float | None = None
        # When the last count ended, and whether it failed: the next is due an interval later.
        self._tried_at: float | None = None
        self._failed = False
        self._renewal: asyncio.Task[None] | None = None
        self._measuring = asyncio.Lock()

    def behind(self) -> bool:
        """Whether the orchestrator is behind, as last measured. Returns at once.

        Before the first measurement, and when the last one is too old to believe, the answer
        is no: being behind is not claimed on what is not known.
        """
        now = self._clock()
        due = self._tried_at is None or now - self._tried_at >= self._policy.refresh_seconds
        if due and self._renewal is None:
            self._renew_behind_the_caller()
        if self._measured_at is None or now - self._measured_at > self._policy.believed_seconds:
            return False
        return self._behind

    async def figures(self) -> Backlog:
        """The backlog itself, no older than the refresh interval (what staff are shown).

        Those who ask together, or soon after one another, share one measurement: the desk of
        every member of staff asks every few seconds, and the queue is longest to count just
        when the database has least to spare.
        """
        async with self._measuring:
            tried = self._tried_at
            if tried is not None and self._clock() - tried < self._policy.refresh_seconds:
                if self._failed:
                    # It could not be counted a moment ago. Those who ask now are told so, and
                    # are not made to wait in line for a count of their own.
                    raise BacklogUnknownError("the backlog could not be measured a moment ago")
                if self._last is not None:
                    return self._last
            return await self._count()

    async def measure(self) -> Backlog:
        """Measure now, whatever was measured before (after a count that is on its way)."""
        async with self._measuring:
            return await self._count()

    async def _count(self) -> Backlog:
        """One count of the inbox. The caller holds ``_measuring``: no two counts overlap, so
        an older one never overwrites a newer one."""
        began = self._clock()
        try:
            async with asyncio.timeout(self._policy.measure_seconds):
                backlog = await self._read()
        except Exception as exc:
            self._tried_at, self._failed = self._clock(), True
            # The database, usually. The last measurement stands until it is too old to believe.
            log.warning("surge_measurement_failed", error=type(exc).__name__)
            raise BacklogUnknownError("the backlog could not be measured") from exc
        behind = self._policy.behind(backlog)
        if behind != self._behind:
            log.warning(
                "surge_started" if behind else "surge_ended",
                waiting_contacts=backlog.contacts,
                oldest_seconds=round(backlog.oldest_seconds, 1),
            )
        self._behind, self._last, self._measured_at = behind, backlog, began
        self._tried_at, self._failed = self._clock(), False
        return backlog

    async def keep_fresh(self) -> None:
        """Measure every refresh interval for as long as this runs (cancel it to stop).

        Asking renews a measurement only for whoever asks next. In a process whose callers
        come far apart, nobody would ever find one young enough to believe, and that is the
        orchestrator stopped on a quiet night: the case in which every caller should be told.
        """
        while True:
            await self._renew()
            await asyncio.sleep(self._policy.refresh_seconds)

    async def settled(self) -> None:
        """Wait for a measurement that is on its way, if one is."""
        renewal = self._renewal
        if renewal is not None:
            # However it ended: a renewal that was cancelled is not the caller being cancelled.
            # And the other way round: a caller who stops waiting does not stop the renewal.
            await asyncio.wait({renewal})

    async def aclose(self) -> None:
        """End a measurement that is on its way: the process is closing its database."""
        renewal = self._renewal
        if renewal is not None:
            renewal.cancel()
            await asyncio.wait({renewal})

    def _renew_behind_the_caller(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop to run it on: the caller is answered from what there is
        self._renewal = loop.create_task(self._renew())
        # Cleared when the task is done however it ended, also when it was cancelled before
        # it ran a line of its own: the gauge never goes on waiting for a renewal that is gone.
        self._renewal.add_done_callback(self._renewed)

    def _renewed(self, _: asyncio.Task[None]) -> None:
        self._renewal = None

    async def _renew(self) -> None:
        """A measurement if one is due, shared with whoever else is asking for the figures.
        Never raises: nobody is waiting for it who could be told."""
        try:
            await self.figures()
        except BacklogUnknownError:
            # Said in the log where it happened. The last measurement stands, and the next
            # try waits for the refresh interval like any other.
            pass
        except Exception:
            log.exception("surge_gauge_failed")


def build_gauge(settings: Settings, database: Database) -> SurgeGauge:
    """The gauge of one process, over the inbox every process shares."""
    return SurgeGauge(
        InboxRepository(database).backlog,
        SurgePolicy(
            waiting_contacts=settings.surge_waiting_contacts,
            waiting_seconds=settings.surge_waiting_seconds,
            running_at_once=settings.worker_concurrency,
        ),
    )
