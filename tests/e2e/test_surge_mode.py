"""Surge mode in the workflow: while the orchestrator is behind, messages are read by the rules.

The whole platform runs here as in the other journeys, except that the language model is a
provider that is not the rules, so that it can be seen whether it was asked.
"""

from __future__ import annotations

import dataclasses

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from skylink_care.adapters.gateway import OssGateway
from skylink_care.db.inbox import InboxRepository
from skylink_care.llm.base import TriageReading, Usage
from skylink_care.llm.gateway import LOAD_SHED, LLMGateway
from skylink_care.llm.offline import OfflineProvider
from skylink_care.orchestrator.graph import build_graph
from skylink_care.orchestrator.worker import OrchestratorWorker
from skylink_care.runtime import build_orchestrator_runtime
from skylink_care.surge import SurgeGauge, SurgePolicy
from tests.support.platform import Platform
from tests.support.stack import Simulator

# With this few contacts waiting the orchestrator counts as behind (three, in these tests).
BEHIND_AT = 3


class Model:
    """A provider that is not the rules. It answers as the rules would and counts its calls."""

    name = "external"
    model = "external-1"

    def __init__(self) -> None:
        self.calls = 0
        self._rules = OfflineProvider()

    async def read_contact(
        self, masked_text: str, *, system_prompt: str
    ) -> tuple[TriageReading, Usage]:
        self.calls += 1
        return await self._rules.read_contact(masked_text, system_prompt=system_prompt)


def with_a_model(platform: Platform, gateway: OssGateway, model: Model) -> Platform:
    """The same platform with a language model configured, and a low bar for being behind.

    The gauge measures the real backlog when a test tells it to and holds that answer for the
    rest of the test: a renewal part-way through a slow run would find the queue shorter.
    """
    held = SurgePolicy(waiting_contacts=BEHIND_AT, refresh_seconds=3600, believed_seconds=3600)
    runtime = build_orchestrator_runtime(
        platform.runtime.settings,
        platform.runtime.db,
        gateway,
        llm=LLMGateway(model),
        now=platform.runtime.now,
        surge=SurgeGauge(InboxRepository(platform.runtime.db).backlog, held),
    )
    checkpointer = AsyncPostgresSaver(runtime.db.pool)
    worker = OrchestratorWorker(
        runtime, build_graph(runtime, checkpointer), checkpointer, retry_backoff_seconds=0.05
    )
    return dataclasses.replace(platform, runtime=runtime, worker=worker, checkpointer=checkpointer)


async def customers_on_one_node(simulator: Simulator, count: int) -> list[dict[str, object]]:
    first = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    return await simulator.find_customers(
        count,
        service_type="broadband",
        mapping_defect="none",
        status="active",
        node_id=first["true_node_id"],
    )


async def test_with_nothing_waiting_the_model_reads_the_message(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    model = Model()
    desk = with_a_model(platform, gateway, model)
    (customer,) = await customers_on_one_node(simulator, 1)

    intake = await desk.contact(
        "My internet has been down since morning",
        authenticated_customer_id=str(customer["customer_id"]),
    )
    await desk.runtime.surge.measure()
    await desk.settle()

    assert desk.runtime.surge.behind() is False
    assert model.calls == 1
    read = [
        record
        for record in await desk.runtime.audit.for_contact(intake.contact_id)
        if record.step == "triage.read_contact"
    ]
    assert [(record.outcome, record.model) for record in read] == [("OK", "external-1")]


async def test_while_the_orchestrator_is_behind_the_rules_read_and_every_customer_is_answered(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    model = Model()
    desk = with_a_model(platform, gateway, model)
    customers = await customers_on_one_node(simulator, BEHIND_AT)
    outage = await simulator.start_outage(str(customers[0]["true_node_id"]), eta_minutes=120)

    intakes = [
        await desk.contact(
            "My internet has been down since morning",
            authenticated_customer_id=str(customer["customer_id"]),
        )
        for customer in customers
    ]
    await desk.runtime.surge.measure()
    behind = desk.runtime.surge.behind()
    await desk.settle()

    assert behind is True
    assert model.calls == 0
    for intake in intakes:
        replies = await desk.replies(intake.contact_id)
        # What is said about the network does not change under load: the outage, proven.
        assert len(replies) == 1
        assert outage["outage_id"] in replies[0]
        read = [
            record
            for record in await desk.runtime.audit.for_contact(intake.contact_id)
            if record.step == "triage.read_contact"
        ]
        assert [(r.outcome, r.inputs["provider_error"]) for r in read] == [("DEGRADED", LOAD_SHED)]


async def test_what_the_rules_cannot_place_during_a_surge_goes_to_a_person(
    platform: Platform, gateway: OssGateway, simulator: Simulator
) -> None:
    """The model would have told a request that is out of scope from one that is only put
    unusually. The rules cannot, so nobody is refused on their word: a person reads it."""
    model = Model()
    desk = with_a_model(platform, gateway, model)
    customers = await customers_on_one_node(simulator, BEHIND_AT)

    intakes = [
        await desk.contact(
            "there is something I wanted to ask you about",
            authenticated_customer_id=str(customer["customer_id"]),
        )
        for customer in customers
    ]
    await desk.runtime.surge.measure()
    await desk.settle()

    assert model.calls == 0
    for intake in intakes:
        contact = await desk.runtime.contacts.get(intake.contact_id)
        assert contact is not None
        assert contact.status == "HANDOFF"
        handoffs = await desk.runtime.handoffs.list_open()
        assert any(
            handoff.contact_id == intake.contact_id and handoff.reason == "LOW_CONFIDENCE"
            for handoff in handoffs
        )
