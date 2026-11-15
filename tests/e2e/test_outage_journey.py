"""First complete path: intake → reference → customer and node → live outage → reply → incident.

Runs the real workflow graph, orchestrator worker and action executor against PostgreSQL and the
mock OSS/BSS gateway (in process). Nothing here is stubbed except the language model, which uses
the offline rules provider.
"""

from __future__ import annotations

from skylink_care.db.actions import ActionStatus
from skylink_care.domain.reference import is_valid_reference
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def broadband_customer(simulator: Simulator, **filters: object) -> dict[str, object]:
    return await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active", **filters
    )


async def test_customer_on_a_node_with_a_declared_outage_is_told_and_linked_to_one_incident(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    outage = await simulator.start_outage(str(customer["true_node_id"]), eta_minutes=120)

    intake = await platform.contact(
        "My internet has been down since morning, router shows a red light",
        authenticated_customer_id=str(customer["customer_id"]),
    )
    await platform.settle()

    # The reference existed before any processing started.
    assert is_valid_reference(intake.complaint_ref)

    replies = await platform.replies(intake.contact_id)
    assert len(replies) == 1
    assert outage["outage_id"] in replies[0]
    assert intake.complaint_ref in replies[0]
    assert "expected back by" in replies[0]
    assert "don't need to restart or reset your router" in replies[0]

    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_NETWORK"
    assert contact.network_state == "ACTIVE"
    assert contact.customer_id == customer["customer_id"]
    assert contact.node_id == customer["true_node_id"]
    assert contact.outage_id == outage["outage_id"]
    assert contact.episode_id is not None

    # Enterprise effects, performed by the executor only: one complaint registered, one incident
    # opened for the node, the complaint linked to it.
    state = (await simulator.state())["tables"]
    assert state["complaints"] == 1
    assert state["incidents"] == 1
    assert state["incident_complaints"] == 1
    actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
    assert {action.action_type: action.status for action in actions} == {
        "COMPLAINT_REGISTER": ActionStatus.SUCCEEDED,
        "INCIDENT_LINK_COMPLAINT": ActionStatus.SUCCEEDED,
    }
    # No device flow and no dispatch were started.
    assert state["appointments"] == 0
    assert state["credits"] == 0


async def test_every_step_of_the_journey_is_in_the_audit_trail(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    await simulator.start_outage(str(customer["true_node_id"]))

    intake = await platform.contact(
        "wifi not working", authenticated_customer_id=str(customer["customer_id"])
    )
    await platform.settle()

    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    steps = [(record.actor, record.step, record.outcome) for record in trail]
    assert ("agent:triage", "tool:crm.customer", "OK") in steps
    assert ("agent:triage", "triage.read_contact", "OK") in steps
    assert ("agent:diagnostics", "tool:network.node_status", "OK") in steps
    assert ("agent:diagnostics", "diagnostics.decide", "ACTIVE") in steps
    assert ("system:executor", "action:COMPLAINT_REGISTER", "SUCCEEDED") in steps
    assert ("system:executor", "action:INCIDENT_LINK_COMPLAINT", "SUCCEEDED") in steps
    reading = next(record for record in trail if record.step == "triage.read_contact")
    assert reading.model == "rules-v1"
    assert reading.prompt_version is not None
    assert reading.prompt_version.startswith("triage@")
    assert all(record.correlation_id for record in trail)


async def test_fifty_customers_on_one_node_get_fifty_references_and_share_one_incident(
    platform: Platform, simulator: Simulator
) -> None:
    anchor = await broadband_customer(simulator)
    node_id = str(anchor["true_node_id"])
    neighbours = await simulator.find_customers(
        50, service_type="broadband", mapping_defect="none", status="active", node_id=node_id
    )
    assert len(neighbours) >= 20, "the data pack should give a node with enough customers"
    await simulator.start_outage(node_id)

    intakes = [
        await platform.contact(
            "internet down", authenticated_customer_id=str(neighbour["customer_id"])
        )
        for neighbour in neighbours
    ]
    await platform.settle(rounds=12)

    assert len({intake.complaint_ref for intake in intakes}) == len(neighbours)
    state = (await simulator.state())["tables"]
    assert state["incidents"] == 1
    assert state["incident_complaints"] == len(neighbours)
    assert state["complaints"] == len(neighbours)
    incident_actions = await platform.runtime.actions.get_by_key(
        f"incident:{(await platform.runtime.contacts.get(intakes[0].contact_id)).episode_id}"  # type: ignore[union-attr]
    )
    assert incident_actions is not None
    assert incident_actions.status is ActionStatus.SUCCEEDED


async def test_a_retried_request_returns_the_same_reference_and_no_second_effect(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await broadband_customer(simulator)
    await simulator.start_outage(str(customer["true_node_id"]))
    request = {
        "client_request_id": "retry-me-0001",
        "authenticated_customer_id": str(customer["customer_id"]),
    }

    first = await platform.contact("internet down", **request)
    await platform.settle()
    again = await platform.contact("internet down", **request)
    await platform.settle()

    assert again.replayed
    assert again.complaint_ref == first.complaint_ref
    assert len(await platform.replies(first.contact_id)) == 1
    state = (await simulator.state())["tables"]
    assert (state["complaints"], state["incidents"], state["incident_complaints"]) == (1, 1, 1)
