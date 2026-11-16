"""What every workflow node shares: the runtime, the contact, the way a message is stored, the
timers a turn may arm and the priority a handoff gets."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any
from uuid import UUID

from skylink_care.agents.replies import Reply
from skylink_care.config import Settings
from skylink_care.db.contacts import Contact
from skylink_care.db.timers import CLOSE_QUIET, RECHECK_NETWORK
from skylink_care.guardrails.claims import Claim
from skylink_care.orchestrator.outgoing import must_be_backed, store_reply
from skylink_care.orchestrator.routing import RoutingLimits
from skylink_care.orchestrator.state import ContactState
from skylink_care.runtime import OrchestratorRuntime

REPEAT_CONTACT_WINDOW = timedelta(days=7)
REPEAT_CONTACT_THRESHOLD = 2
_URGENT_REASONS = frozenset({"LOOP_GUARD", "WORKFLOW_ERROR", "UNKNOWN_PHASE"})


def waits_on_network(state: ContactState) -> bool:
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


class NodeSupport:
    def __init__(self, runtime: OrchestratorRuntime) -> None:
        self._rt = runtime
        self._limits = routing_limits(runtime.settings)

    async def _contact(self, state: ContactState) -> Contact:
        contact = await self._rt.contacts.get(UUID(state["contact_id"]))
        if contact is None:
            raise RuntimeError(f"contact {state['contact_id']} does not exist")
        return contact

    async def _say(
        self, contact: Contact, state: ContactState, reply: Reply, *, author: str
    ) -> None:
        """Store one outbound message for this turn, if the network decision of the turn backs
        what it claims. A re-run of the turn stores nothing new."""
        await store_reply(
            self._rt,
            contact,
            reply,
            author=author,
            dedupe_key=f"turn-{state.get('turn', 0)}:{reply.template_id}",
            network=state.get("network"),
        )

    async def _must_be_backed(
        self,
        contact: Contact,
        state: ContactState,
        claim: Claim,
        *,
        template_id: str,
        outage_id: str | None = None,
        resolution_reported: bool = True,
    ) -> None:
        """Stop the step before it does anything, if the network decision of the turn does not
        back what it is about to tell the customer."""
        await must_be_backed(
            self._rt,
            contact,
            claim,
            template_id=template_id,
            network=state.get("network"),
            outage_id=outage_id,
            resolution_reported=resolution_reported,
        )

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
