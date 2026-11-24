"""What an outgoing message may claim about the network, checked against the evidence.

Every statement about the network comes from a template, and each template is reached only
with the evidence it needs. That is the first lock, and it is in the routing. This is the
second, at the door: whatever path produced a message, it is stored only if what it claims is
backed by the network decision of its turn, made within the time a status stays evidence. A
decision from an earlier turn, or none, proves nothing now.

* "No outage has been reported", and any word of a technician being arranged, needs a
  ``CONFIRMED_CLEAR`` decision.
* "There is an outage in your area" needs an ``ACTIVE`` decision about that outage.
* "The outage is resolved, your service should work again" needs the network confirmed clear,
  or the one case in which the feed's own report of the resolution is all there is to go by
  and nothing contradicts it (``CLEAR_NOT_CONFIRMED``). A status that simply cannot be read
  does not back it.

A template declares its claim in ``CLAIMS``; one that is not declared is not sent. That table
is the control. The words are read as well, but only as a tripwire for a sentence copied from
one template into another: no list of phrases catches every way of saying the network is
fine. What keeps a template's wording from drifting is the test that pins every template's
text (``tests/unit/guardrails/test_template_wording.py``).

Checked here are the messages of a conversation. A proactive notification is built from the
feed event that caused it, only while that event is recent, and is sent by the executor
within its own time limit; it is not checked again against the network when it goes out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from skylink_care.agents.replies import Reply
from skylink_care.domain.models import NetworkState
from skylink_care.domain.network import DecisionReason

# A decision stamped slightly ahead of this process's clock is still this turn's decision.
MAX_CLOCK_SKEW = timedelta(seconds=5)


class Claim(StrEnum):
    NOTHING = "nothing"  # says nothing about the state of the network
    CLEAR = "clear"  # "no outage has been reported for your area"
    VISIT = "visit"  # a technician visit is offered, or is being arranged
    OUTAGE = "outage"  # "there is an outage in your area"
    RESTORED = "restored"  # "the outage is resolved; your service should work again"


_CLEAR = (
    "no_outage_reported.v1",
    "line_ok.v1",
    "guided_steps.v1",
    "checks_clear.v1",
    "line_fault_hold.v1",
    "line_fault.v1",
    "visit_offer.v1",
)
_NOTHING = (
    "network_unknown.v1",
    "handed_to_human.v1",
    "taken_over.v1",
    "dealt_with_by_staff.v1",
    "need_identifier.v1",
    "need_verification.v1",
    "verification_failed.v1",
    "out_of_scope.v1",
    "credit_applied.v1",
    "credit_already_there.v1",
    "credit_not_applied.v1",
    "credit_unconfirmed.v1",
    "closing_after_credit.v1",
    "closed_confirmed.v1",
    "closed_quiet.v1",
    "visit_booked.v1",
    "visit_already_booked.v1",
    "visit_declined.v1",
    "visit_not_booked.v1",
    "visit_still_being_booked.v1",
    "visit_being_arranged_elsewhere.v1",
    "visit_needs_a_person.v1",
)
CLAIMS: Mapping[str, Claim] = MappingProxyType(
    {
        **dict.fromkeys(_CLEAR, Claim.CLEAR),
        **dict.fromkeys(_NOTHING, Claim.NOTHING),
        # They speak of slots and of booking: only on a node that is proven clear.
        "visit_choice_unclear.v1": Claim.VISIT,
        "visit_being_booked.v1": Claim.VISIT,
        # The executor's answer about a slot can arrive long after the look at the network
        # the choice rested on. The reply lists what Field Service has free and promises
        # nothing: the next choice is booked only after a look of its own.
        "visit_slot_taken.v1": Claim.NOTHING,
        "outage_active.v1": Claim.OUTAGE,
        "outage_disputed.v1": Claim.OUTAGE,
        "outage_restored.v1": Claim.RESTORED,
    }
)

# A tripwire, not a net (see the module's docstring).
_SAYS_CLEAR = re.compile(
    r"no\s+(?:network\s+)?outages?\s+(?:(?:has|have)\s+been|was|were|are|is)?\s*reported"
    r"|there\s+(?:is|are)\s+no\s+(?:network\s+)?outages?"
    r"|all[\s-]clear|(?:working|operating|running)\s+normally"
    r"|network\s+is\s+(?:fine|ok(?:ay)?|up|healthy|working)"
    r"|no\s+(?:problems?|issues?|faults?)\s+(?:found\s+|detected\s+)?(?:on|with|in)"
    r"\s+(?:the|our)\s+network",
    re.IGNORECASE,
)
# The words that assert an outage. Not "whether there is an outage", which the degraded-mode
# message says precisely because it cannot tell.
_SAYS_OUTAGE = re.compile(
    r"confirmed\s+outage|affected\s+by\s+planned\s+maintenance", re.IGNORECASE
)


@dataclass(frozen=True)
class Violation:
    """Why a message may not be stored."""

    code: str
    template_id: str
    claim: str
    network_state: str | None


class UnbackedClaimError(RuntimeError):
    """A message claimed something about the network that the turn's evidence does not back.

    It was not stored. Running the turn again would find the same evidence, so the contact
    goes to a person at once. (A ``RuntimeError``, which the workflow does not run a step
    again for.)
    """

    def __init__(self, violation: Violation) -> None:
        super().__init__(
            f"{violation.code}: {violation.template_id} claims {violation.claim} with the"
            f" network {violation.network_state or 'not looked at'}"
        )
        self.violation = violation


def check(
    reply: Reply, network: Mapping[str, Any] | None, *, now: datetime, max_age: timedelta
) -> Violation | None:
    """What stops this message from being stored, or None when its claim is backed.

    ``network`` is the network decision of the turn, as the conversation state holds it.
    """
    declared = CLAIMS.get(reply.template_id)
    state = str(network.get("state")) if network else None
    if declared is None:
        return Violation("UNDECLARED_TEMPLATE", reply.template_id, "undeclared", state)
    claims = {declared}
    if _SAYS_CLEAR.search(reply.text):
        claims.add(Claim.CLEAR)
    if _SAYS_OUTAGE.search(reply.text):
        claims.add(Claim.OUTAGE)
    for claim in (Claim.CLEAR, Claim.VISIT, Claim.OUTAGE, Claim.RESTORED):
        if claim not in claims:
            continue
        code = unbacked(
            claim,
            network,
            now=now,
            max_age=max_age,
            outage_id=reply.facts.get("outage_id"),
            # The message names the time the NOC recorded the resolution, or has none.
            resolution_reported=bool(reply.facts.get("resolved_at")),
        )
        if code is not None:
            return Violation(code, reply.template_id, claim.value, state)
    return None


def unbacked(
    claim: Claim,
    network: Mapping[str, Any] | None,
    *,
    now: datetime,
    max_age: timedelta,
    outage_id: object = None,
    resolution_reported: bool = True,
) -> str | None:
    """Why the network decision does not back this claim, or None when it does.

    For a workflow step to ask before it acts on what it is about to say, as well as for the
    message itself. ``resolution_reported`` says whether the NOC's own report of the outage's
    end is to hand: without it "restored" rests on a confirmed clear network or on nothing.
    """
    if claim is Claim.NOTHING:
        return None
    known = network or {}
    state = known.get("state")
    fresh = _just_now(known.get("decided_at"), now, max_age)
    if claim in (Claim.CLEAR, Claim.VISIT):
        # Decided now, and on a status observed now: a decision may rest on an answer that
        # was already some seconds old, and the two ages must not add up.
        seen = _just_now(known.get("evidence_at"), now, max_age)
        backed = fresh and seen and state == NetworkState.CONFIRMED_CLEAR.value
        return None if backed else f"{claim.name}_NOT_PROVEN"
    if claim is Claim.OUTAGE:
        proven = (known.get("outage") or {}).get("outage_id")
        same = outage_id is None or proven is None or outage_id == proven
        backed = fresh and state == NetworkState.ACTIVE.value and same
        return None if backed else "OUTAGE_NOT_PROVEN"
    # RESTORED: the network confirmed clear, or the feed's report of the resolution with
    # nothing against it. Not a status that could simply not be read.
    reported = (
        resolution_reported
        and state == NetworkState.UNKNOWN.value
        and known.get("reason") == DecisionReason.CLEAR_NOT_CONFIRMED.value
    )
    backed = fresh and (state == NetworkState.CONFIRMED_CLEAR.value or reported)
    return None if backed else "RESTORED_NOT_PROVEN"


def network_state_to_show(
    state: str | None, checked_at: datetime | None, *, now: datetime, max_age: timedelta
) -> str | None:
    """What a page may show of the last look at the network.

    "None reported" on a page is a claim like the message that says it: it is shown only
    while the look is recent, and after that the state reads as not verified. An outage, or a
    status that could not be verified, is shown as last found with its time: neither tells
    the customer that all is well.
    """
    if state != NetworkState.CONFIRMED_CLEAR.value:
        return state
    recent = checked_at is not None and -MAX_CLOCK_SKEW <= now - checked_at <= max_age
    return state if recent else NetworkState.UNKNOWN.value


def _just_now(stamp: object, now: datetime, max_age: timedelta) -> bool:
    moment = _moment(stamp)
    if moment is None:
        return False
    return -MAX_CLOCK_SKEW <= now - moment <= max_age


def _moment(value: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None
