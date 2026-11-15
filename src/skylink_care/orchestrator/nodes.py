"""Workflow nodes: what each step of a contact's journey does.

Every node may run more than once for the same turn (after a crash the run restarts from the last
checkpoint), so every side effect here is idempotent: messages carry a per-turn dedupe key,
actions carry their identity key, episodes are unique per node, timers carry a per-turn key.
Nodes never call a write API; they record proposals in the action ledger for the executor.

This module holds the control nodes and the conversation itself; the nodes that deal with the
network are in ``network_nodes.py`` and what they all share is in ``node_support.py``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from skylink_care.agents import replies
from skylink_care.agents.triage import AWAITING_DIGITS, Claim, IdentityStatus
from skylink_care.orchestrator.handover import clean_summary, handoff_summary, wording_for
from skylink_care.orchestrator.network_nodes import NetworkNodes
from skylink_care.orchestrator.node_support import waits_on_network
from skylink_care.orchestrator.routing import decide_route
from skylink_care.orchestrator.state import ContactState, Phase, Trigger


class Nodes(NetworkNodes):
    # --- control ---------------------------------------------------------------------------------

    async def drain_inbox(self, state: ContactState) -> dict[str, Any]:
        """Take every event newer than the cursor. The cursor commits with the checkpoint."""
        events = await self._rt.inbox.fetch_after(
            UUID(state["contact_id"]), state.get("inbox_cursor", 0)
        )
        if not events:
            return {"events": [], "phase": Phase.REPLIED.value, "steps": 0}
        return {
            "events": [
                {"seq": event.seq, "event_type": event.event_type, "payload": event.payload}
                for event in events
            ],
            "inbox_cursor": events[-1].seq,
            "turn": state.get("turn", 0) + 1,
            "steps": 0,
            "phase": Phase.NEW_TURN.value,
        }

    async def supervisor(self, state: ContactState) -> dict[str, Any]:
        decision = decide_route(state, self._limits)
        return {
            **decision.updates,
            "route": decision.route.value,
            "handoff_reason": decision.handoff_reason,
            "steps": state.get("steps", 0) + 1,
        }

    async def await_event(self, state: ContactState) -> dict[str, Any]:
        """Park the thread. Nothing may happen before the interrupt: this node re-runs on resume."""
        interrupt({"waiting_for": "inbox", "cursor": state.get("inbox_cursor", 0)})
        return {}

    async def finish(self, state: ContactState) -> dict[str, Any]:
        if state.get("phase") == Phase.CLOSED.value:
            await self._rt.contacts.update(
                UUID(state["contact_id"]), status="CLOSED", closed_at=self._rt.now()
            )
        return {}

    # --- triage and conversation -----------------------------------------------------------------

    async def triage(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        audit = self._rt.auditor(contact.contact_id, contact.correlation_id)
        text, message_cursor = await self._customer_text(contact.contact_id, state)
        previous = state.get("identity", {})
        outcome = await self._rt.triage.run(
            contact,
            text,
            audit,
            claim=Claim(**state.get("claim", {})),
            awaiting_digits=previous.get("status") in {status.value for status in AWAITING_DIGITS},
        )

        identity: dict[str, Any] = {
            **asdict(outcome.identity),
            "status": outcome.identity.status.value,
        }
        service = asdict(outcome.service) if outcome.service else None
        if (
            outcome.identity.status is not IdentityStatus.IDENTIFIED
            and previous.get("status") == IdentityStatus.IDENTIFIED.value
        ):
            # A follow-up message does not un-identify a customer who was already verified.
            identity, service = previous, state.get("service")

        intent: dict[str, Any] = {
            **outcome.reading.model_dump(),
            "summary": clean_summary(outcome.reading.summary),
            "degraded": outcome.call.degraded,
        }
        earlier = state.get("intent")
        if earlier and intent["intent"] == "other" and not intent["abusive_or_off_topic"]:
            # A short follow-up ("it is C0001234", "still the same") carries no topic of its
            # own. What the customer came about has not changed.
            intent = {
                **intent,
                "intent": earlier["intent"],
                "confidence": earlier["confidence"],
                "summary": earlier.get("summary", intent["summary"]),
            }

        identified = identity.get("status") == IdentityStatus.IDENTIFIED.value
        await self._rt.contacts.update(
            contact.contact_id,
            status="IN_PROGRESS",
            # Only a verified identity is attached to the contact; a claim never is.
            customer_id=identity.get("customer_id") if identified else None,
            service_id=service["service_id"] if service and identified else None,
            service_type=service["service_type"] if service and identified else None,
            intent=intent["intent"],
        )
        return {
            "identity": identity,
            "claim": asdict(outcome.claim),
            "service": service,
            "intent": intent,
            "message_cursor": message_cursor,
            "phase": Phase.TRIAGED.value,
        }

    async def ask_identity(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        status = (state.get("identity") or {}).get("status")
        if status == IdentityStatus.NEED_VERIFICATION.value:
            reply = replies.need_verification(contact.complaint_ref)
        elif status == IdentityStatus.VERIFICATION_FAILED.value:
            reply = replies.verification_failed(contact.complaint_ref)
        else:
            reply = replies.need_identifier(contact.complaint_ref)
        await self._say(contact, state, reply, author="agent:triage")
        armed = await self._arm_close(contact, state)
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        return {
            **armed,
            "phase": Phase.REPLIED.value,
            "identity_asks": state.get("identity_asks", 0) + 1,
        }

    async def out_of_scope(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        await self._say(
            contact, state, replies.out_of_scope(contact.complaint_ref), author="agent:triage"
        )
        if waits_on_network(state):
            # An off-topic remark changes nothing about what this customer is waiting for: the
            # feed and the re-check must still find them, and silence must not close them.
            await self._rt.contacts.update(contact.contact_id, status="AWAITING_NETWORK")
            return {"phase": Phase.REPLIED.value}
        armed = await self._arm_close(contact, state)
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        return {**armed, "phase": Phase.REPLIED.value}

    async def handoff(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        reason = state.get("handoff_reason") or "UNSPECIFIED"
        summary = handoff_summary(state, reason)
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=reason,
            summary=summary,
            priority=await self._priority(state, reason),
        )
        await self._say(
            contact,
            state,
            replies.handed_to_human(contact.complaint_ref, wording_for(reason)),
            author="agent:supervisor",
        )
        await self._rt.timers.cancel(contact.contact_id)
        await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor="agent:supervisor",
            step="handoff",
            outcome=reason,
            outputs={"summary": summary},
        )
        return {"phase": Phase.HANDED_OFF.value}

    async def close(self, state: ContactState) -> dict[str, Any]:
        """End the contact: the customer confirmed service is back, or stayed silent."""
        contact = await self._contact(state)
        confirmed = state.get("trigger") == Trigger.CUSTOMER.value
        reply = (
            replies.closed_confirmed(contact.complaint_ref)
            if confirmed
            else replies.closed_quiet(contact.complaint_ref)
        )
        await self._say(contact, state, reply, author="agent:supervisor")
        await self._rt.timers.cancel(contact.contact_id)
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor="agent:supervisor",
            step="contact.closed",
            outcome="CUSTOMER_CONFIRMED" if confirmed else "NO_REPLY",
        )
        return {"phase": Phase.CLOSED.value, "close_key": None}

    async def _customer_text(self, contact_id: UUID, state: ContactState) -> tuple[str, int]:
        """Everything the customer wrote since triage last read, and the new read position.

        Several messages can arrive before a run starts. Reading only the newest would miss an
        identifier or a request sent just before it.
        """
        cursor = state.get("message_cursor", 0)
        inbound = [
            message
            for message in await self._rt.messages.list_for_contact(contact_id, after_id=cursor)
            if message.direction == "inbound"
        ]
        if not inbound:
            return "", cursor
        return "\n".join(message.body for message in inbound), inbound[-1].message_id
