"""The surge gauge: whether the orchestrator is behind, known at once and never waited on."""

from __future__ import annotations

import asyncio

import pytest

from skylink_care.db.inbox import Backlog
from skylink_care.orchestrator.worker import DEFAULT_CONCURRENCY
from skylink_care.surge import BacklogUnknownError, SurgeGauge, SurgePolicy

POLICY = SurgePolicy(
    waiting_contacts=40,
    waiting_seconds=15.0,
    running_at_once=8,
    refresh_seconds=2.0,
    believed_seconds=10.0,
)
QUIET = Backlog(contacts=3, oldest_seconds=0.4)
LONG_QUEUE = Backlog(contacts=40, oldest_seconds=2.0)
# One more than the orchestrator runs at once: somebody is waiting behind somebody else.
LONG_WAIT = Backlog(contacts=9, oldest_seconds=15.0)
# As many as it runs at once, each taking very long: slow turns, and nobody queueing.
SLOW_TURNS = Backlog(contacts=8, oldest_seconds=120.0)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Reader:
    """Stands in for the database: answers with what it is given next, and counts the asks."""

    def __init__(self, *answers: Backlog | Exception) -> None:
        self._answers = list(answers)
        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self) -> Backlog:
        self.calls += 1
        await self.release.wait()
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def gauge_over(reader: Reader, clock: Clock | None = None) -> SurgeGauge:
    return SurgeGauge(reader, POLICY, clock=clock or Clock())


async def test_many_contacts_waiting_is_being_behind() -> None:
    gauge = gauge_over(Reader(LONG_QUEUE))

    await gauge.measure()

    assert gauge.behind() is True


async def test_a_queue_in_which_one_has_waited_long_is_being_behind() -> None:
    gauge = gauge_over(Reader(LONG_WAIT))

    await gauge.measure()

    assert gauge.behind() is True


async def test_turns_that_take_long_with_nobody_queueing_behind_them_are_not() -> None:
    """One stuck turn, or eight, is a slow dependency. It must not put every customer's
    message in front of the rules, nor tell every caller that replies are delayed."""
    gauge = gauge_over(Reader(SLOW_TURNS))

    await gauge.measure()

    assert gauge.behind() is False


def test_the_policy_counts_a_queue_from_what_the_orchestrator_runs_at_once() -> None:
    assert SurgePolicy().running_at_once == DEFAULT_CONCURRENCY


async def test_a_short_queue_that_moves_is_not() -> None:
    gauge = gauge_over(Reader(QUIET))

    assert await gauge.measure() == QUIET
    assert gauge.behind() is False


async def test_asking_never_waits_and_what_is_not_known_yet_is_not_claimed() -> None:
    reader = Reader(LONG_QUEUE)
    reader.release.clear()  # the database takes its time
    gauge = gauge_over(reader)

    first = gauge.behind()
    reader.release.set()
    await gauge.settled()

    assert first is False  # nothing measured yet: the answer came at once and claimed nothing
    assert gauge.behind() is True
    assert reader.calls == 1


async def test_a_measurement_is_used_until_it_has_aged_and_is_then_renewed_behind_the_caller() -> (
    None
):
    clock = Clock()
    reader = Reader(LONG_QUEUE, QUIET)
    gauge = gauge_over(reader, clock)
    await gauge.measure()

    clock.now += 1.9
    still = gauge.behind()
    await gauge.settled()
    asked_while_fresh = reader.calls
    clock.now += 0.2
    stale = gauge.behind()  # answered from the old measurement; the new one is on its way
    await gauge.settled()

    assert (still, asked_while_fresh) == (True, 1)
    assert stale is True
    assert reader.calls == 2
    assert gauge.behind() is False


async def test_callers_that_ask_together_cause_one_measurement() -> None:
    reader = Reader(QUIET)
    reader.release.clear()
    gauge = gauge_over(reader)

    for _ in range(50):
        gauge.behind()
    await asyncio.sleep(0)
    reader.release.set()
    await gauge.settled()

    assert reader.calls == 1


async def test_a_measurement_that_fails_leaves_the_last_one_standing() -> None:
    clock = Clock()
    reader = Reader(LONG_QUEUE, RuntimeError("database unavailable"))
    gauge = gauge_over(reader, clock)
    await gauge.measure()

    clock.now += 3.0
    gauge.behind()
    await gauge.settled()

    assert reader.calls == 2
    assert gauge.behind() is True


