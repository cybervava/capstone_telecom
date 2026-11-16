"""A message that claims more about the network than its turn can prove is not sent.

The routing only reaches a template with the evidence it needs. These tests put a defect in on
purpose, to see the second lock hold when the first one fails.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from skylink_care.agents import replies
from skylink_care.agents.replies import Reply
from skylink_care.db.actions import CREDIT_APPLY
from skylink_care.orchestrator import routing
from tests.e2e.test_edges_and_degraded_mode import ALL_CLEAR, customer_with, handoff_reasons
from tests.e2e.test_feed_and_restoration import told_about_outage
from tests.support.platform import Platform
from tests.support.stack import Simulator


async def trail_of(platform: Platform, contact_id: Any) -> list[tuple[str, str]]:
    trail = await platform.runtime.audit.for_contact(contact_id)
    return [(record.step, record.outcome) for record in trail]


async def test_all_clear_the_evidence_does_not_back_is_stopped_and_a_person_takes_over(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect: with the status unverifiable, the workflow reaches for the all-clear."""
    customer = await customer_with(simulator)
    await simulator.start_outage(customer["true_node_id"])
    await simulator.fault("network", "down")

    def all_clear_by_mistake(complaint_ref: str, *_: Any, **__: Any) -> Reply:
        return replies.no_outage_reported(complaint_ref, evidence_at=platform.runtime.now())

    monkeypatch.setattr(replies, "network_unknown", all_clear_by_mistake)

    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    sent = await platform.replies(intake.contact_id)
    assert len(sent) == 1
    assert intake.complaint_ref in sent[0]
    assert "A member of our team will take over" in sent[0]
    assert ALL_CLEAR.search(sent[0]) is None
    assert await handoff_reasons(platform) == ["MESSAGE_BLOCKED"]
    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.status == "HANDOFF"
    # Stopped once and recorded; not tried again and again against the same evidence.
    blocked = [
        entry
        for entry in await trail_of(platform, intake.contact_id)
        if entry[0] == "message.blocked"
    ]
    assert blocked == [("message.blocked", "CLEAR_NOT_PROVEN")]
    (handoff,) = await platform.runtime.handoffs.list_open()
    assert "no_outage_reported.v1" in handoff.summary
    assert (await simulator.state())["tables"]["appointments"] == 0


async def test_outage_announced_on_a_node_proven_clear_is_stopped_too(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opposite defect: a healthy node, and the workflow reaches for the outage reply."""
    customer = await customer_with(simulator)
    real_outage_active = replies.outage_active

    def outage_by_mistake(complaint_ref: str, **_: Any) -> Reply:
        from datetime import timedelta

        from skylink_care.domain.models import OutageInfo

        made_up = OutageInfo(
            outage_id="OUT-NOT-REAL",
            version=1,
            cause="FIBRE_CUT",
            planned=False,
            started_at=platform.runtime.now() - timedelta(minutes=5),
            eta=None,
            eta_source=None,
            resolved_at=None,
        )
        return real_outage_active(complaint_ref, made_up, evidence_at=platform.runtime.now())

    monkeypatch.setattr(replies, "line_ok", outage_by_mistake)
    monkeypatch.setattr(replies, "guided_steps", outage_by_mistake)

    intake = await platform.contact(
        "my internet is very slow", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    sent = await platform.replies(intake.contact_id)
    assert all("confirmed outage" not in message for message in sent)
    assert await handoff_reasons(platform) == ["MESSAGE_BLOCKED"]
    assert ("message.blocked", "OUTAGE_NOT_PROVEN") in await trail_of(platform, intake.contact_id)


async def test_service_restored_on_a_status_nobody_can_read_is_stopped_before_a_credit_is_offered(
    platform: Platform, simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The curveball, with a defect put in: the Network API is down, the feed has gone quiet,
    and the routing sends the customer to "service restored" all the same. The step is stopped
    before it does anything: no message, and no credit proposed on the strength of it."""
    _, _, intake = await told_about_outage(platform, simulator)
    await simulator.fault("network", "down")
    platform.advance(platform.runtime.settings.feed_max_age_seconds + 30)  # the feed is stale

    def restored_by_mistake(*_: Any, **__: Any) -> routing.Decision:
        return routing.Decision(routing.Route.RESTORED)

    monkeypatch.setattr(routing, "_after_diagnosis", restored_by_mistake)
    await platform.say(intake.contact_id, "is it working again?")
    await platform.settle()

    sent = await platform.replies(intake.contact_id)
    assert not any("should be working again" in message for message in sent)
    assert "MESSAGE_BLOCKED" in await handoff_reasons(platform)
    trail = await trail_of(platform, intake.contact_id)
    assert ("message.blocked", "RESTORED_NOT_PROVEN") in trail
    actions = await platform.runtime.actions.list_for_contact(intake.contact_id)
    assert all(action.action_type != CREDIT_APPLY for action in actions)


async def test_the_contact_keeps_when_the_network_was_last_looked_at(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with(simulator)

    intake = await platform.contact(
        "my internet is very slow", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.network_state == "CONFIRMED_CLEAR"
    # The time of the status the decision rests on, as the gateway stamped it.
    assert contact.network_checked_at is not None
    assert abs(platform.runtime.now() - contact.network_checked_at) < timedelta(seconds=2)


async def test_messages_the_evidence_backs_are_sent_as_before(
    platform: Platform, simulator: Simulator
) -> None:
    customer = await customer_with(simulator)
    await simulator.start_outage(customer["true_node_id"])

    intake = await platform.contact(
        "internet down", authenticated_customer_id=customer["customer_id"]
    )
    await platform.settle()

    (reply,) = await platform.replies(intake.contact_id)
    assert "confirmed outage" in reply
    assert await handoff_reasons(platform) == []
    assert all(step != "message.blocked" for step, _ in await trail_of(platform, intake.contact_id))
