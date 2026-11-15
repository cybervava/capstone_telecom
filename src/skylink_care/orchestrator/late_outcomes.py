"""Outcomes that arrive after the workflow has let go of the contact.

A credit or a visit booking can end after the contact went to a person, or was closed. The
customer is still owed the news, and a write that did not happen still has to reach a person.
"""

from __future__ import annotations

from datetime import datetime

from skylink_care.agents import visit_replies
from skylink_care.agents.dispatch import OfferedSlot
from skylink_care.agents.replies import Reply
from skylink_care.db.actions import CREDIT_APPLY, VISIT_BOOK, Action, ActionStatus
from skylink_care.db.contacts import Contact
from skylink_care.db.database import Connection
from skylink_care.domain.dispatch import VisitReason
from skylink_care.orchestrator.credit_nodes import credit_outcome, outcome_note, tell_outcome
from skylink_care.runtime import OrchestratorRuntime

# Actions whose outcome the customer's conversation is told.
OWED_TO_THE_CUSTOMER = frozenset({CREDIT_APPLY, VISIT_BOOK})
VISIT_NOT_BOOKED = "VISIT_NOT_BOOKED"


async def tell_late_outcome(
    rt: OrchestratorRuntime, contact: Contact, action: Action, conn: Connection
) -> None:
    """Tell the customer how the action ended, once, and hand on what a person must do."""
    if action.action_type == CREDIT_APPLY:
        outcome = credit_outcome(contact.complaint_ref, action)
        await tell_outcome(rt.messages, contact, str(action.action_id), outcome, conn=conn)
        follow_up = outcome.follow_up
        note = outcome_note(follow_up, action) if follow_up else ""
    else:
        reply, follow_up, note = _visit_outcome(contact.complaint_ref, action)
        await rt.messages.add(
            contact.contact_id,
            direction="outbound",
            author="agent:dispatch",
            body=reply.text,
            dedupe_key=f"visit-outcome:{action.action_id}",
            meta={"template_id": reply.template_id, "facts": reply.facts},
            conn=conn,
        )
    if follow_up is None:
        return
    await rt.handoffs.open(
        contact.contact_id, reason=follow_up, summary=note, priority=3, conn=conn
    )
    if contact.status != "HANDOFF":
        await rt.contacts.update(contact.contact_id, conn=conn, status="HANDOFF", closed_at=None)


def _visit_outcome(complaint_ref: str, action: Action) -> tuple[Reply, str | None, str]:
    slot = _slot(action)
    if action.status is ActionStatus.SUCCEEDED and slot is not None:
        booked = visit_replies.visit_booked(complaint_ref, slot, appointment_id=action.external_ref)
        return booked, None, ""
    reason = action.last_error or ""
    if action.status is ActionStatus.SKIPPED and reason.startswith(
        VisitReason.VISIT_ALREADY_BOOKED.value
    ):
        return visit_replies.visit_already_booked(complaint_ref, None), None, ""
    note = (
        f"Reason: {VISIT_NOT_BOOKED}. The technician visit the customer chose"
        f" ({action.action_key}, slot {slot.slot_id if slot else 'unknown'}) ended"
        f" {action.status.value}: {reason or 'no detail'}. The line failed its test; arrange"
        " the repair with the customer."
    )
    return visit_replies.visit_not_booked(complaint_ref), VISIT_NOT_BOOKED, note


def _slot(action: Action) -> OfferedSlot | None:
    stored = action.inputs.get("slot") or {}
    try:
        return OfferedSlot(
            slot_id=str(stored["slot_id"]),
            start=datetime.fromisoformat(stored["start"]),
            end=datetime.fromisoformat(stored["end"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
