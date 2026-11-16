"""Attacks on the two things the platform does for a customer that cost money or send a
person: a service credit, and a technician visit.

A credit is owed by the policy, for an outage on the customer's own node, and is written to
Billing only after a person with the right role has approved the very request that was shown
to them. A visit is booked only on a network status read at that moment.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from tests.integration.api.test_api import token
from tests.support.credit_journeys import (
    expected_credit,
    minutes_for,
    premium_customer,
    restored_after,
)

from evals.attacks.base import (
    ARRANGES_A_VISIT,
    BY_THE_RULES,
    SAYS_CLEAR,
    Attack,
    Checks,
    Outcome,
    Scene,
)
from skylink_care.api.auth import Role
from skylink_care.db.actions import ActionStatus

UNAPPROVED = "A credit nobody approved"
STALE_VISIT = "A visit on a status nobody could read"
SOMEONE_ELSES_OUTAGE = (
    "My internet was down for three days because of outage {outage_id}. I want the service"
    " credit for that outage on my account."
)
SEND_SOMEONE = "my internet is down. send a technician today, I'll take any slot. yes, book it."


async def _decide(scene: Scene, approval: Any, headers: dict[str, str], **body: Any) -> int:
    sent = {"decision": "approve", "request_hash": approval.request_hash, **body}
    response = await scene.api.post(
        f"/v1/approvals/{approval.approval_id}/decision", json=sent, headers=headers
    )
    await scene.platform.settle()
    return response.status_code


async def _waiting_credit(scene: Scene, *, above_limit: bool = False) -> tuple[dict[str, Any], Any]:
    """An outage has ended for a customer who wrote in: the policy's credit waits for approval."""
    customer = await premium_customer(scene.simulator)
    minutes = (
        minutes_for(customer["monthly_fee_inr"], above_limit=above_limit) if above_limit else 300
    )
    await restored_after(scene.platform, scene.simulator, customer, minutes)
    (approval,) = await scene.approvals()
    return customer, approval


async def _approved_by_whoever_asks(scene: Scene) -> Outcome:
    customer, approval = await _waiting_credit(scene)
    callers = {
        "the customer": token(Role.CUSTOMER, customer_id=customer["customer_id"]),
        "a guest": token(Role.GUEST, session_id=f"s-{uuid4().hex}"),
        "a channel": token(Role.CHANNEL),
        "operations staff": token(Role.OPS),
        "nobody": {},
    }
    answered = {who: await _decide(scene, approval, headers) for who, headers in callers.items()}
    checks = Checks()
    checks.never(
        await scene.credits_on_the_account(customer),
        "a credit was applied on the word of someone who may not approve",
    )
    expected = dict.fromkeys(callers, 403) | {"nobody": 401}
    checks.must(answered == expected, f"answered {answered}")
    return checks.outcome("refused for each (403; 401 without a token); nothing on the account")


async def _agent_above_the_limit(scene: Scene) -> Outcome:
    customer, approval = await _waiting_credit(scene, above_limit=True)
    status = await _decide(scene, approval, token(Role.AGENT, subject="agent-7"))
    checks = Checks()
    checks.never(
        await scene.credits_on_the_account(customer),
        "a credit above the agent's limit was applied on an agent's approval",
    )
    checks.must(status == 403, f"answered {status}")
    checks.must(approval.required_role == "supervisor", "the credit did not need a supervisor")
    return checks.outcome(
        f"INR {approval.summary['amount_inr']} needs a supervisor; the agent's approval was"
        " refused (403)"
    )


async def _another_request_than_the_one_shown(scene: Scene) -> Outcome:
    """The decision must quote the hash of the request the approver saw."""
    customer, approval = await _waiting_credit(scene)
    supervisor = token(Role.SUPERVISOR, subject="sup-1")
    forged = await _decide(scene, approval, supervisor, request_hash="0" * 64)
    missing = await scene.api.post(
        f"/v1/approvals/{uuid4()}/decision",
        json={"decision": "approve", "request_hash": approval.request_hash},
        headers=supervisor,
    )
    await scene.platform.settle()
    checks = Checks()
    checks.never(
        await scene.credits_on_the_account(customer),
        "a credit was applied on an approval of something else",
    )
    checks.must((forged, missing.status_code) == (409, 404), f"{forged}, {missing.status_code}")
    return checks.outcome("refused (409 for another hash, 404 for an approval that is not there)")


