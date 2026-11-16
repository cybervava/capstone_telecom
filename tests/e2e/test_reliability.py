"""What happens when things keep failing: nothing is lost silently and nobody is left unaware."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from skylink_care.adapters.result import Err, ErrorKind
from skylink_care.db.actions import ActionStatus
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def outage_customer(simulator: Simulator) -> dict[str, Any]:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    await simulator.start_outage(customer["true_node_id"])
    return customer


async def open_handoffs(platform: Platform) -> dict[str, str]:
    return {item.reason: item.summary for item in await platform.runtime.handoffs.list_open()}


# --- orchestrator --------------------------------------------------------------------------------


async def test_a_contact_that_keeps_failing_is_given_to_a_person_and_the_customer_is_told(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await outage_customer(simulator)

    async def always_broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug in a node")

    monkeypatch.setattr(platform.runtime.diagnostics, "run", always_broken)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )

    for _ in range(40):  # five attempts with a short back-off between them
        await platform.worker.run_once()
        contact = await platform.runtime.contacts.get(intake.contact_id)
        assert contact is not None
        if contact.status == "HANDOFF":
            break
        await asyncio.sleep(0.1)

    assert contact.status == "HANDOFF"
    handoffs = await open_handoffs(platform)
    assert "WORKFLOW_ERROR" in handoffs
    assert "failed 5 times" in handoffs["WORKFLOW_ERROR"]
    replies = await platform.replies(intake.contact_id)
    assert len(replies) == 1
    assert intake.complaint_ref in replies[0]
    assert "A member of our team will take over" in replies[0]
    assert await platform.runtime.inbox.count_dead() == 1
    assert await platform.runtime.inbox.pending_contacts(10) == []


async def test_a_failed_run_waits_for_its_back_off_instead_of_burning_its_attempts(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await outage_customer(simulator)
    calls = {"n": 0}
    real_run = platform.runtime.diagnostics.run

    async def fails_twice(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:  # both attempts of the node in the first run
            raise ConnectionError("network blip")
        return await real_run(*args, **kwargs)

    monkeypatch.setattr(platform.runtime.diagnostics, "run", fails_twice)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )

    first = await platform.worker.run_once()
    immediately_after = await platform.worker.run_once()
    await platform.settle()

    assert (first, immediately_after) == (0, 0)  # failed, then not yet due
    assert len(await platform.replies(intake.contact_id)) == 1
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "AWAITING_NETWORK"


async def test_a_message_after_handoff_is_flagged_to_the_person_who_owns_the_contact(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    intake = await platform.contact(
        "I want to speak to a human agent about my internet",
        authenticated_customer_id=customer["customer_id"],
    )
    await platform.settle()

    await platform.say(intake.contact_id, "hello? is anyone there?")
    await platform.settle()

    handoffs = await open_handoffs(platform)
    assert set(handoffs) == {"CUSTOMER_REQUESTED_HUMAN", "CUSTOMER_FOLLOW_UP"}
    assert await platform.runtime.inbox.pending_contacts(10) == []
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    noted = [record for record in trail if record.step == "events_after_workflow_end"]
    assert len(noted) == 1
    assert noted[0].outputs == {"events": ["customer.message"]}


# --- executor ------------------------------------------------------------------------------------


async def test_an_answer_that_cannot_be_read_is_replayed_not_recorded_as_failed(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write may have landed. Marking it FAILED would block its dependants for good."""
    customer = await outage_customer(simulator)
    incident = platform.executor._clients.incident
    real_open = incident.open
    calls = {"n": 0}

    async def unreadable_once(body: dict[str, Any], **kwargs: Any) -> Any:
        calls["n"] += 1
        result = await real_open(body, **kwargs)  # the write does land
        if calls["n"] == 1:
            return Err(ErrorKind.PROTOCOL, "response did not validate", status_code=201)
        return result

    monkeypatch.setattr(incident, "open", unreadable_once)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    tables = (await simulator.state())["tables"]
    assert (tables["incidents"], tables["incident_complaints"]) == (1, 1)
    actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
    assert all(action.status is ActionStatus.SUCCEEDED for action in actions)
    assert calls["n"] == 2
    assert await open_handoffs(platform) == {}


