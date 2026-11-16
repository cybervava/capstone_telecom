"""Workflow nodes that deal with the network: what is known about the customer's node and line,
what the customer is told about it, and the node's one incident ticket.

Every node may run more than once for the same turn, so every side effect is idempotent (see
``nodes.py``). None of them calls a write API.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from skylink_care.agents import replies
from skylink_care.agents.diagnostics import Subject
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
from skylink_care.db.line_tests import FAIL as LINE_FAILED
from skylink_care.db.line_tests import PASS as LINE_PASSED
from skylink_care.db.line_tests import LineTest
from skylink_care.db.timers import CLOSE_QUIET
from skylink_care.domain.models import NetworkState
from skylink_care.domain.network import DecisionReason
from skylink_care.orchestrator.credit_nodes import CreditNodes
from skylink_care.orchestrator.evidence import decision_to_state, outage_from_state, parse_time
from skylink_care.orchestrator.handover import handoff_summary
from skylink_care.orchestrator.routing import disputes_the_outage
from skylink_care.orchestrator.state import (
    VISIT_CHOSEN,
    VISIT_OFFERED,
    ContactState,
    Phase,
    Trigger,
    visit_status,
)
from skylink_care.orchestrator.visit_nodes import VisitNodes

# How much of what the customer wrote is used to find the guide section.
MAX_PROBLEM_CHARS = 2000
# A hold on a failed line belongs to the moment it was started in. Once the line passes, or the
# customer has been told about an outage or its end, a later fault starts a hold of its own.
_NO_LINE_HOLD: dict[str, Any] = {
    "line_hold_until": None,
    "line_hold_over": False,
    "line_fault_seen_at": None,
}
# The customer has just been told where the network stands: no re-check is armed, no restoration
# is owed, no line hold is running and the count of silent re-checks starts again.
_TOLD_THE_NEWS: dict[str, Any] = {
    "recheck_key": None,
    "resolved_outage_id": None,
    "network_rechecks": 0,
    "suspected_told": False,
    **_NO_LINE_HOLD,
}


def _line_fault_state(
    state: ContactState, network: NetworkState, line: LineTest | None, *, hold_over: bool
) -> dict[str, Any]:
    """What the contact keeps about its line fault after a diagnosis.

    A line that passes has no fault to hold for; the next failure starts afresh. And a visit
    that was on offer, or chosen, for a fault the evidence no longer shows is withdrawn.
    """
    kept: dict[str, Any] = (
        _NO_LINE_HOLD if line and line.result == LINE_PASSED else {"line_hold_over": hold_over}
    )
    still_a_line_fault = (
        network is NetworkState.CONFIRMED_CLEAR and line is not None and line.result == LINE_FAILED
    )
    if not still_a_line_fault and visit_status(state) in {VISIT_OFFERED, VISIT_CHOSEN}:
        return {**kept, "visit": None}
    return kept


_NEWS_FROM_THE_FEED = frozenset({Trigger.DECLARED.value, Trigger.RESOLVED.value})


class NetworkNodes(CreditNodes, VisitNodes):
    async def diagnose(self, state: ContactState) -> dict[str, Any]:
        contact = await self._contact(state)
        service = state.get("service") or {}
        hold_until = parse_time(state.get("line_hold_until"))
        hold_over = hold_until is not None and self._rt.now() >= hold_until
        diagnosis = await self._rt.diagnostics.run(
            Subject(
                contact_id=contact.contact_id,
                service_id=service["service_id"],
                service_type=service["service_type"],
                address_id=service["address_id"],
            ),
            audit=self._rt.auditor(contact.contact_id, contact.correlation_id),
            # What happens next rests on whether the fault is still there: test, do not recall.
            fresh_line_test=hold_over,
            # The feed has just said something about this node. The turn must see the node as
            # it is after that, not join a status read that was sent before it.
            own_status_read=state.get("trigger") in _NEWS_FROM_THE_FEED,
        )
        decision, line = diagnosis.decision, diagnosis.line_test
        hold = _line_fault_state(state, decision.state, line, hold_over=hold_over)
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
            "line_test": (
                {
                    "test_id": line.test_id,
                    "result": line.result,
                    "node_id": line.node_id,
                    "tested_at": line.tested_at.isoformat(),
                }
                if line
                else None
            ),
            **hold,
            "phase": Phase.DIAGNOSED.value,
        }

    async def outage_reply(self, state: ContactState) -> dict[str, Any]:
        """Tell the customer about the outage and tie the complaint to the node's one incident."""
        contact = await self._contact(state)
        network = state["network"]
        outage = outage_from_state(network["outage"])
        if outage is None or not network.get("node_id"):
            raise RuntimeError("outage_reply reached without an active outage on a known node")
        episode, already_over = await self._ticket_for(contact, state, outage.outage_id)
        # A customer who says the fault is their own equipment is told why nothing is arranged
        # for it while the outage lasts. Once: the second time a person takes it (routing).
        disputed = state.get("trigger") == Trigger.CUSTOMER.value and disputes_the_outage(state)
        reply = replies.outage_active(
            contact.complaint_ref,
            outage,
            evidence_at=parse_time(network.get("evidence_at")),
            service_type=(state.get("service") or {}).get("service_type"),
            disputed=disputed,
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
            "outage_disputed": outage.outage_id if disputed else state.get("outage_disputed"),
            "close_key": None,
            "asked_if_working": False,
        }

    async def _ticket_for(
        self, contact: Contact, state: ContactState, outage_id: str
    ) -> tuple[Episode, bool]:
        """The node's one incident for this outage, with this complaint on it. Returns the
        episode and whether the outage has turned out to be over already."""
        network, service = state["network"], state.get("service") or {}
        joined = await self._rt.episodes.open_or_join(
            network["node_id"], service["service_type"], outage_id=outage_id
        )
        episode = joined.episode
        opened = await self._open_ticket(contact, state, episode)
        if joined.upgraded or opened.conflicting:
            # The ticket was opened as a suspected fault before the outage was declared.
            await self._upgrade_ticket(contact, episode, opened, outage_id)
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
        return episode, already_over

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
            service_type=(state.get("service") or {}).get("service_type"),
        )
        await self._say(contact, state, reply, author="agent:diagnostics")
        if reason is DecisionReason.MAPPING_UNRESOLVED:
            return await self._hand_over_unmapped(contact, state)

        updates: dict[str, Any] = {"close_key": None, "asked_if_working": False}
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

    async def line_hold(self, state: ContactState) -> dict[str, Any]:
        """A failed line on a node that looks healthy: wait before anything is arranged.

        The NOC declares an outage minutes after lines start failing, and those minutes are when
        customers call. Sending a technician now would be the truck roll this platform exists to
        avoid, so the contact is watched until the hold is over. Meanwhile a declared outage or
        more failing lines on the node are picked up by the re-checks and change the answer.
        """
        contact = await self._contact(state)
        settings = self._rt.settings
        first = not state.get("line_hold_until")
        line = state.get("line_test") or {}
        # The hold has its own end; re-checks during it are not failures to verify the network.
        updates: dict[str, Any] = {
            "close_key": None,
            "network_rechecks": 0,
            "asked_if_working": False,
        }
        if first:
            until = self._rt.now() + timedelta(seconds=settings.dispatch_hold_seconds)
            updates["line_hold_until"] = until.isoformat()
            updates["line_fault_seen_at"] = line.get("tested_at")
        if first or state.get("trigger") == Trigger.CUSTOMER.value:
            reply = replies.line_fault_hold(
                contact.complaint_ref,
                evidence_at=parse_time(state["network"].get("evidence_at")) or self._rt.now(),
                tested_at=(
                    parse_time(line.get("tested_at"))
                    or parse_time(state.get("line_fault_seen_at"))
                    or self._rt.now()
                ),
                hold_minutes=math.ceil(settings.dispatch_hold_seconds / 60),
            )
            await self._say(contact, state, reply, author="agent:diagnostics")
        await self._rt.timers.cancel(contact.contact_id, CLOSE_QUIET)
        updates |= await self._arm_recheck(contact, state)
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_NETWORK")
        return {**updates, "phase": Phase.REPLIED.value}

    async def guide(self, state: ContactState) -> dict[str, Any]:
        """Clear node, passing line: the problem is most likely the equipment in the home.

        A customer who has just written is given the steps for their own router, once. On a
        turn the platform started itself (a re-check, a feed event) nobody has asked anything:
        the customer is told the checks are clear and asked whether they still need help, and
        gets the steps when they say so. Asking someone to restart their router out of the
        blue, from a conversation that may be hours old, is not help.
        """
        contact = await self._contact(state)
        line = state.get("line_test") or {}
        tested_at = parse_time(line.get("tested_at"))
        if tested_at is None:
            return await self.clear_reply(state)
        evidence_at = parse_time(state["network"].get("evidence_at")) or self._rt.now()
        steps_given = None
        if state.get("trigger") != Trigger.CUSTOMER.value:
            reply = replies.checks_clear(
                contact.complaint_ref, evidence_at=evidence_at, tested_at=tested_at
            )
        else:
            guidance = await self._rt.troubleshooting.guide(
                router_model=(state.get("service") or {}).get("router_model"),
                problem=await self._everything_the_customer_wrote(contact),
                audit=self._rt.auditor(contact.contact_id, contact.correlation_id),
            )
            if guidance is None:
                return await self.clear_reply(state)
            passage = guidance.passage
            steps_given = passage.citation
            reply = replies.guided_steps(
                contact.complaint_ref,
                guide_title=passage.title.split(":")[0],
                section_title=passage.title.split(":", 1)[-1].strip(),
                steps=passage.steps,
                citation=passage.citation,
                evidence_at=evidence_at,
                tested_at=tested_at,
            )
        await self._say(contact, state, reply, author="agent:troubleshooting")
        await self._rt.timers.cancel(contact.contact_id)
        armed = await self._arm_close(contact, state)
        await self._rt.contacts.update(contact.contact_id, status="AWAITING_CUSTOMER")
        return {
            **_TOLD_THE_NEWS,
            **armed,
            "phase": Phase.REPLIED.value,
            "asked_if_working": True,
            "guidance_told": steps_given or state.get("guidance_told"),
        }

    async def _everything_the_customer_wrote(self, contact: Contact) -> str:
        """The customer's side of the conversation, as the search query for the guide."""
        messages = await self._rt.messages.list_for_contact(contact.contact_id)
        written = "\n".join(m.body for m in messages if m.direction == "inbound")
        # The most recent words describe the problem best, and bounding the text bounds the work.
        return written[-MAX_PROBLEM_CHARS:]

    async def clear_reply(self, state: ContactState) -> dict[str, Any]:
        """Confirmed clear: what happens next depends on the customer's own line.

        A line that still fails after the hold needs a repair; one that passes points at the
        equipment in the home. Guided troubleshooting and dispatch attach here in later slices;
        until then a person takes over with the evidence in hand.
        """
        contact = await self._contact(state)
        evidence_at = parse_time(state["network"].get("evidence_at")) or self._rt.now()
        line = state.get("line_test") or {}
        tested_at = parse_time(line.get("tested_at"))
        reason = "TROUBLESHOOTING"
        if line.get("result") == LINE_FAILED and tested_at is not None:
            reason = "LINE_FAULT"
            reply = replies.line_fault(contact.complaint_ref, tested_at=tested_at)
        elif line.get("result") == LINE_PASSED and tested_at is not None:
            reply = replies.line_ok(
                contact.complaint_ref, evidence_at=evidence_at, tested_at=tested_at
            )
        else:
            reply = replies.no_outage_reported(
                contact.complaint_ref,
                evidence_at=evidence_at,
                service_type=(state.get("service") or {}).get("service_type"),
            )
        await self._say(contact, state, reply, author="agent:diagnostics")
        await self._rt.handoffs.open(
            contact.contact_id,
            reason=reason,
            summary=handoff_summary(state, reason),
            priority=await self._priority(state, reason),
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
        # A credit is assessed only for an outage the NOC has recorded as resolved: its amount
        # depends on when the outage ended, and that time must not be a guess.
        about_credit, credit = await self._offer_credit(contact, state, outage)
        reply = replies.outage_restored(
            contact.complaint_ref,
            outage_id=outage_id,
            resolved_at=resolved_at,
            service_type=(state.get("service") or {}).get("service_type"),
            about_credit=about_credit,
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
            **credit,
            "phase": Phase.REPLIED.value,
            "told_outage_id": None,
            "asked_if_working": True,
        }

    # --- the node's incident ticket -----------------------------------------------------------

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
