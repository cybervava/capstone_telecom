"""Workflow nodes: what each step of a contact's journey does.

Every node may run more than once for the same turn (after a crash the run restarts from the last
checkpoint), so every side effect here is idempotent: messages carry a per-turn dedupe key,
actions carry their identity key, episodes are unique per node. Nodes never call a write API;
they record proposals in the action ledger for the executor.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from skylink_care.agents import replies
from skylink_care.agents.replies import Reply
from skylink_care.agents.triage import AWAITING_DIGITS, Claim, IdentityStatus
from skylink_care.db.actions import ActionStatus, NewAction
from skylink_care.db.contacts import Contact
from skylink_care.domain.network import DecisionReason
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.orchestrator.evidence import decision_to_state, outage_from_state, parse_time
from skylink_care.orchestrator.routing import decide_route
from skylink_care.orchestrator.state import ContactState, Phase
from skylink_care.runtime import OrchestratorRuntime

INCIDENT_OPEN = "INCIDENT_OPEN"
INCIDENT_UPDATE = "INCIDENT_UPDATE"
INCIDENT_LINK_COMPLAINT = "INCIDENT_LINK_COMPLAINT"
INCIDENT_LANE = 2
LINK_LANE = 3
REPEAT_CONTACT_WINDOW = timedelta(days=7)
REPEAT_CONTACT_THRESHOLD = 2
MAX_SUMMARY_CHARS = 240

_HANDOFF_WORDING = {
    "CUSTOMER_REQUESTED_HUMAN": "You asked to speak to a person.",
    "BILLING": "Billing questions are handled by our billing team.",
    "CRM_UNAVAILABLE": "I couldn't reach our account system to verify your details.",
    "IDENTITY_UNRESOLVED": "I couldn't match the details you gave to an account.",
    "LOW_CONFIDENCE": "I want to be sure this is handled correctly.",
    "NO_SERVICE_ON_ACCOUNT": "I couldn't find a service on the account to check.",
    "TROUBLESHOOTING": "Your line needs to be checked.",
}
_DEFAULT_HANDOFF_WORDING = "This needs a member of our team."
_URGENT_REASONS = frozenset({"LOOP_GUARD", "WORKFLOW_ERROR", "UNKNOWN_PHASE"})


def clean_summary(text: str) -> str:
    """Make a model-written summary safe to store and to show to staff.

    The prompt asks for a neutral sentence without personal data, but a prompt is not a control:
    contact details are masked again here, markup and control characters are removed, and the
    length is capped.
    """
    redacted = str(redact_for_log(text))
    printable = "".join(char if char.isprintable() else " " for char in redacted)
    without_markup = printable.replace("<", "(").replace(">", ")")
    return " ".join(without_markup.split())[:MAX_SUMMARY_CHARS]


class Nodes:
    def __init__(self, runtime: OrchestratorRuntime) -> None:
        self._rt = runtime

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
        decision = decide_route(state)
        return {
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

    # --- specialists -----------------------------------------------------------------------------

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

    async def diagnose(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        service = state.get("service") or {}
        diagnosis = await self._rt.diagnostics.run(
            address_id=service["address_id"],
            service_type=service["service_type"],
            audit=self._rt.auditor(contact.contact_id, contact.correlation_id),
        )
        decision = diagnosis.decision
        await self._rt.contacts.update(
            contact.contact_id,
            node_id=decision.node_id,
            network_state=decision.state.value,
            outage_id=decision.outage.outage_id if decision.outage else None,
        )
        return {
            "mapping": {
                "status": diagnosis.mapping.status.value,
                "node_id": diagnosis.mapping.node_id,
                "candidates": list(diagnosis.mapping.candidate_node_ids),
                "map_version": diagnosis.mapping.map_version,
            },
            "network": decision_to_state(decision),
            "phase": Phase.DIAGNOSED.value,
        }

    async def outage_reply(self, state: ContactState) -> dict[str, Any]:
        """Tell the customer about the outage and tie the complaint to the node's one incident."""
        contact = await self._contact(state)
        network = state["network"]
        outage = outage_from_state(network["outage"])
        service = state.get("service") or {}
        if outage is None or not network.get("node_id"):
            raise RuntimeError("outage_reply reached without an active outage on a known node")

        joined = await self._rt.episodes.open_or_join(
            network["node_id"], service["service_type"], outage_id=outage.outage_id
        )
        episode = joined.episode
        opened = await self._rt.actions.propose(
            NewAction(
                action_key=f"incident:{episode.episode_id}",
                action_type=INCIDENT_OPEN,
                lane=INCIDENT_LANE,
                requested_by="agent:diagnostics",
                episode_id=episode.episode_id,
                correlation_id=contact.correlation_id,
                request_body={
                    "node_id": episode.node_id,
                    "service_type": episode.service_type,
                    "kind": "NETWORK_OUTAGE",
                    "outage_id": outage.outage_id,
                    "summary": f"Customer-reported impact on {episode.node_id}",
                },
            )
        )
        if joined.upgraded or opened.conflicting:
            # The ticket was opened as a suspected fault before the outage was declared.
            await self._rt.actions.propose(
                NewAction(
                    action_key=f"incident-update:{episode.episode_id}:{outage.outage_id}",
                    action_type=INCIDENT_UPDATE,
                    lane=INCIDENT_LANE,
                    requested_by="agent:diagnostics",
                    episode_id=episode.episode_id,
                    depends_on=opened.action.action_id,
                    correlation_id=contact.correlation_id,
                    request_body={"kind": "NETWORK_OUTAGE", "outage_id": outage.outage_id},
                )
            )
        await self._rt.actions.propose(
            NewAction(
                action_key=f"incident-link:{episode.episode_id}:{contact.complaint_ref}",
                action_type=INCIDENT_LINK_COMPLAINT,
                lane=LINK_LANE,
                requested_by="agent:diagnostics",
                contact_id=contact.contact_id,
                episode_id=episode.episode_id,
                depends_on=opened.action.action_id,
                correlation_id=contact.correlation_id,
                request_body={
                    "complaint_ref": contact.complaint_ref,
                    "contact_id": str(contact.contact_id),
                    "customer_id": (state.get("identity") or {}).get("customer_id"),
                },
            )
        )
        if opened.action.status in {ActionStatus.FAILED, ActionStatus.BLOCKED}:
            # The node's ticket could not be raised earlier. The customer is still told the
            # truth about the outage; a person is told the ticket is missing.
            await self._rt.handoffs.open(
                contact.contact_id,
                reason="INCIDENT_NOT_RAISED",
                summary=self._summary(state, "INCIDENT_NOT_RAISED"),
                priority=2,
            )
        reply = replies.outage_active(
            contact.complaint_ref, outage, evidence_at=parse_time(network.get("evidence_at"))
        )
        await self._say(contact, state, reply, author="agent:resolution")
        await self._rt.contacts.update(
            contact.contact_id, status="AWAITING_NETWORK", episode_id=episode.episode_id
        )
        return {"phase": Phase.REPLIED.value, "episode_id": str(episode.episode_id)}

    async def unknown_reply(self, state: ContactState) -> dict[str, Any]:
        """Degraded mode: say what is and is not known, and put the case in front of a person."""
        contact = await self._contact(state)
        network = state.get("network", {})
        reason = DecisionReason(network.get("reason", DecisionReason.NO_FRESH_EVIDENCE.value))
        reply = replies.network_unknown(
            contact.complaint_ref,
            reason,
            last_known_outage=outage_from_state(network.get("last_known_outage")),
            evidence_at=parse_time(network.get("evidence_at")),
        )
        await self._say(contact, state, reply, author="agent:diagnostics")
        handoff_reason = (
            "MAPPING_UNRESOLVED"
            if reason is DecisionReason.MAPPING_UNRESOLVED
            else "NETWORK_STATUS_UNVERIFIED"
        )
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=handoff_reason,
            summary=self._summary(state, handoff_reason),
            priority=await self._priority(state, handoff_reason),
        )
        if reason is DecisionReason.MAPPING_UNRESOLVED:
            await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
            return {"phase": Phase.HANDED_OFF.value}
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_NETWORK")
        return {"phase": Phase.REPLIED.value}

    async def clear_reply(self, state: ContactState) -> dict[str, Any]:
        """Confirmed clear. Line tests and guided troubleshooting attach here in the next slice."""
        contact = await self._contact(state)
        evidence_at = parse_time(state["network"].get("evidence_at")) or self._rt.now()
        reply = replies.no_outage_reported(contact.complaint_ref, evidence_at=evidence_at)
        await self._say(contact, state, reply, author="agent:diagnostics")
        await self._rt.handoffs.open(
            contact.contact_id,
            reason="TROUBLESHOOTING",
            summary=self._summary(state, "TROUBLESHOOTING"),
            priority=await self._priority(state, "TROUBLESHOOTING"),
        )
        await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
        return {"phase": Phase.HANDED_OFF.value}

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
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        return {
            "phase": Phase.REPLIED.value,
            "identity_asks": state.get("identity_asks", 0) + 1,
        }

    async def out_of_scope(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        await self._say(
            contact, state, replies.out_of_scope(contact.complaint_ref), author="agent:triage"
        )
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        return {"phase": Phase.REPLIED.value}

    async def handoff(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        reason = state.get("handoff_reason") or "UNSPECIFIED"
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=reason,
            summary=self._summary(state, reason),
            priority=await self._priority(state, reason),
        )
        wording = _HANDOFF_WORDING.get(reason, _DEFAULT_HANDOFF_WORDING)
        await self._say(
            contact,
            state,
            replies.handed_to_human(contact.complaint_ref, wording),
            author="agent:supervisor",
        )
        await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor="agent:supervisor",
            step="handoff",
            outcome=reason,
            outputs={"summary": self._summary(state, reason)},
        )
        return {"phase": Phase.HANDED_OFF.value}

    # --- helpers ---------------------------------------------------------------------------------

    async def _contact(self, state: ContactState) -> Contact:
        contact = await self._rt.contacts.get(UUID(state["contact_id"]))
        if contact is None:
            raise RuntimeError(f"contact {state['contact_id']} does not exist")
        return contact

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

    async def _say(
        self, contact: Contact, state: ContactState, reply: Reply, *, author: str
    ) -> None:
        """Store one outbound message for this turn. A re-run of the turn stores nothing new."""
        await self._rt.messages.add(
            contact.contact_id,
            direction="outbound",
            author=author,
            body=reply.text,
            dedupe_key=f"turn-{state.get('turn', 0)}:{reply.template_id}",
            meta={"template_id": reply.template_id, "facts": reply.facts},
        )

    async def _priority(self, state: ContactState, reason: str) -> int:
        """Queue priority. Repeat contact raises it; it never changes what the customer is owed."""
        if reason in _URGENT_REASONS:
            return 2
        customer_id = (state.get("identity") or {}).get("customer_id")
        if customer_id:
            recent = await self._rt.contacts.count_recent_for_customer(
                customer_id, since=self._rt.now() - REPEAT_CONTACT_WINDOW
            )
            if recent >= REPEAT_CONTACT_THRESHOLD:
                return 3
        return 5

    @staticmethod
    def _summary(state: ContactState, reason: str) -> str:
        """What a person needs to take over. Built from structured state: no free customer text."""
        intent = state.get("intent") or {}
        identity = state.get("identity") or {}
        network = state.get("network") or {}
        service = state.get("service") or {}
        outage = network.get("outage") or network.get("last_known_outage") or {}
        parts = [
            f"Reason: {reason}.",
            f"Request: {intent.get('summary', 'not read')} (intent {intent.get('intent', '?')},"
            f" confidence {intent.get('confidence', '?')}).",
            f"Identity: {identity.get('status', 'not attempted')}"
            f" via {identity.get('method') or 'n/a'},"
            f" {'verified' if identity.get('verified') else 'not verified'}.",
        ]
        if service:
            parts.append(
                f"Service: {service.get('service_id')} ({service.get('service_type')}),"
                f" router {service.get('router_model') or 'n/a'}."
            )
        if network:
            parts.append(
                f"Network: {network.get('state')} ({network.get('reason')}),"
                f" node {network.get('node_id') or 'unresolved'},"
                f" outage {outage.get('outage_id') or 'none'},"
                f" evidence at {network.get('evidence_at') or 'n/a'}."
            )
        flags = [
            name
            for name in ("insists_device_fault", "requests_technician", "requests_credit")
            if intent.get(name)
        ]
        if flags:
            parts.append(f"Customer flags: {', '.join(flags)}.")
        return " ".join(parts)