async def test_a_failing_measurement_is_not_tried_again_by_every_caller() -> None:
    clock = Clock()
    reader = Reader(RuntimeError("database unavailable"))
    gauge = gauge_over(reader, clock)

    for _ in range(5):
        gauge.behind()
        await gauge.settled()
    clock.now += 2.1
    gauge.behind()
    await gauge.settled()

    assert reader.calls == 2  # once, and once more after the refresh interval


async def test_a_measurement_too_old_to_believe_is_not_passed_on() -> None:
    """A process that nobody asked for a while answers the first caller from what it measured
    long ago. That is not knowledge of now, and being behind is not claimed on it."""
    clock = Clock()
    reader = Reader(LONG_QUEUE)
    reader_blocks = reader.release
    gauge = gauge_over(reader, clock)
    await gauge.measure()

    clock.now += 10.1
    reader_blocks.clear()
    too_old = gauge.behind()
    reader_blocks.set()
    await gauge.settled()

    assert too_old is False
    assert gauge.behind() is True  # measured afresh


async def test_outside_an_event_loop_the_gauge_answers_from_what_it_has() -> None:
    clock = Clock()
    reader = Reader(LONG_QUEUE)
    gauge = gauge_over(reader, clock)
    await gauge.measure()

    clock.now += 3.0  # aged: a renewal is due, and a thread has no loop to run it on
    answer = await asyncio.to_thread(gauge.behind)

    assert answer is True
    assert reader.calls == 1


# --- the figures, for staff ----------------------------------------------------------------------


async def test_staff_who_ask_together_or_soon_after_share_one_measurement() -> None:
    clock = Clock()
    reader = Reader(LONG_QUEUE)
    gauge = gauge_over(reader, clock)

    together = await asyncio.gather(*(gauge.figures() for _ in range(20)))
    clock.now += 1.9
    soon_after = await gauge.figures()

    assert together == [LONG_QUEUE] * 20
    assert soon_after == LONG_QUEUE
    assert reader.calls == 1


async def test_figures_that_have_aged_are_measured_again() -> None:
    clock = Clock()
    reader = Reader(LONG_QUEUE, QUIET)
    gauge = gauge_over(reader, clock)

    first = await gauge.figures()
    clock.now += 2.0
    second = await gauge.figures()

    assert (first, second) == (LONG_QUEUE, QUIET)
    assert gauge.behind() is False


async def test_figures_that_cannot_be_measured_are_an_error_and_not_an_old_answer() -> None:
    clock = Clock()
    reader = Reader(LONG_QUEUE, RuntimeError("database unavailable"))
    gauge = gauge_over(reader, clock)
    await gauge.figures()

    clock.now += 5.0
    with pytest.raises(BacklogUnknownError) as failure:
        await gauge.figures()

    assert "database unavailable" in str(failure.value.__cause__)


async def test_a_renewal_that_never_got_to_run_does_not_stop_the_ones_after_it() -> None:
    """A task cancelled before it starts runs none of its own code. The gauge must not go on
    believing that a measurement is on its way."""
    clock = Clock()
    reader = Reader(LONG_QUEUE)
    gauge = gauge_over(reader, clock)

    gauge.behind()
    renewal = gauge._renewal  # the task the gauge started behind the caller
    assert renewal is not None
    renewal.cancel()
    await asyncio.gather(renewal, return_exceptions=True)
    clock.now += 2.0
    gauge.behind()
    await gauge.settled()

    assert reader.calls == 1
    assert gauge.behind() is True


async def test_figures_that_just_failed_are_not_measured_again_for_every_desk() -> None:
    """With the database struggling, twenty desks asking must not become twenty counts in a
    row, each holding a connection until it gives up."""
    clock = Clock()
    reader = Reader(RuntimeError("database unavailable"))
    gauge = gauge_over(reader, clock)

    failures = await asyncio.gather(*(gauge.figures() for _ in range(20)), return_exceptions=True)
    clock.now += 2.0
    later = await asyncio.gather(gauge.figures(), return_exceptions=True)

    # One kind of error, whether the count failed for this desk or for the one before it.
    assert all(isinstance(failure, BacklogUnknownError) for failure in failures)
    assert isinstance(later[0], BacklogUnknownError)
    assert reader.calls == 2  # once for the twenty, and once more an interval later


async def test_waiting_for_a_renewal_that_was_cancelled_is_not_being_cancelled() -> None:
    reader = Reader(LONG_QUEUE)
    reader.release.clear()
    gauge = gauge_over(reader)

    gauge.behind()
    renewal = gauge._renewal  # the task the gauge started behind the caller
    assert renewal is not None
    renewal.cancel()
    await gauge.settled()  # returns; it does not raise the renewal's cancellation here

    assert gauge.behind() is False


