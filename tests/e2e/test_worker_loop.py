"""The orchestrator's main loop: every slot busy, no contact waiting behind another's slow turn.

The journeys elsewhere drive the worker one pass at a time. These run the loop the process
runs, with a language model that can be made slow for one customer.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from skylink_care.adapters.gateway import OssGateway
from skylink_care.llm.base import TriageReading, Usage
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.offline import read_with_rules
from skylink_care.orchestrator import worker as worker_module
from skylink_care.orchestrator.graph import build_graph
from skylink_care.orchestrator.worker import OrchestratorWorker
from skylink_care.runtime import build_orchestrator_runtime
from tests.support.platform import Platform
from tests.support.stack import Simulator

DOWN = "My internet has been down since morning"
# The same complaint from the customer whose message the model takes long over.
DOWN_SLOWLY = "My internet has been down since morning, and the wifi light is off as well"


class Model:
    """A language model that takes ``slow_seconds`` over one message and no time over others,
    and knows how many messages it had in hand at once."""

    name = "external"
    model = "external-1"

    def __init__(self, *, slow_seconds: float = 0.0, every_seconds: float = 0.0) -> None:
        self._slow_seconds = slow_seconds
        self._every_seconds = every_seconds
        self.in_hand = 0
        self.most_in_hand = 0

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        self.in_hand += 1
        self.most_in_hand = max(self.most_in_hand, self.in_hand)
        try:
            slow = "wifi light" in masked_text
            await asyncio.sleep(self._slow_seconds if slow else self._every_seconds)
            return read_with_rules(masked_text), Usage()
        finally:
            self.in_hand -= 1


def with_a_model(
    platform: Platform, gateway: OssGateway, model: Model, **worker_options: Any
) -> Platform:
    runtime = build_orchestrator_runtime(
        platform.runtime.settings,
        platform.runtime.db,
        gateway,
        # The model's own pace is the point: it is not cut off as a model that hangs would be.
        llm=LLMGateway(model, timeout_seconds=30),
        now=platform.runtime.now,
    )
    checkpointer = AsyncPostgresSaver(runtime.db.pool)
    options = {"poll_seconds": 0.02, "retry_backoff_seconds": 0.05, **worker_options}
    worker = OrchestratorWorker(
        runtime, build_graph(runtime, checkpointer), checkpointer, **options
    )
    return dataclasses.replace(platform, runtime=runtime, worker=worker, checkpointer=checkpointer)


@asynccontextmanager
async def looping(desk: Platform) -> AsyncIterator[asyncio.Event]:
    """The loop running as it does in the process, stopped and waited for at the end."""
    stop = asyncio.Event()
    loop = asyncio.create_task(desk.worker.run_forever(stop))
    try:
        yield stop
    finally:
        stop.set()
        await asyncio.wait_for(loop, timeout=30)


async def customers_in_an_outage(simulator: Simulator, count: int) -> list[str]:
    """Customers on one node with a declared outage: a turn for them needs no line test."""
    first = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    await simulator.start_outage(str(first["true_node_id"]), eta_minutes=120)
    found = await simulator.find_customers(
        count,
        service_type="broadband",
        mapping_defect="none",
        status="active",
        node_id=first["true_node_id"],
    )
    return [str(customer["customer_id"]) for customer in found]


async def answered(desk: Platform, contacts: list[UUID], within: float) -> list[bool]:
    """Which of these contacts have a reply, looking until all have or the time is up."""
    deadline = asyncio.get_running_loop().time() + within
    while True:
        have = [bool(await desk.replies(contact_id)) for contact_id in contacts]
        if all(have) or asyncio.get_running_loop().time() >= deadline:
            return have
        await asyncio.sleep(0.02)


async def test_a_turn_that_takes_long_does_not_hold_up_the_contacts_behind_it(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    """One customer's message keeps the model busy for seconds. Twelve others write just after.
    They are answered through the free slots, long before the slow turn ends."""
    desk = with_a_model(platform, gateway, Model(slow_seconds=4.0))
    slow_one, *others = await customers_in_an_outage(simulator, 13)

    async with looping(desk):
        slow = await desk.contact(DOWN_SLOWLY, authenticated_customer_id=slow_one)
        await asyncio.sleep(0.3)  # the slow turn is under way
        quick = [
            (await desk.contact(DOWN, authenticated_customer_id=customer)).contact_id
            for customer in others
        ]
        overtook = await answered(desk, quick, within=2.5)
        slow_answered_meanwhile = bool(await desk.replies(slow.contact_id))
        in_the_end = await answered(desk, [slow.contact_id], within=10)

    assert overtook == [True] * 12
    assert slow_answered_meanwhile is False
    assert in_the_end == [True]


async def test_no_more_contacts_are_run_at_once_than_the_worker_is_set_to(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    model = Model(every_seconds=0.15)
    desk = with_a_model(platform, gateway, model, concurrency=2)
    customers = await customers_in_an_outage(simulator, 6)

    async with looping(desk):
        contacts = [
            (await desk.contact(DOWN, authenticated_customer_id=customer)).contact_id
            for customer in customers
        ]
        everyone = await answered(desk, contacts, within=10)

    assert everyone == [True] * 6
    assert model.most_in_hand == 2


async def test_the_loop_survives_a_database_error_and_goes_on(
    platform: Platform,
    gateway: OssGateway,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desk = with_a_model(platform, gateway, Model())
    (customer,) = await customers_in_an_outage(simulator, 1)
    real = desk.runtime.inbox.pending_contacts
    asked = {"times": 0}

    async def restarting(limit: int) -> list[UUID]:
        asked["times"] += 1
        if asked["times"] <= 2:
            raise ConnectionError("database restarted")
        return await real(limit)

    monkeypatch.setattr(desk.runtime.inbox, "pending_contacts", restarting)

    async with looping(desk):
        intake = await desk.contact(DOWN, authenticated_customer_id=customer)
        got_there = await answered(desk, [intake.contact_id], within=10)

    assert asked["times"] > 2
    assert got_there == [True]


async def test_stopping_lets_the_turns_in_progress_finish(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    """A turn that is dropped half-way is run again from its checkpoint, and that is safe. A
    turn that is let finish needs no second run: the customer gets the answer now."""
    desk = with_a_model(platform, gateway, Model(slow_seconds=1.0))
    (customer,) = await customers_in_an_outage(simulator, 1)

    async with looping(desk) as stop:
        intake = await desk.contact(DOWN_SLOWLY, authenticated_customer_id=customer)
        await asyncio.sleep(0.3)  # the turn is with the model
        stop.set()

    assert len(await desk.replies(intake.contact_id)) == 1


async def test_contacts_held_by_other_workers_do_not_hide_the_ones_behind_them(
    platform: Platform,
    gateway: OssGateway,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contact in another worker's hands stays due until its turn ends. When the oldest due
    contacts are all like that, this worker must look past them for one it can start."""
    desk = with_a_model(platform, gateway, Model(), concurrency=2)
    customers = await customers_in_an_outage(simulator, 9)
    elsewhere = {
        (await desk.contact(DOWN, authenticated_customer_id=customer)).contact_id
        for customer in customers[:8]  # a whole page, for a worker of two slots
    }
    ours = await desk.contact(DOWN, authenticated_customer_id=customers[8])
    really_process = desk.worker.process_contact

    async def held_elsewhere(contact_id: UUID) -> bool:
        if contact_id in elsewhere:
            return False  # as the contact's lock answers when another worker holds it
        return await really_process(contact_id)

    monkeypatch.setattr(desk.worker, "process_contact", held_elsewhere)

    async with looping(desk):
        reached = await answered(desk, [ours.contact_id], within=5)

    assert reached == [True]


