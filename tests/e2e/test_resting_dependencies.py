"""A dependency that is down is rested, and nobody is told anything it could not prove.

The gateway of this module rests a dependency after three failures in a row, and counts the
rest by a clock the tests hold: it ends when a test says so, and not before.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from skylink_care.adapters.breaker import BreakerPolicy
from skylink_care.adapters.gateway import GatewayPolicy, OssGateway
from skylink_care.config import OssKeys
from skylink_care.db.actions import ActionStatus
from skylink_care.domain.reference import is_valid_reference
from skylink_care.orchestrator.worker import DEFAULT_CONCURRENCY
from tests.e2e.test_edges_and_degraded_mode import ALL_CLEAR, customer_with
from tests.support.platform import Platform
from tests.support.stack import MOCK_URL, Simulator

FAILURES, REST = 3, 10.0
CONTACTS = 30
# Calls already on their way when the circuit opens still arrive: at most what one pass of the
# worker has in flight, each with the one more try a read is allowed.
MOST_CALLS = FAILURES + 2 * DEFAULT_CONCURRENCY


class Seconds:
    """Seconds that pass only when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def seconds() -> Seconds:
    return Seconds()


@pytest.fixture
async def gateway(
    oss_transport: httpx.ASGITransport, oss_keys: OssKeys, seconds: Seconds
) -> AsyncIterator[OssGateway]:
    policy = GatewayPolicy(
        breaker=BreakerPolicy(failures=FAILURES, rest_seconds=REST),
        read_retries=1,
        retry_pause_seconds=0,
    )
    client = OssGateway(
        MOCK_URL, oss_keys, transport=oss_transport, policy=policy, monotonic=seconds
    )
    yield client
    await client.aclose()


async def calls_to(simulator: Simulator, dependency: str) -> int:
    requests = (await simulator.state())["requests"]
    return sum(requests.get(dependency, {}).values())


async def test_a_status_api_that_is_down_is_not_called_once_for_every_contact(
    platform: Platform, simulator: Simulator, gateway: OssGateway
) -> None:
    customers = await simulator.find_customers(
        CONTACTS, service_type="broadband", mapping_defect="none", status="active"
    )
    assert len(customers) == CONTACTS
    await simulator.fault("network", "down")

    intakes = [
        await platform.contact("internet down", authenticated_customer_id=one["customer_id"])
        for one in customers
    ]
    await platform.settle(rounds=40)

    for intake in intakes:
        assert is_valid_reference(intake.complaint_ref)
        (reply,) = await platform.replies(intake.contact_id)
        assert "can't verify the live network status" in reply
        assert ALL_CLEAR.search(reply) is None
        contact = await platform.runtime.contacts.get(intake.contact_id)
        assert contact is not None
        assert contact.network_state == "UNKNOWN"
    # A status read and a line test for each contact would have been 60 calls and more.
    assert await calls_to(simulator, "network") <= MOST_CALLS
    assert gateway.circuits.snapshot()["network"] == "open"
    tables = (await simulator.state())["tables"]
    assert tables["appointments"] == 0
    assert tables["complaints"] == CONTACTS  # every reference is still registered


async def test_an_account_lookup_that_was_not_attempted_is_not_an_account_that_was_not_found(
    platform: Platform, simulator: Simulator, gateway: OssGateway
) -> None:
    customers = await simulator.find_customers(
        CONTACTS, service_type="broadband", mapping_defect="none", status="active"
    )
    await simulator.fault("crm", "down")

    intakes = [
        await platform.contact("internet down", authenticated_customer_id=one["customer_id"])
        for one in customers
    ]
    await platform.settle(rounds=40)

    for intake in intakes:
        (reply,) = await platform.replies(intake.contact_id)
        assert intake.complaint_ref in reply
        assert "couldn't reach our account system" in reply
    reasons = [item.reason for item in await platform.runtime.handoffs.list_open(limit=100)]
    assert reasons == ["CRM_UNAVAILABLE"] * CONTACTS
    assert await calls_to(simulator, "crm") <= MOST_CALLS
    assert gateway.circuits.snapshot()["crm"] == "open"


async def put_off_unsent(platform: Platform, contact_id: Any) -> list[Any]:
    """The records of writes for this contact that were put off without being sent."""
    trail = await platform.runtime.audit.for_contact(contact_id)
    return [
        record
        for record in trail
        if record.outcome == "DEFERRED" and "CIRCUIT_OPEN" in str(record.outputs.get("error"))
    ]


async def test_a_write_to_a_rested_dependency_is_not_sent_and_lands_once_when_it_is_back(
    platform: Platform, simulator: Simulator, gateway: OssGateway, seconds: Seconds
) -> None:
    customer = await customer_with(simulator)
    await simulator.start_outage(customer["true_node_id"])
    await simulator.fault("incident", "down")

    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.worker.run_once()
    # The executor's writes fail until the incident API is rested, and are then put off
    # without being sent. Each comes back on its own back-off, so this takes a few passes.
    for _ in range(100):
        await platform.executor.run_once()
        if await put_off_unsent(platform, intake.contact_id):
            break
        await asyncio.sleep(0.05)
    unsent = await put_off_unsent(platform, intake.contact_id)
    sent_while_down = await calls_to(simulator, "incident")
    for _ in range(6):
        await asyncio.sleep(0.05)
        await platform.executor.run_once()
    sent_while_rested = await calls_to(simulator, "incident") - sent_while_down

    await simulator.fault("incident", "ok")
    seconds.now += REST
    # Only one call probes a rested dependency at a time: the others are put off once more.
    for _ in range(100):
        await platform.settle(rounds=40)
        actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
        if all(action.status is ActionStatus.SUCCEEDED for action in actions):
            break
        await asyncio.sleep(0.1)

    # A write that was not sent is recorded as that, not as one whose outcome is unknown.
    assert unsent
    assert sent_while_rested == 0
    assert all(action.status is ActionStatus.SUCCEEDED for action in actions)
    tables = (await simulator.state())["tables"]
    assert (tables["complaints"], tables["incidents"], tables["incident_complaints"]) == (1, 1, 1)
    assert gateway.circuits.snapshot()["incident"] == "closed"
