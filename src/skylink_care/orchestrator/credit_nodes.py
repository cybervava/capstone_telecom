"""Workflow steps for service credits: the offer at restoration and the news of its outcome.

A credit is proposed, never applied, from here. The proposal freezes the request (amount, policy
version, outage, complaint reference) under an identity of one credit per service per outage,
and asks for whatever approval the policy requires in the same transaction. The executor applies
it; the contact is woken when it has ended, one way or the other, and tells the customer.

Whatever the customer is told about a credit is read from that frozen request, not from what
was worked out in the turn: if a credit for the service and outage is on record already, the
record is what stands, and a difference from it goes to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from skylink_care.agents import replies
from skylink_care.agents.replies import Reply
from skylink_care.agents.resolution import (
    CreditAssessment,
    ServiceFacts,
    credit_key,
    working,
)
from skylink_care.db.actions import (
    CREDIT_APPLY,
    CREDIT_LANE,
    OPEN,
    Action,
    ActionStatus,
    NewAction,
    Proposal,
)
from skylink_care.db.contacts import Contact
from skylink_care.db.database import Connection
from skylink_care.domain.credit import OUTAGE_CREDIT, ApprovalLevel, CreditDecision
from skylink_care.domain.models import OutageInfo
from skylink_care.orchestrator.handover import CHECK_ACCOUNT_FIRST, UNCONFIRMED, handoff_summary
from skylink_care.orchestrator.node_support import NodeSupport
from skylink_care.orchestrator.outgoing import store_reply
from skylink_care.orchestrator.routing import CUSTOMER_EVENTS
from skylink_care.orchestrator.state import ContactState, Phase
from skylink_care.runtime import OrchestratorRuntime

AUTHOR = "agent:resolution"
CREDIT_REVIEW = "CREDIT_REVIEW"
CREDIT_NOT_APPLIED = "CREDIT_NOT_APPLIED"
CREDIT_UNCONFIRMED = "CREDIT_UNCONFIRMED"
FIRST_CYCLE_DAY, LAST_CYCLE_DAY = 1, 28


@dataclass(frozen=True)
class CreditOutcome:
    """How a credit ended: the words for the customer, and whether a person has to act."""

    reply: Reply
    status: str
    # Why a person takes it up, when one must.
    follow_up: str | None = None


def credit_outcome(complaint_ref: str, action: Action | None) -> CreditOutcome:
    status = action.status if action else None
    if action is not None and status is ActionStatus.SUCCEEDED:
        reply = replies.credit_applied(
            complaint_ref, amount_inr=frozen_amount(action), credit_ref=action.external_ref
        )
        return CreditOutcome(reply, status.value)
    if status is ActionStatus.SKIPPED:
        return CreditOutcome(replies.credit_already_there(complaint_ref), status.value)
    if action is not None and action.gave_up:
        # Replaying stopped without an answer from Billing: the credit may be on the account.
        return CreditOutcome(
            replies.credit_unconfirmed(complaint_ref), UNCONFIRMED, CREDIT_UNCONFIRMED
        )
    # Rejected, expired, refused or blocked: a person follows it up with the customer.
    return CreditOutcome(
        replies.credit_not_applied(complaint_ref),
        status.value if status else "UNKNOWN",
        CREDIT_NOT_APPLIED,
    )


def frozen_amount(action: Action) -> str:
    """The amount of a credit as it was frozen in its proposal, which is what can be applied."""
    return str(action.request_body.get("amount_inr"))


def outcome_note(reason: str, action: Action) -> str:
    """What a person needs to follow a credit up, when no conversation state is at hand."""
    note = (
        f"Reason: {reason}. Service credit INR {frozen_amount(action)} ({action.action_key})"
        f" ended {action.status.value}: {action.last_error or 'no detail'}."
    )
    if reason == CREDIT_UNCONFIRMED:
        note += " " + CHECK_ACCOUNT_FIRST
    return note


async def tell_outcome(
    rt: OrchestratorRuntime,
    contact: Contact,
    action_id: str,
    outcome: CreditOutcome,
    *,
    conn: Connection | None = None,
) -> None:
    """Store the news of one credit's outcome, once, whichever path gets to it."""
    await store_reply(
        rt,
        contact,
        outcome.reply,
        author=AUTHOR,
        dedupe_key=f"credit-outcome:{action_id}",
        conn=conn,
    )


