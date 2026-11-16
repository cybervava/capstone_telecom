"""The load test's reading of the platform's own records, checked against small journeys.

A load run's verdict rests on these queries. Here they read what the platform recorded for a
handful of customers whose story is known, so a wrong count is a failing test and not a wrong
report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loadtest.facts import (
    after_the_wave,
    database_time,
    read_facts,
    told_of_the_restoration,
    waiting_on_an_outage,
)

from tests.e2e.test_feed_and_restoration import waiting_on_one_outage
from tests.support.platform import Platform
from tests.support.stack import Simulator

# The database is empty before each test: every contact in it belongs to the run.
START, END = datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)


async def customer_on_another_node(
    platform: Platform, simulator: Simulator, node_id: str
) -> dict[str, Any]:
    """A customer with a healthy line on a broadband node that the outage does not touch."""
    async with platform.runtime.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT node_id FROM nodes WHERE node_type = 'OLT' AND status = 'ACTIVE'"
            " AND node_id <> %s ORDER BY node_id",
            (node_id,),
        )
        others = [row["node_id"] for row in await cursor.fetchall()]
    for other in others:
        found = await simulator.find_customers(
            1, service_type="broadband", mapping_defect="none", status="active", node_id=other
        )
        if found:
            await simulator.line_fault(found[0]["service_id"], fault=False)
            return found[0]
    raise LookupError("the data pack has no customer on another node")


async def test_the_wave_after_an_outage_is_accounted_for_in_full(
    platform: Platform, simulator: Simulator
) -> None:
    db = platform.runtime.db
    outage, people, intakes = await waiting_on_one_outage(platform, simulator, 5)
    elsewhere = await customer_on_another_node(platform, simulator, people[0]["true_node_id"])
    other = await platform.contact(
        "my wifi is slow", authenticated_customer_id=elsewhere["customer_id"]
    )
    await platform.settle()
    await simulator.line_fault(people[-1]["service_id"], fault=True)  # a fault of their own

    waiting = await waiting_on_an_outage(db, START, END)
    since = await database_time(db)
    platform.advance(1800)
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    for intake in intakes:
        await platform.worker.process_contact(intake.contact_id)
    await platform.settle()

    assert sorted(waiting) == sorted(intake.contact_id for intake in intakes)
    assert other.contact_id not in waiting
    assert await told_of_the_restoration(db, waiting) == 5
    assert await after_the_wave(db, waiting, since) == {
        "not_told_stand_as": {},
        "told_unverified_meanwhile": 0,
        "lines_tested": 5,
        "lines_failing": 1,
        "suspected_tickets": 0,
    }
    facts = await read_facts(db, START, END, [outage["outage_id"]])
    assert (facts["contacts"], facts["replied"]) == (6, 6)
    assert facts["first_reply"]["outage_active.v1"] == 5


async def test_a_node_suspected_again_after_its_outage_shows_in_the_account(
    platform: Platform, simulator: Simulator
) -> None:
    """Every line still fails after the NOC's resolution. Two customers were told that it is
    over before the third failing line made the node suspect; the others stand where they
    were left, and the ticket raised for the node is counted."""
    db = platform.runtime.db
    outage, people, intakes = await waiting_on_one_outage(platform, simulator, 6)
    for person in people:
        await simulator.line_fault(person["service_id"], fault=True)

    waiting = await waiting_on_an_outage(db, START, END)
    since = await database_time(db)
    platform.advance(1800)
    await simulator.resolve_outage(outage["outage_id"])
    await platform.feed()
    for intake in intakes:
        await platform.worker.process_contact(intake.contact_id)
    await platform.settle()

    assert await told_of_the_restoration(db, waiting) == 2
    assert await after_the_wave(db, waiting, since) == {
        "not_told_stand_as": {"network_unknown.v1": 4},
        "told_unverified_meanwhile": 4,
        "lines_tested": 6,
        "lines_failing": 6,
        "suspected_tickets": 1,
    }


async def test_nobody_waiting_is_an_empty_account_and_not_an_error(platform: Platform) -> None:
    db = platform.runtime.db

    waiting = await waiting_on_an_outage(db, START, END)

    assert waiting == []
    assert await told_of_the_restoration(db, waiting) == 0
    assert await after_the_wave(db, waiting, await database_time(db)) == {
        "not_told_stand_as": {},
        "told_unverified_meanwhile": 0,
        "lines_tested": 0,
        "lines_failing": 0,
        "suspected_tickets": 0,
    }
