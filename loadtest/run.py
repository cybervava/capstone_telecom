"""One run of the surge against the running stack, from the declared outage to the facts.

1. The NOC declares an outage on each of the run's aggregation nodes, and the platform is given
   a moment to learn of it: the outage is live and known before anyone calls.
2. In the run with the Network Monitoring API down, it goes down now and stays down.
3. The contacts are offered on schedule. What staff see of the backlog is read all the while.
4. When the last caller has left, the backlog is given time to drain, and what the platform
   recorded about the run's contacts is read from its database.
5. The dependency is brought back, the NOC resolves the outages, and how long the news takes to
   reach everyone who waits on them is measured: the wave that follows a surge.

Whatever happens, the simulator is left with no fault and no outage of this run.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from loadtest.callers import Callers, Pace
from loadtest.facts import (
    after_the_wave,
    database_time,
    read_facts,
    told_of_the_restoration,
    waiting_on_an_outage,
)
from loadtest.outcome import Offered, Targets
from loadtest.population import Cast
from loadtest.stack import LoadSample, Noc, Outage, StackNotReadyError, Watch, staff_headers
from skylink_care.db.database import Database

NETWORK = "network"
PROGRESS_EVERY_SECONDS = 60.0


@dataclass(frozen=True)
class Plan:
    name: str  # in keys and file names
    title: str
    fault: str | None  # how the Network Monitoring API fails in this run, or None
    due: tuple[float, ...]
    cast: Cast


@dataclass(frozen=True)
class Waits:
    """How long the run waits for things (seconds)."""

    settle: float = 20.0  # after the outage is declared, before the first call
    drain: float = 600.0  # the longest the backlog is given to drain after the surge
    restoration: float = 1800.0  # the longest the news of the restoration is given
    breaker_rest: float = 11.0  # after the dependency is back, before it is relied on


@dataclass(frozen=True)
class Rig:
    api: httpx.AsyncClient
    noc: Noc
    db: Database
    staff_key: str
    targets: Targets
    pace: Pace
    waits: Waits
    # What the data pack says hangs under each aggregation node; the simulator must agree.
    nodes_under: Mapping[str, tuple[str, ...]]
    say: Callable[[str], None]


@dataclass(frozen=True)
class Result:
    plan: Plan
    started_at: datetime
    ended_at: datetime
    outages: tuple[Outage, ...]
    offered: tuple[Offered, ...]
    samples: tuple[LoadSample, ...]
    missed_readings: int
    # The latest the generator offered a contact after it was due (its own lag, in seconds).
    generator_lag: float
    drained_after: float | None
    facts: Mapping[str, Any]
    restoration: Mapping[str, Any]


async def run(plan: Plan, rig: Rig) -> Result:
    centre = await staff_headers(rig.api, "channel", "load.centre", rig.staff_key)

    async def as_ops() -> dict[str, str]:
        return await staff_headers(rig.api, "ops", "load.watch", rig.staff_key)

    outages: list[Outage] = []
    try:
        await _declare(plan, rig, outages)
        await asyncio.sleep(rig.waits.settle)
        if plan.fault:
            await rig.noc.fail(NETWORK, plan.fault)
            rig.say(f"  the Network Monitoring API is {plan.fault}")
        started_at = datetime.now(UTC)
        async with Watch(rig.api, await as_ops(), as_ops) as watch, _progress(watch, rig.say):
            callers = Callers(rig.api, channel_headers=centre, targets=rig.targets, pace=rig.pace)
            rig.say(f"  offering {len(plan.due):,} contacts over {plan.due[-1]:.0f} s")
            offered = await callers.offer_all(plan.due, plan.cast.callers)
            rig.say("  the last caller has left; waiting for the backlog to drain")
            drained_after = await watch.until_keeping_up(rig.waits.drain)
            ended_at = datetime.now(UTC)
            ids = [outage.outage_id for outage in outages]
            facts = await read_facts(rig.db, started_at, ended_at, ids)
            surge_readings = tuple(watch.samples)
            restoration = await _restoration(plan, rig, outages, watch, started_at, ended_at)
            return Result(
                plan=plan,
                started_at=started_at,
                ended_at=ended_at,
                outages=tuple(outages),
                offered=tuple(offered),
                samples=surge_readings,
                missed_readings=watch.missed,
                generator_lag=round(callers.worst_lag_seconds, 3),
                drained_after=drained_after,
                facts=facts,
                restoration=restoration,
            )
    finally:
        await _leave_clean(rig, outages)


async def _declare(plan: Plan, rig: Rig, outages: list[Outage]) -> None:
    for root in plan.cast.roots:
        outage = await rig.noc.declare(root)
        outages.append(outage)
        # An aggregation node carries mobile towers as well; the callers are on its broadband
        # nodes, and every one of those must be out.
        broadband = set(rig.nodes_under.get(root, ()))
        if not broadband or not broadband <= set(outage.nodes):
            raise StackNotReadyError(
                f"the outage on {root} does not cover the broadband nodes the data pack puts"
                " under it: the stack was not seeded from this pack. Run `make dev` and pass"
                " the same --data."
            )
    nodes = sum(len(rig.nodes_under[outage.root]) for outage in outages)
    rig.say(f"  the NOC declared {len(outages)} outage(s) over {nodes} broadband nodes")


async def _restoration(
    plan: Plan, rig: Rig, outages: list[Outage], watch: Watch, start: datetime, end: datetime
) -> dict[str, Any]:
    """Bring the dependency back, resolve the outages and time the news reaching everyone.

    Everyone who waited is accounted for: told that it is over, or standing where the platform
    left them when the time allowed was up.
    """
    if plan.fault:
        await rig.noc.restore()
        await asyncio.sleep(rig.waits.breaker_rest)
    waiting = await waiting_on_an_outage(rig.db, start, end)
    since = await database_time(rig.db)
    loop = asyncio.get_running_loop()
    began, already = loop.time(), len(watch.samples)
    for outage in outages:
        await rig.noc.resolve(outage)
    rig.say(f"  the NOC resolved the outage(s); {len(waiting):,} contacts wait to hear of it")
    told = 0
    while loop.time() - began < rig.waits.restoration:
        told = await told_of_the_restoration(rig.db, waiting)
        if told >= len(waiting):
            break
        await asyncio.sleep(2.0)
    took = round(loop.time() - began, 1)
    wave = watch.samples[already:]
    return {
        "waiting_on_the_outage": len(waiting),
        "told_it_is_over": told,
        # None: not everyone had been told when the time allowed was up.
        "seconds": took if told >= len(waiting) else None,
        "allowed_seconds": rig.waits.restoration,
        **await after_the_wave(rig.db, waiting, since),
        "most_waiting_for_a_turn": max((sample.waiting for sample in wave), default=0),
        "longest_wait_seconds": max((sample.oldest_seconds for sample in wave), default=0.0),
    }


async def _leave_clean(rig: Rig, outages: list[Outage]) -> None:
    """No fault and no outage of this run is left behind, however the run ended."""
    with contextlib.suppress(httpx.HTTPError):
        await rig.noc.restore()
    for outage in outages:
        # Already resolved in the ordinary course: the simulator refuses, and that is fine.
        with contextlib.suppress(httpx.HTTPError):
            await rig.noc.resolve(outage)


@contextlib.asynccontextmanager
async def _progress(watch: Watch, say: Callable[[str], None]) -> AsyncIterator[None]:
    """A line a minute, so that a long run can be seen to be alive."""

    async def tell() -> None:
        while True:
            await asyncio.sleep(PROGRESS_EVERY_SECONDS)
            if watch.samples:
                last = watch.samples[-1]
                say(
                    f"    {last.at:>6.0f} s: {last.waiting} waiting for a turn, the longest for"
                    f" {last.oldest_seconds:.0f} s{' (behind)' if last.behind else ''}"
                )

    task = asyncio.get_running_loop().create_task(tell())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
