"""Action ledger: identity, frozen requests, single claim, exactly-once outcome."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from skylink_care.db.actions import ActionRepository, ActionStatus, NewAction
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.inbox import InboxRepository
from skylink_care.domain.models import Channel


def incident(key: str = "incident:ep-1", **overrides: object) -> NewAction:
    base: dict[str, object] = {
        "action_key": key,
        "action_type": "INCIDENT_OPEN",
        "requested_by": "agent:diagnostics",
        "request_body": {"node_id": "BLR-OLT-0012", "service_type": "broadband", "outage_id": "O1"},
        "correlation_id": "corr-1",
    }
    return NewAction(**{**base, **overrides})  # type: ignore[arg-type]


async def make_contact(contacts: ContactRepository) -> UUID:
    result = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="hello",
            correlation_id=uuid4().hex,
            received_at=datetime(2026, 11, 15, 4, 30, tzinfo=UTC),
        )
    )
    return result.contact_id


async def test_proposing_the_same_effect_twice_returns_the_first_record(
    actions: ActionRepository,
) -> None:
    first = await actions.propose(incident())
    second = await actions.propose(incident())

    assert (first.created, second.created) == (True, False)
    assert not second.conflicting
    assert second.action.action_id == first.action.action_id
    assert first.action.status is ActionStatus.READY


async def test_same_key_with_a_different_request_is_flagged_and_not_overwritten(
    actions: ActionRepository,
) -> None:
    original = await actions.propose(incident())

    clash = await actions.propose(incident(request_body={"node_id": "BLR-OLT-0099"}))

    assert clash.conflicting
    assert not clash.created
    stored = await actions.get(original.action.action_id)
    assert stored is not None
    assert stored.request_body == original.action.request_body
    assert stored.request_hash == original.action.request_hash


async def test_fifty_simultaneous_proposals_of_one_effect_create_one_action(
    actions: ActionRepository, db: Database
) -> None:
    results = await asyncio.gather(*(actions.propose(incident()) for _ in range(50)))

    assert sum(1 for result in results if result.created) == 1
    assert len({result.action.action_id for result in results}) == 1


async def test_action_needing_approval_cannot_be_claimed(actions: ActionRepository) -> None:
    proposal = await actions.propose(incident("credit:S1:O1", approval_level="supervisor"))

    claimed = await actions.claim_runnable(limit=10, visibility_seconds=30)

    assert proposal.action.status is ActionStatus.PENDING_APPROVAL
    assert claimed == []


async def test_most_urgent_lane_is_claimed_first(actions: ActionRepository) -> None:
    await actions.propose(incident("notify:C1:O1", lane=9))
    await actions.propose(incident("incident:ep-1", lane=3))
    await actions.propose(incident("complaint:SKL-1", lane=1))

    claimed = await actions.claim_runnable(limit=2, visibility_seconds=30)

    assert [action.action_key for action in claimed] == ["complaint:SKL-1", "incident:ep-1"]
    assert all(action.status is ActionStatus.IN_FLIGHT for action in claimed)
    assert all(action.attempts == 1 for action in claimed)


async def test_claimed_action_is_hidden_until_its_claim_lapses(actions: ActionRepository) -> None:
    await actions.propose(incident())

    first = await actions.claim_runnable(limit=10, visibility_seconds=0.3)
    hidden = await actions.claim_runnable(limit=10, visibility_seconds=0.3)
    await asyncio.sleep(0.4)
    replay = await actions.claim_runnable(limit=10, visibility_seconds=30)

    assert len(first) == 1
    assert hidden == []
    assert [action.action_id for action in replay] == [first[0].action_id]
    assert replay[0].attempts == 2
    assert replay[0].request_hash == first[0].request_hash


async def test_two_executors_never_claim_the_same_action(actions: ActionRepository) -> None:
    for n in range(30):
        await actions.propose(incident(f"incident:ep-{n}"))

    batches = await asyncio.gather(
        *(actions.claim_runnable(limit=10, visibility_seconds=30) for _ in range(6))
    )

    claimed = [action.action_id for batch in batches for action in batch]
    assert len(claimed) == 30
    assert len(set(claimed)) == 30


async def test_dependent_action_waits_for_its_prerequisite(actions: ActionRepository) -> None:
    opened = await actions.propose(incident("incident:ep-1"))
    await actions.propose(
        incident(
            "incident-link:ep-1:SKL-1",
            action_type="INCIDENT_LINK_COMPLAINT",
            depends_on=opened.action.action_id,
        )
    )

    first = await actions.claim_runnable(limit=10, visibility_seconds=30)
    await actions.finish(opened.action.action_id, ActionStatus.SUCCEEDED, external_ref="INC-7")
    second = await actions.claim_runnable(limit=10, visibility_seconds=30)

    assert [action.action_key for action in first] == ["incident:ep-1"]
    assert [action.action_key for action in second] == ["incident-link:ep-1:SKL-1"]


async def test_outcome_is_recorded_exactly_once(actions: ActionRepository) -> None:
    proposal = await actions.propose(incident())
    await actions.claim_runnable(limit=1, visibility_seconds=30)

    first = await actions.finish(
        proposal.action.action_id,
        ActionStatus.SUCCEEDED,
        external_ref="INC-7",
        response_body={"incident_id": "INC-7"},
    )
    late_duplicate = await actions.finish(
        proposal.action.action_id, ActionStatus.FAILED, error="late timeout"
    )

    stored = await actions.get(proposal.action.action_id)
    assert (first, late_duplicate) == (True, False)
    assert stored is not None
    assert stored.status is ActionStatus.SUCCEEDED
    assert stored.external_ref == "INC-7"
    assert stored.response_body == {"incident_id": "INC-7"}


async def test_finishing_can_wake_the_contact_exactly_once(
    actions: ActionRepository, contacts: ContactRepository, inbox: InboxRepository
) -> None:
    contact_id = await make_contact(contacts)
    proposal = await actions.propose(incident("credit:S1:O1", contact_id=contact_id))
    await actions.claim_runnable(limit=10, visibility_seconds=30)

    await actions.finish(proposal.action.action_id, ActionStatus.SUCCEEDED, wake_contact=True)
    await actions.finish(proposal.action.action_id, ActionStatus.SUCCEEDED, wake_contact=True)

    wakeups = [
        event
        for event in await inbox.fetch_after(contact_id, 0)
        if event.event_type == "action.finished"
    ]
    assert len(wakeups) == 1
    assert wakeups[0].payload == {
        "action_id": str(proposal.action.action_id),
        "status": "SUCCEEDED",
    }


async def test_non_terminal_status_cannot_be_recorded_as_an_outcome(
    actions: ActionRepository,
) -> None:
    proposal = await actions.propose(incident())

    with pytest.raises(ValueError, match="terminal"):
        await actions.finish(proposal.action.action_id, ActionStatus.READY)


async def test_deferred_action_is_retried_later_with_the_same_request(
    actions: ActionRepository,
) -> None:
    proposal = await actions.propose(incident())
    await actions.claim_runnable(limit=1, visibility_seconds=30)

    deferred = await actions.defer(
        proposal.action.action_id, delay_seconds=0.2, error="504 after write"
    )
    assert deferred
    too_early = await actions.claim_runnable(limit=1, visibility_seconds=30)
    await asyncio.sleep(0.3)
    retried = await actions.claim_runnable(limit=1, visibility_seconds=30)

    assert too_early == []
    assert retried[0].last_error == "504 after write"
    assert retried[0].request_body == proposal.action.request_body


async def test_action_is_blocked_when_its_prerequisite_fails(actions: ActionRepository) -> None:
    opened = await actions.propose(incident("incident:ep-1"))
    link = await actions.propose(
        incident("incident-link:ep-1:SKL-1", depends_on=opened.action.action_id)
    )
    await actions.finish(opened.action.action_id, ActionStatus.FAILED, error="422")

    blocked = await actions.block_orphans()

    stored = await actions.get(link.action.action_id)
    assert [action.action_key for action in blocked] == ["incident-link:ep-1:SKL-1"]
    assert stored is not None
    assert stored.status is ActionStatus.BLOCKED