async def test_a_loop_that_is_cancelled_drops_its_turns_and_they_run_again_later(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    """Asked to stop, the loop lets its turns finish. Cancelled outright, it must not hold the
    process up: the turns are dropped, and they run again from where they stood."""
    desk = with_a_model(platform, gateway, Model(slow_seconds=20.0))
    (customer,) = await customers_in_an_outage(simulator, 1)
    intake = await desk.contact(DOWN_SLOWLY, authenticated_customer_id=customer)

    loop = asyncio.create_task(desk.worker.run_forever(asyncio.Event()))
    await asyncio.sleep(0.4)  # the turn is with the model, for twenty seconds
    loop.cancel()
    await asyncio.wait_for(asyncio.gather(loop, return_exceptions=True), timeout=3)
    dropped = await desk.replies(intake.contact_id)

    again = with_a_model(platform, gateway, Model())
    async with looping(again):
        in_the_end = await answered(again, [intake.contact_id], within=10)

    assert dropped == []
    assert in_the_end == [True]
    assert len(await again.replies(intake.contact_id)) == 1


async def test_a_turn_that_could_not_even_be_put_off_is_not_tried_again_at_once(
    platform: Platform,
    gateway: OssGateway,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed turn is put off by what the inbox records about it. When the contact's lock or
    that record cannot be had either (the database again), nothing says when to try next: the
    loop waits as long as a recorded failure would, not one poll interval."""
    desk = with_a_model(platform, gateway, Model(), retry_backoff_seconds=0.6)
    (customer,) = await customers_in_an_outage(simulator, 1)
    really_process = desk.worker.process_contact
    tried = {"times": 0}

    async def no_database_at_first(contact_id: UUID) -> bool:
        tried["times"] += 1
        if tried["times"] <= 2:
            raise ConnectionError("no connection for the contact's lock")
        return await really_process(contact_id)

    monkeypatch.setattr(desk.worker, "process_contact", no_database_at_first)

    async with looping(desk):
        intake = await desk.contact(DOWN, authenticated_customer_id=customer)
        await asyncio.sleep(0.4)  # twenty poll intervals
        tried_meanwhile = tried["times"]
        got_there = await answered(desk, [intake.contact_id], within=10)

    assert tried_meanwhile == 1
    assert got_there == [True]


async def test_a_stop_asked_for_while_the_inbox_is_read_starts_nothing(
    platform: Platform,
    gateway: OssGateway,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desk = with_a_model(platform, gateway, Model())
    (customer,) = await customers_in_an_outage(simulator, 1)
    intake = await desk.contact(DOWN, authenticated_customer_id=customer)
    stop = asyncio.Event()
    really_read = desk.runtime.inbox.pending_contacts

    async def stopped_meanwhile(limit: int) -> list[UUID]:
        found = await really_read(limit)
        stop.set()  # the process is asked to stop while the query is out
        return found

    monkeypatch.setattr(desk.runtime.inbox, "pending_contacts", stopped_meanwhile)

    await asyncio.wait_for(desk.worker.run_forever(stop), timeout=5)

    assert await desk.replies(intake.contact_id) == []


async def test_what_was_read_from_the_inbox_a_while_ago_is_read_again_before_it_is_acted_on(
    platform: Platform,
    gateway: OssGateway,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every slot busy, due contacts wait in this worker's hands. By the time a slot
    frees, another worker may have run one of them, or put it off after a failure: the list is
    as old as the turn that held the slot, and is not gone by."""
    desk = with_a_model(platform, gateway, Model(slow_seconds=0.6), concurrency=1)
    slow_one, taken_elsewhere, third = await customers_in_an_outage(simulator, 3)
    first = await desk.contact(DOWN_SLOWLY, authenticated_customer_id=slow_one)
    second = await desk.contact(DOWN, authenticated_customer_id=taken_elsewhere)
    last = await desk.contact(DOWN, authenticated_customer_id=third)
    really_process = desk.worker.process_contact
    started_here: list[UUID] = []

    async def noted(contact_id: UUID) -> bool:
        started_here.append(contact_id)
        return await really_process(contact_id)

    monkeypatch.setattr(desk.worker, "process_contact", noted)

    async with looping(desk):
        await asyncio.sleep(0.2)  # the slow turn holds the only slot; two contacts wait
        assert await really_process(second.contact_id) is True  # another worker runs one
        everyone = await answered(
            desk, [first.contact_id, second.contact_id, last.contact_id], within=10
        )

    assert everyone == [True] * 3
    assert started_here == [first.contact_id, last.contact_id]


async def test_the_page_asked_of_the_inbox_has_an_upper_bound(
    platform: Platform,
    gateway: OssGateway,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page grows by the contacts this worker knows to be in other hands, so that it can
    look past them. However many those are, one query never asks for more than the bound."""
    monkeypatch.setattr(worker_module, "MAX_PAGE", 12)
    desk = with_a_model(platform, gateway, Model(), concurrency=2)
    customers = await customers_in_an_outage(simulator, 9)
    elsewhere = {
        (await desk.contact(DOWN, authenticated_customer_id=customer)).contact_id
        for customer in customers[:8]
    }
    ours = await desk.contact(DOWN, authenticated_customer_id=customers[8])
    really_process = desk.worker.process_contact
    really_read = desk.runtime.inbox.pending_contacts
    asked_for: list[int] = []

    async def held_elsewhere(contact_id: UUID) -> bool:
        if contact_id in elsewhere:
            return False
        return await really_process(contact_id)

    async def counted(limit: int) -> list[UUID]:
        asked_for.append(limit)
        return await really_read(limit)

    monkeypatch.setattr(desk.worker, "process_contact", held_elsewhere)
    monkeypatch.setattr(desk.runtime.inbox, "pending_contacts", counted)

    async with looping(desk):
        reached = await answered(desk, [ours.contact_id], within=5)

    assert reached == [True]
    assert max(asked_for) == 12  # it grew to the bound and no further