async def test_whoever_stops_waiting_for_a_measurement_does_not_stop_the_measurement() -> None:
    """The measurement is shared. One caller giving up on it (a timeout, a cancelled request)
    must not take it from the others, nor leave the gauge without one for an interval."""
    reader = Reader(LONG_QUEUE)
    reader.release.clear()
    gauge = gauge_over(reader)
    gauge.behind()  # a measurement is on its way

    waiter = asyncio.create_task(gauge.settled())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    reader.release.set()
    await gauge.settled()

    assert waiter.cancelled()
    assert gauge.behind() is True
    assert reader.calls == 1


async def test_a_measurement_on_its_way_is_shared_by_whoever_else_asks() -> None:
    """Staff ask for the figures while the database is slow, and a customer's request asks
    whether the assistant is behind. That is one count of the inbox, not two at once."""
    clock = Clock()
    reader = Reader(LONG_QUEUE)
    reader.release.clear()
    gauge = gauge_over(reader, clock)

    for_staff = asyncio.create_task(gauge.figures())
    await asyncio.sleep(0)  # the count for staff has begun
    gauge.behind()  # ... and a renewal is started behind a customer's request
    await asyncio.sleep(0)
    reader.release.set()
    figures = await for_staff
    await gauge.settled()

    assert figures == LONG_QUEUE
    assert reader.calls == 1
    assert gauge.behind() is True


async def test_a_count_that_cannot_even_begin_gives_up_and_is_tried_again_later() -> None:
    """The statement has its own time limit in the database. Waiting for a connection has
    none there, and it is longest just when the backlog matters: the gauge bounds it."""
    clock = Clock()
    reader = Reader(LONG_QUEUE)
    reader.release.clear()  # no connection to be had
    gauge = SurgeGauge(reader, SurgePolicy(waiting_contacts=40, measure_seconds=0.05), clock=clock)

    with pytest.raises(BacklogUnknownError) as failure:
        await gauge.figures()
    reader.release.set()
    clock.now += 2.0
    later = await gauge.figures()

    assert isinstance(failure.value.__cause__, TimeoutError)
    assert later == LONG_QUEUE
    assert reader.calls == 2


async def test_a_measurement_is_as_old_as_its_beginning_not_its_end() -> None:
    """A count that took long describes the inbox as it was when it began. It is believed for
    no longer than that allows."""
    clock = Clock()

    async def slow() -> Backlog:
        clock.now += 4.0  # the count takes four seconds
        return LONG_QUEUE

    gauge = SurgeGauge(slow, POLICY, clock=clock)
    await gauge.measure()  # began at 1000, ended at 1004

    clock.now = 1009.9
    believed = gauge.behind()
    clock.now = 1010.1  # ten seconds and more since it began
    not_any_more = gauge.behind()
    await gauge.aclose()

    assert (believed, not_any_more) == (True, False)


async def test_closing_the_gauge_ends_a_measurement_that_is_on_its_way() -> None:
    reader = Reader(LONG_QUEUE)
    reader.release.clear()
    gauge = gauge_over(reader)
    gauge.behind()
    renewal = gauge._renewal  # the task the gauge started behind the caller
    assert renewal is not None

    await gauge.aclose()

    assert renewal.cancelled()
    assert gauge._renewal is None


# --- kept fresh, for a process whose callers come far apart ---------------------------------------


async def test_kept_fresh_the_gauge_knows_before_anyone_asks() -> None:
    """With the orchestrator stopped and a caller every half minute, nobody's question would
    ever find a measurement young enough to believe: the one case in which every caller should
    be told that replies are delayed is the one in which none would be."""
    reader = Reader(QUIET, LONG_QUEUE)
    gauge = SurgeGauge(reader, SurgePolicy(waiting_contacts=40, refresh_seconds=0.02))

    keeping = asyncio.create_task(gauge.keep_fresh())
    try:
        await asyncio.sleep(0.15)
        first_ask = gauge.behind()
    finally:
        keeping.cancel()
        await asyncio.gather(keeping, return_exceptions=True)

    assert first_ask is True
    assert reader.calls >= 2


async def test_keeping_fresh_goes_on_after_a_measurement_fails() -> None:
    reader = Reader(RuntimeError("database unavailable"), LONG_QUEUE)
    gauge = SurgeGauge(reader, SurgePolicy(waiting_contacts=40, refresh_seconds=0.02))

    keeping = asyncio.create_task(gauge.keep_fresh())
    try:
        await asyncio.sleep(0.15)
    finally:
        keeping.cancel()
        await asyncio.gather(keeping, return_exceptions=True)

    assert gauge.behind() is True