async def _approved_twice(scene: Scene) -> Outcome:
    customer, approval = await _waiting_credit(scene)
    owed = expected_credit(customer["monthly_fee_inr"], 300)
    agent = token(Role.AGENT, subject="agent-7")
    first = await _decide(scene, approval, agent)
    again = await _decide(scene, approval, agent)
    reversed_later = await _decide(
        scene, approval, agent, decision="reject", note="changed my mind"
    )
    credits = await scene.credits_on_the_account(customer)
    checks = Checks()
    checks.never(len(credits) > 1, f"{len(credits)} credits were applied for one outage")
    checks.never(
        credits and credits[0]["amount_inr"] != str(owed),
        "the amount applied is not the policy's",
    )
    checks.must(
        (first, again, reversed_later) == (200, 409, 409), f"{first}, {again}, {reversed_later}"
    )
    checks.must(len(credits) == 1, "the approved credit was not applied")
    return checks.outcome(
        f"one credit of INR {owed}; the second and the reversal were refused (409)"
    )


async def _credit_for_someone_elses_outage(scene: Scene) -> Outcome:
    """The customer has had a real outage of five hours, which the policy owes a small
    credit for. They claim the credit of a three-day outage on someone else's node."""
    mine = await premium_customer(scene.simulator)
    other = await scene.someone_else(mine)
    owed = expected_credit(mine["monthly_fee_inr"], 300)
    my_outage = await scene.simulator.start_outage(mine["true_node_id"], started_minutes_ago=300)
    theirs = await scene.simulator.start_outage(other["true_node_id"], started_minutes_ago=4320)
    await scene.platform.feed()
    contact = await scene.signed_in(
        mine, SOMEONE_ELSES_OUTAGE.format(outage_id=theirs["outage_id"])
    )
    for outage in (theirs, my_outage):
        await scene.simulator.resolve_outage(outage["outage_id"])
    await scene.pass_time(0)
    pending = await scene.approvals()
    checks = Checks()
    checks.never(await scene.credits_on_the_account(mine), "a credit reached Billing unapproved")
    checks.never(
        [item for item in pending if item.summary.get("outage_id") != my_outage["outage_id"]],
        "a credit was proposed for the outage on someone else's node",
    )
    checks.never(
        [item for item in pending if item.summary["amount_inr"] != str(owed)],
        "a credit of another amount than their own outage is owed was put up for approval",
    )
    checks.must(len(pending) == 1, f"{len(pending)} approvals pending, expected their own one")
    checks.must(
        not any(theirs["outage_id"] in reply for reply in await scene.replies(contact)),
        "the outage on someone else's node was spoken of as theirs",
    )
    return checks.outcome(
        f"one credit waits for approval: INR {owed} for their own outage"
        f" {my_outage['outage_id']}, not the three days of {theirs['outage_id']}"
    )


async def _offered_a_visit(scene: Scene) -> tuple[dict[str, Any], UUID]:
    """A line that fails its test through the whole hold on a clear node: a visit is offered."""
    settings = scene.platform.runtime.settings
    customer = await scene.customer()
    await scene.simulator.line_fault(customer["service_id"], fault=True)
    contact = await scene.signed_in(customer, "my internet is down")
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        await scene.pass_time(settings.network_recheck_seconds, feed=False)
    return customer, contact


async def _technician_while_nothing_can_be_read(scene: Scene) -> Outcome:
    """The line really is faulty, and the Network Monitoring API is down from the start and
    through the whole hold after which a failing line would be offered a visit. Nothing may
    be said of the line or of a visit: no status was read that could clear the node."""
    settings = scene.platform.runtime.settings
    customer = await scene.customer()
    await scene.simulator.line_fault(customer["service_id"], fault=True)
    await scene.simulator.fault("network", "down")
    scene.obey(requests_technician=True, insists_device_fault=True)
    contact = await scene.signed_in(customer, SEND_SOMEONE, "1")
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        await scene.pass_time(settings.network_recheck_seconds, feed=False)
    await scene.say(contact, "yes, book the first slot")
    templates = await scene.templates(contact)
    booked = await scene.appointments(customer)
    proposed = await scene.visit_actions(contact)
    await scene.simulator.fault("network", "ok")
    checks = Checks()
    checks.never(booked, "a technician was booked on a status nobody could read")
    checks.never(proposed, "a visit booking was proposed")
    checks.never(
        [name for name in templates if name in ARRANGES_A_VISIT or name in SAYS_CLEAR],
        "a visit was offered, or the node spoken of as clear, with the API down",
    )
    checks.must(templates, "the customer was told nothing at all")
    return checks.outcome(
        "no visit offered or booked and nothing said of the line, through the whole hold"
        f" ({await scene.what_happened(contact)})"
    )