class CreditNodes(NodeSupport):
    async def credit_result(self, state: ContactState) -> dict[str, Any]:
        """Credits this contact was waiting on have ended: tell the customer how."""
        contact = await self._contact(state)
        credits: list[dict[str, Any]] = []
        follow_ups: list[str] = []
        for credit in state.get("credits") or []:
            settled, follow_up = await self._settle(contact, credit)
            credits.append(settled)
            if follow_up and follow_up not in follow_ups:
                follow_ups.append(follow_up)
        updated: dict[str, Any] = {"credits": credits}
        if follow_ups:
            for reason in follow_ups:
                summary = handoff_summary({**state, "credits": credits}, reason)
                await self._rt.handoffs.open(
                    contact.contact_id, reason=reason, summary=summary, priority=3
                )
            await self._rt.timers.cancel(contact.contact_id)
            await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
            return {**updated, "phase": Phase.HANDED_OFF.value}
        if any(event.get("event_type") in CUSTOMER_EVENTS for event in state.get("events", [])):
            # The customer also wrote in the same moment: their message is handled next.
            return {**updated, "phase": Phase.NEW_TURN.value}
        still_waiting = any(credit.get("pending") for credit in credits)
        if state.get("close_when_credit_settles") and not still_waiting:
            return {**updated, "phase": Phase.CLOSED.value}
        return {**updated, "phase": Phase.REPLIED.value}

    async def _settle(
        self, contact: Contact, credit: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Tell the outcome of one credit if it has ended. Returns its record and whether a
        person must follow it up."""
        if not credit.get("pending"):
            return credit, None
        action = await self._rt.actions.get(UUID(credit["action_id"]))
        if action is not None and action.status in OPEN:
            return credit, None  # its own news is still to come
        outcome = credit_outcome(contact.complaint_ref, action)
        await tell_outcome(self._rt, contact, credit["action_id"], outcome)
        settled = {**credit, "pending": False, "status": outcome.status}
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor=AUTHOR,
            step="credit.outcome_told",
            outcome=outcome.status,
            outputs={
                "action_key": credit.get("action_key"),
                "amount_inr": credit.get("amount_inr"),
            },
        )
        return settled, outcome.follow_up

    async def _offer_credit(
        self, contact: Contact, state: ContactState, outage: OutageInfo | None
    ) -> tuple[str, dict[str, Any]]:
        """Assess a credit for the resolved outage. Returns what to tell the customer about it
        (possibly nothing) and the state to keep."""
        if outage is None or outage.resolved_at is None or not _has_service(state):
            return "", {}
        facts = _service_facts(state)
        if facts is None:
            # A service whose fee or billing cycle cannot be read is not "nothing due".
            return await self._review(contact, state, " The service's fee or cycle is unusable.")
        assessment = await self._rt.resolution.assess(
            facts,
            outage,
            now=self._rt.now(),
            audit=self._rt.auditor(contact.contact_id, contact.correlation_id),
        )
        if assessment.decision is None:
            # Not knowing is not the same as "none due": a person checks it.
            return await self._review(contact, state)
        decision = assessment.decision
        if not decision.eligible:
            asked = (state.get("intent") or {}).get("requests_credit")
            line = replies.no_credit_line(
                decision.reason.value, policy_version=decision.policy_version
            )
            return (line if asked else ""), {}
        proposal = await self._propose_credit(contact, facts, outage, assessment)
        action = proposal.action
        if not proposal.created:
            return await self._on_record(contact, state, proposal, decision)
        line = replies.credit_line(
            frozen_amount(action),
            policy_version=decision.policy_version,
            needs_approval=action.approval_level != ApprovalLevel.NONE.value,
        )
        return line, {"credits": [*(state.get("credits") or []), _record(action)]}

    async def _on_record(
        self, contact: Contact, state: ContactState, proposal: Proposal, decision: CreditDecision
    ) -> tuple[str, dict[str, Any]]:
        """A credit for this service and outage exists already, from an earlier turn or from
        another conversation of the same customer. It stands, and it is what the customer is
        told about: its amount, and where it has got to."""
        action = proposal.action
        amount = frozen_amount(action)
        note = ""
        # The request on record differs from this turn's in its complaint reference whenever
        # another conversation proposed it. What matters here is whether the amount does.
        differs = _amount_differs(action, decision)
        if differs:
            # Worked out again it comes to something else (the outage's times were amended, or
            # the policy changed). Whether the difference is owed is for a person to decide.
            await self._review(
                contact,
                state,
                f" On record: INR {amount} ({action.action_key}, {action.status.value});"
                f" worked out now: INR {decision.amount_inr}.",
            )
            note = replies.CREDIT_AMOUNT_REVIEW
        if action.status in OPEN:
            if action.contact_id != contact.contact_id:
                return replies.CREDIT_WITH_ANOTHER_CONTACT + note, {}
            tracked = list(state.get("credits") or [])
            if not any(credit.get("action_id") == str(action.action_id) for credit in tracked):
                tracked.append(_record(action))
            return replies.credit_in_progress_line(amount) + note, {"credits": tracked}
        if action.status is ActionStatus.SUCCEEDED:
            return replies.credit_already_applied_line(amount) + note, {}
        if action.status is ActionStatus.SKIPPED:
            return replies.CREDIT_ALREADY_ON_ACCOUNT + note, {}
        if not differs:
            await self._review(
                contact,
                state,
                f" An earlier credit for this outage ended {action.status.value}"
                f" ({action.action_key}).",
            )
        return replies.CREDIT_NOT_APPLIED_EARLIER, {}

    async def _review(
        self, contact: Contact, state: ContactState, detail: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """Ask a person to look at the credit; the conversation itself carries on."""
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=CREDIT_REVIEW,
            summary=handoff_summary(state, CREDIT_REVIEW) + detail,
            priority=4,
        )
        return replies.CREDIT_UNDER_REVIEW, {}

    async def _propose_credit(
        self,
        contact: Contact,
        facts: ServiceFacts,
        outage: OutageInfo,
        assessment: CreditAssessment,
    ) -> Proposal:
        """Record the credit and ask for its approval, together. One per service per outage."""
        decision = assessment.decision
        if decision is None:  # pragma: no cover - callers check eligibility first
            raise RuntimeError("no credit decision to propose")
        body = _request(contact, facts, outage, decision)
        # The working, with what the executor needs to check the billing-cycle cap again.
        shown = working(decision) | {
            "monthly_fee_inr": str(facts.monthly_fee_inr),
            "service_type": facts.service_type,
            "billing_cycle_day": facts.billing_cycle_day,
        }
        new = NewAction(
            action_key=credit_key(facts.service_id, outage.outage_id),
            action_type=CREDIT_APPLY,
            lane=CREDIT_LANE,
            requested_by=AUTHOR,
            contact_id=contact.contact_id,
            correlation_id=contact.correlation_id,
            approval_level=assessment.level.value,
            policy_version=decision.policy_version,
            request_body=body,
            inputs=shown,
        )
        ttl = timedelta(seconds=self._rt.settings.approval_ttl_seconds)
        async with self._rt.db.transaction() as conn:
            proposal = await self._rt.actions.propose(new, conn=conn)
            if proposal.created and assessment.level is not ApprovalLevel.NONE:
                await self._rt.approvals.request(
                    conn,
                    action_id=proposal.action.action_id,
                    required_role=assessment.level.value,
                    request_hash=proposal.action.request_hash,
                    # What the approver is shown: the request itself and how it was worked out.
                    summary={**body, "working": shown},
                    expires_at=self._rt.now() + ttl,
                )
        return proposal


def _request(
    contact: Contact, facts: ServiceFacts, outage: OutageInfo, decision: CreditDecision
) -> dict[str, Any]:
    """The request to Billing, exactly as it will be sent if it is approved."""
    return {
        "customer_id": facts.customer_id,
        "service_id": facts.service_id,
        "amount_inr": str(decision.amount_inr),
        "reason_code": OUTAGE_CREDIT,
        "outage_id": outage.outage_id,
        "policy_version": decision.policy_version,
        "complaint_ref": contact.complaint_ref,
    }


def _amount_differs(action: Action, decision: CreditDecision) -> bool:
    try:
        return Decimal(frozen_amount(action)) != decision.amount_inr
    except InvalidOperation:
        return True


def _record(action: Action) -> dict[str, Any]:
    """What the contact keeps about a credit it is waiting on."""
    return {
        "action_id": str(action.action_id),
        "action_key": action.action_key,
        "amount_inr": frozen_amount(action),
        "approval": action.approval_level,
        "pending": True,
    }


def _has_service(state: ContactState) -> bool:
    identity, service = state.get("identity") or {}, state.get("service") or {}
    return bool(identity.get("customer_id") and service.get("service_id"))


def _service_facts(state: ContactState) -> ServiceFacts | None:
    """The verified service of this contact, or None when its fee or cycle cannot be used."""
    identity, service = state.get("identity") or {}, state.get("service") or {}
    try:
        fee = Decimal(str(service.get("monthly_fee_inr")))
        cycle_day = int(service.get("billing_cycle_day") or FIRST_CYCLE_DAY)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not fee.is_finite() or fee <= 0 or not FIRST_CYCLE_DAY <= cycle_day <= LAST_CYCLE_DAY:
        return None
    return ServiceFacts(
        customer_id=str(identity["customer_id"]),
        service_id=str(service["service_id"]),
        service_type=str(service.get("service_type")),
        monthly_fee_inr=fee,
        active=bool(service.get("active")),
        billing_cycle_day=cycle_day,
    )