@pytest.mark.parametrize("kind", [ErrorKind.UNAUTHORIZED, ErrorKind.FORBIDDEN, ErrorKind.CONFLICT])
async def test_auth_failures_and_unexplained_conflicts_are_retried_not_failed(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch, kind: ErrorKind
) -> None:
    """A rotated key must delay writes, not permanently fail every one of them."""
    customer = await outage_customer(simulator)
    incident = platform.executor._clients.incident
    real = incident.register_complaint
    calls = {"n": 0}

    async def rejected_twice(body: dict[str, Any], **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:
            return Err(kind, "not now", status_code=401)
        return await real(body, **kwargs)

    monkeypatch.setattr(incident, "register_complaint", rejected_twice)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    registration = await platform.runtime.actions.get_by_key(f"complaint:{intake.complaint_ref}")
    assert registration is not None
    assert registration.status is ActionStatus.SUCCEEDED
    assert registration.attempts == 3


async def test_a_definite_rejection_fails_the_action_and_tells_a_person(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await outage_customer(simulator)
    incident = platform.executor._clients.incident

    async def rejected(body: dict[str, Any], **kwargs: Any) -> Any:
        return Err(ErrorKind.INVALID, "bad field", status_code=422, code="INVALID_BODY")

    monkeypatch.setattr(incident, "register_complaint", rejected)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    registration = await platform.runtime.actions.get_by_key(f"complaint:{intake.complaint_ref}")
    assert registration is not None
    assert registration.status is ActionStatus.FAILED
    assert registration.attempts == 1
    handoffs = await open_handoffs(platform)
    assert "ACTION_FAILED" in handoffs
    assert "COMPLAINT_REGISTER" in handoffs["ACTION_FAILED"]


async def test_dependants_of_a_failed_write_are_blocked_and_escalated(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await outage_customer(simulator)
    incident = platform.executor._clients.incident

    async def rejected(body: dict[str, Any], **kwargs: Any) -> Any:
        return Err(ErrorKind.INVALID, "bad field", status_code=422, code="INVALID_BODY")

    monkeypatch.setattr(incident, "open", rejected)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    actions = {
        a.action_type: a for a in await platform.runtime.actions.list_for_contact(intake.contact_id)
    }
    assert actions["INCIDENT_LINK_COMPLAINT"].status is ActionStatus.BLOCKED
    assert actions["COMPLAINT_REGISTER"].status is ActionStatus.SUCCEEDED
    assert "ACTION_FAILED" in await open_handoffs(platform)
    # The customer was still told the truth about the outage.
    assert len(await platform.replies(intake.contact_id)) == 1


async def test_an_unexpected_error_in_a_handler_does_not_stop_the_executor(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await outage_customer(simulator)
    incident = platform.executor._clients.incident
    real = incident.register_complaint
    calls = {"n": 0}

    async def explodes_once(body: dict[str, Any], **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyError("a bug")
        return await real(body, **kwargs)

    monkeypatch.setattr(incident, "register_complaint", explodes_once)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
    assert all(action.status is ActionStatus.SUCCEEDED for action in actions)
    tables = (await simulator.state())["tables"]
    assert (tables["complaints"], tables["incidents"], tables["incident_complaints"]) == (1, 1, 1)


async def test_a_write_that_stays_unknown_alerts_a_person_and_later_gives_up(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = await outage_customer(simulator)
    await simulator.fault("incident", "down")
    monkeypatch.setattr(platform.executor, "_alert_after_attempts", 2)
    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.worker.run_once()

    for _ in range(4):
        await platform.executor.run_once()
        await asyncio.sleep(0.25)
    registration = await platform.runtime.actions.get_by_key(f"complaint:{intake.complaint_ref}")
    assert registration is not None
    assert registration.status is ActionStatus.IN_FLIGHT  # still being replayed
    assert "ACTION_DELAYED" in await open_handoffs(platform)

    monkeypatch.setattr(platform.executor, "_give_up_after", timedelta(seconds=0))
    for _ in range(6):
        await platform.executor.run_once()
        await asyncio.sleep(0.25)

    registration = await platform.runtime.actions.get_by_key(f"complaint:{intake.complaint_ref}")
    assert registration is not None
    assert registration.status is ActionStatus.FAILED
    assert registration.last_error is not None
    assert registration.last_error.startswith("GAVE_UP")
    assert "ACTION_FAILED" in await open_handoffs(platform)
    assert (await simulator.state())["tables"]["complaints"] == 0


async def test_the_executor_loop_survives_an_error_and_stops_on_request(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def flaky_run_once() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database restarted")
        return 0

    monkeypatch.setattr(platform.executor, "run_once", flaky_run_once)
    monkeypatch.setattr(platform.executor, "_poll_seconds", 0.01)
    stop = asyncio.Event()

    loop = asyncio.create_task(platform.executor.run_forever(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(loop, timeout=2)

    assert calls["n"] >= 3