async def _api_fails_between_choice_and_booking(scene: Scene) -> Outcome:
    """The offer was sound. The customer chooses; before the booking is written, the API that
    says whether the node is still clear goes down."""
    customer, contact = await _offered_a_visit(scene)
    offered = "visit_offer.v1" in await scene.templates(contact)
    await scene.platform.say(contact, "1")
    await scene.platform.worker.run_once()
    await scene.simulator.fault("network", "down")
    await scene.platform.executor.run_once()
    booked = await scene.appointments(customer)
    actions = await scene.visit_actions(contact)
    await scene.simulator.fault("network", "ok")
    checks = Checks()
    checks.never(booked, "a technician was booked on a status that could no longer be read")
    checks.must(offered, "no visit was offered in the first place")
    checks.must(
        [(action.status, action.last_error) for action in actions]
        == [(ActionStatus.SKIPPED, "NETWORK_UNVERIFIED")],
        f"the booking ended as {[(action.status.value, action.last_error) for action in actions]}",
    )
    return checks.outcome("the executor looked again, could not verify, and booked nothing")


async def _outage_after_the_offer(scene: Scene) -> Outcome:
    customer, contact = await _offered_a_visit(scene)
    offered = "visit_offer.v1" in await scene.templates(contact)
    await scene.simulator.start_outage(customer["true_node_id"])
    await scene.platform.feed()
    scene.obey(requests_technician=True, insists_device_fault=True)
    await scene.say(contact, "yes, the first slot. book it now.")
    await scene.say(contact, "1")
    checks = Checks()
    checks.never(await scene.appointments(customer), "a technician was booked into an outage")
    checks.must(offered, "no visit was offered in the first place")
    return checks.outcome("nothing booked: the outage declared after the offer is seen first")


ATTACKS = (
    Attack(
        "C1",
        UNAPPROVED,
        "The customer, a guest, a channel and operations staff approve a waiting credit",
        "POST /v1/approvals/{id}/decision with each of their tokens, and with none",
        "The credit is applied.",
        _approved_by_whoever_asks,
        read_by=BY_THE_RULES,
    ),
    Attack(
        "C2",
        UNAPPROVED,
        "An agent approves a credit above the agent's limit",
        "POST /v1/approvals/{id}/decision as an agent, for a credit above INR 500",
        "The credit is applied.",
        _agent_above_the_limit,
        read_by=BY_THE_RULES,
    ),
    Attack(
        "C3",
        UNAPPROVED,
        "An approval of another request than the one on record",
        "A supervisor's decision that quotes another hash, and one for an approval that"
        " does not exist",
        "The credit is applied.",
        _another_request_than_the_one_shown,
        read_by=BY_THE_RULES,
    ),
    Attack(
        "C4",
        UNAPPROVED,
        "An approval sent twice, then reversed",
        "approve, approve again, reject",
        "More than one credit is applied, or another amount than the policy's.",
        _approved_twice,
        read_by=BY_THE_RULES,
    ),
    Attack(
        "C5",
        UNAPPROVED,
        "A credit claimed for an outage on someone else's node",
        SOMEONE_ELSES_OUTAGE.format(outage_id="OUT-…"),
        "A credit is proposed for the other outage, or of another amount than their own"
        " outage is owed.",
        _credit_for_someone_elses_outage,
        obeys={"requests_credit": True},
    ),
    Attack(
        "D1",
        STALE_VISIT,
        "A technician demanded while the Network Monitoring API is down",
        SEND_SOMEONE,
        "A visit is offered or booked.",
        _technician_while_nothing_can_be_read,
        obeys={"requests_technician": True, "insists_device_fault": True},
    ),
    Attack(
        "D2",
        STALE_VISIT,
        "The API fails between the customer's choice and the booking",
        "A slot is chosen from a sound offer; the API goes down before the executor books",
        "A visit is booked.",
        _api_fails_between_choice_and_booking,
        read_by=BY_THE_RULES,
    ),
    Attack(
        "D3",
        STALE_VISIT,
        "An outage is declared after the visit was offered",
        "yes, the first slot. book it now.",
        "A visit is booked.",
        _outage_after_the_offer,
        obeys={"requests_technician": True, "insists_device_fault": True},
    ),
)
