"""Contacts about one node that are diagnosed at the same moment share one status read."""

from __future__ import annotations

from typing import Any

import pytest

from skylink_care.adapters.shared import SharedNodeStatus
from skylink_care.db.actions import VISIT_BOOK
from skylink_care.executor.visits import VisitBooker
from tests.e2e.test_feed_and_restoration import told_about_outage
from tests.support.platform import Platform
from tests.support.stack import Simulator

ENOUGH = 12


async def customers_of_one_node(simulator: Simulator) -> list[dict[str, Any]]:
    """Broadband customers with a sound mapping who are all served by the same node."""
    filters = {"service_type": "broadband", "mapping_defect": "none", "status": "active"}
    for candidate in await simulator.find_customers(50, **filters):
        together = await simulator.find_customers(50, node_id=candidate["true_node_id"], **filters)
        if len(together) >= ENOUGH:
            return together
    raise LookupError(f"no node of the data pack serves {ENOUGH} such customers")


async def status_reads(simulator: Simulator) -> int:
    requests = (await simulator.state())["requests"]
    return sum(requests.get("network", {}).values())


async def test_contacts_about_one_node_in_outage_share_its_status_reads(
    platform: Platform, simulator: Simulator
) -> None:
    customers = await customers_of_one_node(simulator)
    await simulator.start_outage(customers[0]["true_node_id"])
    before = await status_reads(simulator)

    intakes = [
        await platform.contact("internet down", authenticated_customer_id=one["customer_id"])
        for one in customers
    ]
    await platform.settle(rounds=40)

    for intake in intakes:
        (reply,) = await platform.replies(intake.contact_id)
        assert "outage" in reply.lower()
        assert intake.complaint_ref in reply
    reads = await status_reads(simulator) - before
    # One read for each contact without sharing. The worker takes several contacts at a time,
    # and those that reach the status read together are answered by one read.
    assert 1 <= reads <= len(customers) // 2, (reads, len(customers))


async def test_turn_that_follows_news_from_the_feed_reads_the_status_for_itself(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contact woken by "outage resolved" must see the node as it is after that news, so it
    does not join a status read that was sent before it. The customer's own message may."""
    asked: list[bool] = []
    run = platform.runtime.diagnostics.run

    async def watched(subject: Any, **options: Any) -> Any:
        asked.append(bool(options.get("own_status_read")))
        return await run(subject, **options)

    monkeypatch.setattr(platform.runtime.diagnostics, "run", watched)
    _, outage, intake = await told_about_outage(platform, simulator)
    by_the_customer = list(asked)

    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    await platform.settle()

    assert by_the_customer == [False]
    assert asked[len(by_the_customer) :] == [True]
    assert any("restored" in reply.lower() for reply in await platform.replies(intake.contact_id))


async def test_the_executor_reads_the_status_for_itself(platform: Platform) -> None:
    """Sharing is for diagnoses. Whoever is about to book a visit must look for itself.

    A check of the wiring, so it looks inside: the orchestrator's agent reads through the
    shared source, the visit booker's agent does not.
    """
    assert isinstance(platform.runtime.diagnostics._shared, SharedNodeStatus)
    booker = platform.executor._handlers[VISIT_BOOK]
    assert isinstance(booker, VisitBooker)
    assert booker._nodes._shared is None
