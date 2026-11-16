"""Workflow steps for a technician visit: the offer, the customer's answer, the booking.

A visit is the one remedy here that costs a journey, and the one this platform exists to stop
being arranged for nothing. It is offered only when the customer's line still fails after the
hold on a node proven clear, booked only for a slot the customer chose, and proposed, never
booked, from here: the executor books it after checking the same evidence again for itself.

The customer's reply to an offer is read here, after the network and the line have been looked
at afresh, so a choice made hours after the offer is never acted on with the offer's evidence.
A choice is tied to the message that made it: a booking is proposed under that message, once,
and a later message from the customer means the choice is asked for again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from skylink_care.agents import visit_replies
from skylink_care.agents.dispatch import AnswerKind, Booking, FieldView, OfferedSlot, read_answer
from skylink_care.agents.replies import Reply
from skylink_care.db.actions import (
    TERMINAL,
    VISIT_BOOK,
    VISIT_LANE,
    Action,
    ActionStatus,
    NewAction,
)
from skylink_care.db.contacts import Contact
from skylink_care.db.messages import Message
from skylink_care.db.timers import CLOSE_QUIET
from skylink_care.domain.dispatch import (
    VisitDecision,
    VisitEvidence,
    VisitReason,
    decide_visit,
)
from skylink_care.domain.models import NetworkState
from skylink_care.guardrails.claims import Claim
from skylink_care.orchestrator.evidence import parse_time
from skylink_care.orchestrator.handover import handoff_summary
from skylink_care.orchestrator.node_support import NodeSupport
from skylink_care.orchestrator.state import (
    VISIT_BOOKED,
    VISIT_BOOKING,
    VISIT_CHOSEN,
    VISIT_DECLINED,
    VISIT_OFFERED,
    ContactState,
    Phase,
    Trigger,
)

AUTHOR = "agent:dispatch"
LINE_FAULT = "LINE_FAULT"
VISIT_REASON = "Line still failing its remote test after the hold; node confirmed clear"
# The offer is repeated once when the answer cannot be read; after that a person takes over.
MAX_ASKS = 1
# A booking the executor declined on changed evidence is proposed again at most this often.
MAX_RETRIES = 2
ALREADY_BOOKED = VisitReason.VISIT_ALREADY_BOOKED.value
SLOT_TAKEN = "SLOT_TAKEN"


@dataclass(frozen=True)
class _Choice:
    """The slot the customer chose, and the message they chose it with."""

    slot: OfferedSlot
    message_id: int


class VisitNodes(NodeSupport):
    async def visit(self, state: ContactState) -> dict[str, Any]:
        """The line still fails after the hold on a node proven clear: read the customer's
        answer if there is one, then offer a visit or propose the booking of the chosen slot."""
        contact = await self._contact(state)
        visit = state.get("visit") or {}
        asked = state.get("trigger") == Trigger.CUSTOMER.value
        if visit.get("status") == VISIT_BOOKING:
            # The booking is with the executor. Its outcome is told when it comes.
            if asked:
                reply = visit_replies.visit_still_being_booked(
                    contact.complaint_ref, _slot(visit["slot"])
                )
                await self._say(contact, state, reply, author=AUTHOR)
            return {"phase": Phase.REPLIED.value}
        if visit.get("status") == VISIT_OFFERED and not asked:
            return {"phase": Phase.REPLIED.value}  # the offer stands and nobody has written
        choice, settled = await self._choice(contact, state)
        if settled is not None:
            return settled
        view = await self._field_view(contact, state)
        if not view.available:
            return await self._to_a_person(contact, state)
        decision = await self._decide(contact, state, view, consented=choice is not None)
        if decision.reason is VisitReason.VISIT_ALREADY_BOOKED:
            return await self._already_booked(contact, state, view.booked)
        if decision.reason is VisitReason.NO_CONSENT:
            return await self._offer(contact, state, view.slots)
        if decision.allowed and choice is not None:
            return await self._book(contact, state, choice)
        # The evidence does not hold after all, in a way the routing did not foresee.
        return await self._to_a_person(contact, state)

    async def visit_result(self, state: ContactState) -> dict[str, Any]:
        """The booking this contact was waiting on has ended: tell the customer how."""
        contact = await self._contact(state)
        visit = state.get("visit") or {}
        action = await self._rt.actions.get(UUID(visit["action_id"]))
        return await self._after_booking(contact, state, visit, action)

    # --- the customer's answer ------------------------------------------------------------------

    async def _choice(
        self, contact: Contact, state: ContactState
    ) -> tuple[_Choice | None, dict[str, Any] | None]:
        """The slot the customer has chosen, if any. The second value is the end of the turn
        when their reply settles it on its own: a refusal, or words that name no slot."""
        visit = state.get("visit") or {}
        status = visit.get("status")
        latest = await self._latest_inbound(contact)
        if status == VISIT_CHOSEN:
            # Proposed once already and declined on evidence that has changed back. The choice
            # stands only if the customer has written nothing since they made it.
            if latest is None or latest.message_id != visit.get("consent_message_id"):
                return None, None
            return _Choice(_slot(visit["chosen"]), latest.message_id), None
        if status != VISIT_OFFERED:
            return None, None
        offered = [_slot(item) for item in visit.get("slots", [])]
        answer = read_answer(latest.body if latest else "", offered, now=self._rt.now())
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor=AUTHOR,
            step="visit.answer",
            outcome=answer.kind.value,
            outputs={
                "slot_id": answer.slot.slot_id if answer.slot else None,
                "message_id": latest.message_id if latest else None,
            },
        )
        if answer.kind is AnswerKind.CHOSEN and answer.slot is not None and latest is not None:
            return _Choice(answer.slot, latest.message_id), None
        if answer.kind is AnswerKind.DECLINED:
            reply = visit_replies.visit_declined(contact.complaint_ref)
            waiting = await self._wait_for_customer(contact, state, reply)
            return None, {**waiting, "visit": {**visit, "status": VISIT_DECLINED}}
        if not (state.get("intent") or {}).get("carried_over"):
            # A message with a topic of its own ("my internet is still down"), not a reply to
            # the offer: the slots are offered afresh below.
            return None, None
        asks = int(visit.get("asks", 0))
        if asks >= MAX_ASKS:
            reply = visit_replies.visit_needs_a_person(contact.complaint_ref)
            return None, await self._to_a_person(contact, state, reply)
        reply = visit_replies.visit_choice_unclear(contact.complaint_ref, offered)
        waiting = await self._wait_for_customer(contact, state, reply)
        return None, {**waiting, "visit": {**visit, "asks": asks + 1}}

    async def _latest_inbound(self, contact: Contact) -> Message | None:
        messages = await self._rt.messages.list_for_contact(contact.contact_id)
        return next((m for m in reversed(messages) if m.direction == "inbound"), None)

    # --- the evidence ---------------------------------------------------------------------------

    async def _field_view(self, contact: Contact, state: ContactState) -> FieldView:
        service = state.get("service") or {}
        return await self._rt.dispatch.view(
            str(service.get("service_id")),
            str(service.get("pincode") or ""),
            audit=self._rt.auditor(contact.contact_id, contact.correlation_id),
        )

    async def _decide(
        self, contact: Contact, state: ContactState, view: FieldView, *, consented: bool
    ) -> VisitDecision:
        evidence = _evidence(state, view, consented=consented)
        decision = decide_visit(evidence, self._rt.dispatch.limits, now=self._rt.now())
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor=AUTHOR,
            step="dispatch.decide",
            outcome=decision.reason.value,
            inputs={
                "network_state": evidence.network_state.value,
                "line_test": evidence.line_result,
                "line_tested_at": _iso(evidence.line_tested_at),
                "first_failed_at": _iso(evidence.first_failed_at),
                "consented": evidence.consented,
                "visit_already_booked": evidence.visit_already_booked,
            },
        )
        return decision

    # --- steps ----------------------------------------------------------------------------------

    async def _offer(
        self,
        contact: Contact,
        state: ContactState,
        slots: tuple[OfferedSlot, ...],
        *,
        taken: bool = False,
    ) -> dict[str, Any]:
        if not slots:
            return await self._to_a_person(contact, state)
        line, network = state.get("line_test") or {}, state.get("network") or {}
        now = self._rt.now()
        reply = (
            visit_replies.visit_slot_taken(contact.complaint_ref, slots)
            if taken
            else visit_replies.visit_offer(
                contact.complaint_ref,
                slots,
                tested_at=parse_time(line.get("tested_at")) or now,
                evidence_at=parse_time(network.get("evidence_at")) or now,
            )
        )
        waiting = await self._wait_for_customer(contact, state, reply)
        offer = {"status": VISIT_OFFERED, "slots": [_as_state(slot) for slot in slots], "asks": 0}
        return {**waiting, "recheck_key": None, "network_rechecks": 0, "visit": offer}

    async def _book(self, contact: Contact, state: ContactState, choice: _Choice) -> dict[str, Any]:
        """Propose the booking of the chosen slot: once per choice and per look at the evidence,
        and never while another booking for the service is in progress."""
        visit = state.get("visit") or {}
        retries = int(visit.get("retries", 0)) if visit.get("status") == VISIT_CHOSEN else 0
        # Before a booking is proposed: is the node proven clear, now? The executor asks the
        # same of evidence it reads itself; this keeps the proposal from being made at all.
        await self._must_be_backed(contact, state, Claim.VISIT, template_id="visit_being_booked.v1")
        proposal = await self._rt.actions.propose_only_one_open(
            self._booking(contact, state, choice, retries)
        )
        if proposal is None:
            return await self._being_arranged_elsewhere(contact, state)
        action = proposal.action
        booking = {
            "status": VISIT_BOOKING,
            "retries": retries,
            "consent_message_id": choice.message_id,
            "action_id": str(action.action_id),
            "action_key": action.action_key,
            "slot": _as_state(choice.slot),
        }
        if action.status in TERMINAL:
            # This very booking was proposed before and has ended (the turn ran twice).
            return await self._after_booking(contact, state, booking, action)
        reply = visit_replies.visit_being_booked(contact.complaint_ref, choice.slot)
        await self._say(contact, state, reply, author=AUTHOR)
        await self._rt.timers.cancel(contact.contact_id)
        await self._rt.contacts.update(contact.contact_id, status="IN_PROGRESS")
        return {"phase": Phase.REPLIED.value, "close_key": None, "visit": booking}

    def _booking(
        self, contact: Contact, state: ContactState, choice: _Choice, retries: int
    ) -> NewAction:
        service, network = state.get("service") or {}, state.get("network") or {}
        return NewAction(
            # One booking per choice the customer made, and per look at the evidence.
            action_key=f"visit:{contact.contact_id}:{choice.message_id}:{retries}",
            action_type=VISIT_BOOK,
            lane=VISIT_LANE,
            requested_by=AUTHOR,
            contact_id=contact.contact_id,
            correlation_id=contact.correlation_id,
            request_body={
                "customer_id": (state.get("identity") or {}).get("customer_id"),
                "service_id": service.get("service_id"),
                "slot_id": choice.slot.slot_id,
                "reason": VISIT_REASON,
                "complaint_ref": contact.complaint_ref,
            },
            # What the executor needs to check the evidence again, and what was agreed.
            inputs={
                "node_id": network.get("node_id"),
                "service_type": service.get("service_type"),
                "first_failed_at": state.get("line_fault_seen_at"),
                "consent_message_id": choice.message_id,
                "slot": _as_state(choice.slot),
            },
        )

    async def _after_booking(
        self, contact: Contact, state: ContactState, visit: dict[str, Any], action: Action | None
    ) -> dict[str, Any]:
        status = action.status if action else None
        reason = (action.last_error or "") if action else ""
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor=AUTHOR,
            step="visit.outcome",
            outcome=status.value if status else "UNKNOWN",
            outputs={"action_key": visit.get("action_key"), "reason": reason or None},
        )
        if action is not None and status is ActionStatus.SUCCEEDED:
            reply = visit_replies.visit_booked(
                contact.complaint_ref, _slot(visit["slot"]), appointment_id=action.external_ref
            )
            waiting = await self._wait_for_customer(contact, state, reply)
            booked = {**visit, "status": VISIT_BOOKED, "appointment_id": action.external_ref}
            return {**waiting, "visit": booked}
        if status is not ActionStatus.SKIPPED:
            return await self._to_a_person(contact, state)  # failed, blocked, or given up on
        if reason.startswith(ALREADY_BOOKED):
            view = await self._field_view(contact, state)
            return await self._already_booked(contact, state, view.booked)
        if reason.startswith(SLOT_TAKEN):
            view = await self._field_view(contact, state)
            if not view.available:
                return await self._to_a_person(contact, state)
            return await self._offer(contact, state, view.slots, taken=True)
        retries = int(visit.get("retries", 0))
        if retries >= MAX_RETRIES:
            return await self._to_a_person(contact, state)
        # The executor found the evidence changed (an outage, a line that passes now). The
        # choice stands; the network is looked at again and decides what happens next.
        again = {**visit, "status": VISIT_CHOSEN, "chosen": visit["slot"], "retries": retries + 1}
        return {"phase": Phase.RECHECK.value, "trigger": Trigger.RECHECK.value, "visit": again}

    async def _already_booked(
        self, contact: Contact, state: ContactState, booking: Booking | None
    ) -> dict[str, Any]:
        reply = visit_replies.visit_already_booked(
            contact.complaint_ref, booking.slot if booking else None
        )
        waiting = await self._wait_for_customer(contact, state, reply)
        known = {
            "status": VISIT_BOOKED,
            "appointment_id": booking.appointment_id if booking else None,
            "slot": _as_state(booking.slot) if booking else None,
        }
        return {**waiting, "visit": known}

    async def _being_arranged_elsewhere(
        self, contact: Contact, state: ContactState
    ) -> dict[str, Any]:
        """Another conversation of this customer is booking a visit at this very moment.
        Nothing is said to be booked. When the customer writes again, what Field Service says
        by then decides: a visit that is booked, or a fresh offer."""
        reply = visit_replies.visit_being_arranged_elsewhere(contact.complaint_ref)
        waiting = await self._wait_for_customer(contact, state, reply)
        return {**waiting, "visit": None}

    async def _wait_for_customer(
        self, contact: Contact, state: ContactState, reply: Reply
    ) -> dict[str, Any]:
        """Say it, and leave the next move to the customer."""
        await self._say(contact, state, reply, author=AUTHOR)
        await self._rt.timers.cancel(contact.contact_id, CLOSE_QUIET)
        armed = await self._arm_close(contact, state)
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        return {**armed, "phase": Phase.REPLIED.value, "asked_if_working": False}

    async def _to_a_person(
        self, contact: Contact, state: ContactState, reply: Reply | None = None
    ) -> dict[str, Any]:
        """The line needs a repair and a visit could not be arranged here."""
        said = reply or visit_replies.visit_not_booked(contact.complaint_ref)
        await self._say(contact, state, said, author=AUTHOR)
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=LINE_FAULT,
            summary=handoff_summary(state, LINE_FAULT),
            priority=await self._priority(state, LINE_FAULT),
        )
        await self._rt.timers.cancel(contact.contact_id)
        await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
        return {"phase": Phase.HANDED_OFF.value}


def _evidence(state: ContactState, view: FieldView, *, consented: bool) -> VisitEvidence:
    service, network = state.get("service") or {}, state.get("network") or {}
    line = state.get("line_test") or {}
    try:
        network_state = NetworkState(str(network.get("state")))
    except ValueError:
        network_state = NetworkState.UNKNOWN
    return VisitEvidence(
        service_active=bool(service.get("active")),
        visit_already_booked=view.booked is not None,
        network_state=network_state,
        network_evidence_at=parse_time(network.get("evidence_at")),
        line_result=line.get("result"),
        line_tested_at=parse_time(line.get("tested_at")),
        first_failed_at=parse_time(state.get("line_fault_seen_at")),
        consented=consented,
    )


def _as_state(slot: OfferedSlot) -> dict[str, str]:
    return {
        "slot_id": slot.slot_id,
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat(),
    }


def _slot(stored: dict[str, Any]) -> OfferedSlot:
    return OfferedSlot(
        slot_id=str(stored["slot_id"]),
        start=datetime.fromisoformat(stored["start"]),
        end=datetime.fromisoformat(stored["end"]),
    )


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
