"""Workflow nodes: what each step of a contact's journey does.

Every node may run more than once for the same turn (after a crash the run restarts from the last
checkpoint), so every side effect here is idempotent: messages carry a per-turn dedupe key,
actions carry their identity key, episodes are unique per node, timers carry a per-turn key.
Nodes never call a write API; they record proposals in the action ledger for the executor.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from skylink_care.agents import replies
from skylink_care.agents.replies import Reply
from skylink_care.agents.triage import AWAITING_DIGITS, Claim, IdentityStatus
from skylink_care.config import Settings
from skylink_care.db.actions import (
    INCIDENT_LANE,
    INCIDENT_LINK_COMPLAINT,
    INCIDENT_OPEN,
    INCIDENT_UPDATE,
    LINK_LANE,
    ActionStatus,
    NewAction,
    Proposal,
)
from skylink_care.db.contacts import Contact
from skylink_care.db.episodes import Episode
from skylink_care.db.timers import CLOSE_QUIET, RECHECK_NETWORK
from skylink_care.domain.models import NetworkState
from skylink_care.domain.network import DecisionReason
from skylink_care.orchestrator.evidence import decision_to_state, outage_from_state, parse_time
from skylink_care.orchestrator.handover import clean_summary, handoff_summary, wording_for
from skylink_care.orchestrator.routing import RoutingLimits, decide_route
from skylink_care.orchestrator.state import ContactState, Phase, Trigger
from skylink_care.runtime import OrchestratorRuntime

REPEAT_CONTACT_WINDOW = timedelta(days=7)
REPEAT_CONTACT_THRESHOLD = 2
_URGENT_REASONS = frozenset({"LOOP_GUARD", "WORKFLOW_ERROR", "UNKNOWN_PHASE"})
# The customer has just been told where the network stands: no re-check is armed, no restoration
# is owed and the count of silent re-checks starts again.
_TOLD_THE_NEWS: dict[str, Any] = {
    "recheck_key": None,
    "resolved_outage_id": None,
    "network_rechecks": 0,
    "suspected_told": False,
}


def _waits_on_network(state: ContactState) -> bool:
    """The customer has been told about the network and something is still owed or watched."""
    return bool(
        state.get("told_outage_id") or state.get("resolved_outage_id") or state.get("recheck_key")
    )


def routing_limits(settings: Settings) -> RoutingLimits:
    return RoutingLimits(
        unverified_rechecks=settings.network_recheck_limit,
        suspected_rechecks=max(
            1, math.ceil(settings.suspected_wait_seconds / settings.network_recheck_seconds)
        ),
    )


class Nodes:
    def __init__(self, runtime: OrchestratorRuntime) -> None:
        self._rt = runtime
        self._limits = routing_limits(runtime.settings)

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
        if _waits_on_network(state):
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

    # --- network ---------------------------------------------------------------------------------

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
        opened = await self._open_ticket(contact, state, episode)
        if joined.upgraded or opened.conflicting:
            # The ticket was opened as a suspected fault before the outage was declared.
            await self._upgrade_ticket(contact, episode, opened, outage.outage_id)
        already_over = await self._settle_ticket(contact, episode, opened)
        if opened.action.status in {ActionStatus.FAILED, ActionStatus.BLOCKED}:
            # The node's ticket could not be raised earlier. The customer is still told the
            # truth about the outage; a person is told the ticket is missing.
            await self._rt.handoffs.open(
                contact.contact_id,
                reason="INCIDENT_NOT_RAISED",
                summary=handoff_summary(state, "INCIDENT_NOT_RAISED"),
                priority=2,
            )
        reply = replies.outage_active(
            contact.complaint_ref, outage, evidence_at=parse_time(network.get("evidence_at"))
        )
        await self._say(contact, state, reply, author="agent:resolution")
        # The end of the outage normally arrives as a feed event. The live status is watched as
        # well: slowly while all is as expected, and soon when the NOC already reports this
        # outage resolved (the reply above then rests on a status that is about to change). A
        # lost or overtaken event can therefore never leave the customer waiting for good.
        resolving = already_over or state.get("resolved_outage_id") == outage.outage_id
        settings = self._rt.settings
        await self._rt.timers.cancel(contact.contact_id)
        armed = await self._arm_recheck(
            contact,
            state,
            seconds=(
                settings.network_recheck_seconds if resolving else settings.outage_recheck_seconds
            ),
            replace=True,
        )
        await self._rt.contacts.update(
            contact.contact_id, status="AWAITING_NETWORK", episode_id=episode.episode_id
        )
        return {
            **_TOLD_THE_NEWS,
            **armed,
            "phase": Phase.REPLIED.value,
            "episode_id": str(episode.episode_id),
            "told_outage_id": outage.outage_id,
            "resolved_outage_id": outage.outage_id if resolving else None,
            "close_key": None,
            "restoration_told": False,
        }

    async def unknown_reply(self, state: ContactState) -> dict[str, Any]:
        """Say what is and is not known. Never an all-clear, and never device steps.

        An unresolved mapping needs a person now. Anything else may settle by itself (the NOC
        declares the outage, the API comes back), so the contact waits and is looked at again;
        ``keep_waiting`` and the re-check limit decide when a person takes over instead.
        """
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
        if reason is DecisionReason.MAPPING_UNRESOLVED:
            return await self._hand_over_unmapped(contact, state)

        updates: dict[str, Any] = {"close_key": None}
        changes: dict[str, Any] = {"status": "AWAITING_NETWORK"}
        if reason is DecisionReason.SUSPECTED_NODE_FAULT and network.get("node_id"):
            episode = await self._suspect_node(contact, state)
            updates |= {"episode_id": str(episode.episode_id), "suspected_told": True}
            changes["episode_id"] = episode.episode_id
            if not state.get("suspected_told"):
                updates["network_rechecks"] = 0  # the longer wait for the NOC starts now
        await self._rt.timers.cancel(contact.contact_id, CLOSE_QUIET)
        updates |= await self._arm_recheck(contact, state)
        await self._rt.contacts.update(contact.contact_id, **changes)
        return {**updates, "phase": Phase.REPLIED.value}

    async def keep_waiting(self, state: ContactState) -> dict[str, Any]:
        """Nothing new to tell the customer: make sure the next look at the network is booked.

        While the outage they know about is simply still active, the look is a slow safety net
        behind the feed. In every other case the status is unverified or the sources disagree,
        and it is looked at again soon.
        """
        contact = await self._contact(state)
        settings = self._rt.settings
        steady = state.get("network", {}).get(
            "state"
        ) == NetworkState.ACTIVE.value and not state.get("resolved_outage_id")
        seconds = settings.outage_recheck_seconds if steady else settings.network_recheck_seconds
        armed = await self._arm_recheck(contact, state, seconds=seconds)
        return {**armed, "phase": Phase.REPLIED.value}

    async def clear_reply(self, state: ContactState) -> dict[str, Any]:
        """Confirmed clear. Line tests and guided troubleshooting attach here in the next slice."""
        contact = await self._contact(state)
        evidence_at = parse_time(state["network"].get("evidence_at")) or self._rt.now()
        reply = replies.no_outage_reported(contact.complaint_ref, evidence_at=evidence_at)
        await self._say(contact, state, reply, author="agent:diagnostics")
        await self._rt.handoffs.open(
            contact.contact_id,
            reason="TROUBLESHOOTING",
            summary=handoff_summary(state, "TROUBLESHOOTING"),
            priority=await self._priority(state, "TROUBLESHOOTING"),
        )
        await self._rt.timers.cancel(contact.contact_id)
        await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
        return {"phase": Phase.HANDED_OFF.value}

    async def restored(self, state: ContactState) -> dict[str, Any]:
        """The outage this customer waited on is over: say so and ask them to confirm."""
        contact = await self._contact(state)
        network = state.get("network", {})
        outage_id = state.get("resolved_outage_id") or state.get("told_outage_id")
        outage = await self._rt.read_model.get_outage(outage_id) if outage_id else None
        resolved_at = outage.resolved_at if outage else None
        reply = replies.outage_restored(
            contact.complaint_ref,
            outage_id=outage_id,
            resolved_at=resolved_at,
            service_type=(state.get("service") or {}).get("service_type"),
        )
        await self._say(contact, state, reply, author="agent:resolution")
        await self._rt.timers.cancel(contact.contact_id)
        armed = await self._arm_close(contact, state)
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor="agent:resolution",
            step="restoration.told",
            outcome="OK",
            inputs={"network_state": network.get("state"), "reason": network.get("reason")},
            outputs={
                "outage_id": outage_id,
                "resolved_at": resolved_at.isoformat() if resolved_at else None,
            },
        )
        return {
            **_TOLD_THE_NEWS,
            **armed,
            "phase": Phase.REPLIED.value,
            "told_outage_id": None,
            "restoration_told": True,
        }

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

    async def _open_ticket(
        self, contact: Contact, state: ContactState, episode: Episode
    ) -> Proposal:
        """One incident per episode, whoever asks first, with this complaint linked to it."""
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
                    "kind": episode.kind.value,
                    "outage_id": episode.outage_id,
                    "summary": f"Customer-reported impact on {episode.node_id}",
                },
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
        return opened

    async def _suspect_node(self, contact: Contact, state: ContactState) -> Episode:
        """Evidence of a node fault with no outage declared yet: raise it once for the node.

        Every customer who reports in meanwhile is linked to the same ticket, so the NOC sees
        the reports gather on the node instead of each one landing in a human queue. When the
        outage is declared, the same ticket is upgraded rather than a second one opened.
        """
        service = state.get("service") or {}
        joined = await self._rt.episodes.open_or_join(
            state["network"]["node_id"], service["service_type"], outage_id=None
        )
        opened = await self._open_ticket(contact, state, joined.episode)
        await self._settle_ticket(contact, joined.episode, opened)
        return joined.episode

    async def _upgrade_ticket(
        self, contact: Contact, episode: Episode, opened: Proposal, outage_id: str
    ) -> None:
        """The ticket was raised on suspicion: tell it which declared outage it belongs to."""
        await self._rt.actions.propose(
            NewAction(
                action_key=f"incident-update:{episode.episode_id}:{outage_id}",
                action_type=INCIDENT_UPDATE,
                lane=INCIDENT_LANE,
                requested_by="agent:diagnostics",
                episode_id=episode.episode_id,
                depends_on=opened.action.action_id,
                correlation_id=contact.correlation_id,
                request_body={"kind": "NETWORK_OUTAGE", "outage_id": outage_id},
            )
        )

    async def _settle_ticket(self, contact: Contact, episode: Episode, opened: Proposal) -> bool:
        """Bring the ticket in line with what happened while this turn was running.

        Between the moment this turn read the episode and the moment its ticket was proposed,
        the feed consumer may have upgraded or closed the episode, and it could not change a
        ticket that did not exist yet. Whatever it would have proposed is proposed here, under
        the same keys. Returns True when the episode's outage is already over.
        """
        current = await self._rt.episodes.get(episode.episode_id) or episode
        if current.outage_id and current.outage_id != episode.outage_id:
            await self._upgrade_ticket(contact, current, opened, current.outage_id)
        over = False
        if current.outage_id:
            known = await self._rt.read_model.get_outage(current.outage_id)
            over = known is not None and not known.is_open
            if over:
                await self._rt.episodes.close_for_outage(current.outage_id)
        if over or current.closed_at is not None:
            await self._rt.actions.propose(
                NewAction(
                    action_key=f"incident-resolve:{episode.episode_id}",
                    action_type=INCIDENT_UPDATE,
                    lane=INCIDENT_LANE,
                    requested_by="agent:diagnostics",
                    episode_id=episode.episode_id,
                    depends_on=opened.action.action_id,
                    correlation_id=contact.correlation_id,
                    request_body={"status": "RESOLVED"},
                )
            )
        return over

    async def _hand_over_unmapped(self, contact: Contact, state: ContactState) -> dict[str, Any]:
        """No node could be found for the address: nothing will settle this without a person."""
        reason = "MAPPING_UNRESOLVED"
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=reason,
            summary=handoff_summary(state, reason),
            priority=await self._priority(state, reason),
        )
        await self._rt.timers.cancel(contact.contact_id)
        await self._rt.contacts.update(contact.contact_id, status="HANDOFF")
        return {"phase": Phase.HANDED_OFF.value}

    async def _arm_recheck(
        self,
        contact: Contact,
        state: ContactState,
        *,
        seconds: int | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Book the next look at the network, unless one is already booked."""
        if state.get("recheck_key") and not replace:
            return {}
        wait = seconds if seconds is not None else self._rt.settings.network_recheck_seconds
        return {"recheck_key": await self._arm(contact, state, RECHECK_NETWORK, wait)}

    async def _arm_close(self, contact: Contact, state: ContactState) -> dict[str, Any]:
        """The turn ends waiting for the customer: book the quiet close, replacing any earlier."""
        await self._rt.timers.cancel(contact.contact_id, CLOSE_QUIET)
        key = await self._arm(contact, state, CLOSE_QUIET, self._rt.settings.close_quiet_seconds)
        return {"close_key": key}

    async def _arm(self, contact: Contact, state: ContactState, kind: str, seconds: int) -> str:
        """Arm one timer for this turn and return its key.

        The key names the turn and the inbox position the turn started from, so a repeat of the
        same turn arms the same timer, while a retry that has since received more events (among
        them, possibly, this timer firing) arms a new one.
        """
        key = f"{kind}:{state.get('turn', 0)}:{state.get('inbox_cursor', 0)}"
        armed = await self._rt.timers.schedule(
            contact.contact_id,
            kind,
            fire_at=self._rt.now() + timedelta(seconds=seconds),
            dedupe_key=key,
        )
        if not armed:
            # Carrying on would leave the contact waiting on a timer that will never come.
            # Failing sends the run to the retry path, which a person hears about if it persists.
            raise RuntimeError(f"timer {key} has already fired")
        return key

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
