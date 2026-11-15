"""Customer-facing messages, built from verified facts.

Every statement about the network comes from a template filled with structured evidence, never
from free generation. That is what makes "no false all-clear" a property of the code: the only
template that says the network looks healthy needs a CONFIRMED_CLEAR decision to be called, and
even it says "no outage has been reported", not "there is no outage".

Times are shown in Indian Standard Time and always say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import DecisionReason

IST = ZoneInfo("Asia/Kolkata")
AI_DISCLOSURE = "I'm SkyLink's automated assistant."

_CAUSES = {
    "FIBRE_CUT": "a fibre cut",
    "POWER_FAILURE": "a power failure at our network site",
    "EQUIPMENT_FAULT": "an equipment fault",
    "TOWER_FAULT": "a fault at the mobile tower",
    "PLANNED_MAINTENANCE": "planned maintenance",
}
_ETA_SOURCES = {
    "noc_estimate": "network operations centre estimate",
    "field_update": "update from the field team",
}


@dataclass(frozen=True)
class Reply:
    text: str
    template_id: str
    # The facts the text was built from, stored with the message for audit and for citation.
    facts: dict[str, Any] = field(default_factory=dict)


def clock(moment: datetime) -> str:
    """``14:05 IST on 15 Nov``"""
    local = moment.astimezone(IST)
    return f"{local:%H:%M} IST on {local.day} {local:%b}"


def cause_phrase(cause: str) -> str:
    """The cause in plain words. An unknown code is never shown to a customer."""
    return _CAUSES.get(cause, "a network fault")


def eta_source_phrase(eta_source: str | None) -> str:
    return _ETA_SOURCES.get(eta_source or "", "network team estimate")


def outage_active(complaint_ref: str, outage: OutageInfo, *, evidence_at: datetime | None) -> Reply:
    cause = cause_phrase(outage.cause)
    parts = [AI_DISCLOSURE]
    if outage.planned:
        parts.append(
            f"I've checked the network: your area is affected by planned maintenance"
            f" (outage {outage.outage_id}) that began at {clock(outage.started_at)}."
        )
    else:
        parts.append(
            f"I've checked the network: there is a confirmed outage in your area"
            f" (outage {outage.outage_id}) caused by {cause}. It began at"
            f" {clock(outage.started_at)} and our engineers are working on it."
        )
    if outage.eta is not None:
        source = eta_source_phrase(outage.eta_source)
        parts.append(f"Service is expected back by {clock(outage.eta)} ({source}).")
    else:
        parts.append("There is no restoration estimate yet; I will not guess one.")
    if evidence_at is not None:
        parts.append(f"This status was verified at {clock(evidence_at)}.")
    parts.append(
        "You don't need to restart or reset your router and no technician visit is needed:"
        " the fault is on our side."
    )
    parts.append(
        f"Your complaint reference is {complaint_ref}. It is recorded against this outage and"
        " I'll update you when service is restored."
    )
    return Reply(
        " ".join(parts),
        "outage_active.v1",
        {
            "outage_id": outage.outage_id,
            "outage_version": outage.version,
            "eta": outage.eta.isoformat() if outage.eta else None,
            "eta_source": outage.eta_source,
            "evidence_at": evidence_at.isoformat() if evidence_at else None,
        },
    )


def network_unknown(
    complaint_ref: str,
    reason: DecisionReason,
    *,
    last_known_outage: OutageInfo | None = None,
    evidence_at: datetime | None = None,
) -> Reply:
    """Degraded-mode message. It must never suggest that the network is healthy."""
    opening = f"{AI_DISCLOSURE} Your complaint is registered under reference {complaint_ref}."
    closing = (
        "Until the status is confirmed I won't ask you to change anything on your router or"
        " book a technician, because that could waste your time. We will update you as soon as"
        " we can verify it."
    )
    if reason is DecisionReason.MAPPING_UNRESOLVED:
        middle = (
            "I couldn't match your service address to our network records, so I can't check the"
            " network status for your line automatically. A specialist will check it and"
            " contact you."
        )
    elif reason is DecisionReason.SUSPECTED_NODE_FAULT:
        middle = (
            "We are seeing signs of a possible network problem in your area and our network"
            " team is checking. No outage has been declared yet."
        )
    elif reason is DecisionReason.STALE_LAST_KNOWN_OUTAGE and last_known_outage is not None:
        when = f" at {clock(evidence_at)}" if evidence_at else ""
        middle = (
            "I can't verify the live network status right now. The last verified"
            f" information{when} showed an outage in your area"
            f" (outage {last_known_outage.outage_id}). I can't confirm whether it has ended."
        )
    else:
        middle = (
            "I can't verify the live network status for your area right now, so I can't tell"
            " you whether there is an outage."
        )
    return Reply(
        f"{opening} {middle} {closing}",
        "network_unknown.v1",
        {
            "reason": reason.value,
            "last_known_outage_id": last_known_outage.outage_id if last_known_outage else None,
            "evidence_at": evidence_at.isoformat() if evidence_at else None,
        },
    )


def no_outage_reported(complaint_ref: str, *, evidence_at: datetime) -> Reply:
    """Only for a CONFIRMED_CLEAR decision. Says "none reported", never "there is none"."""
    return Reply(
        f"{AI_DISCLOSURE} I've checked the network: no outage has been reported for your area"
        f" as of {clock(evidence_at)}. Your complaint reference is {complaint_ref}."
        " I'm passing you to a support specialist to check your line.",
        "no_outage_reported.v1",
        {"evidence_at": evidence_at.isoformat()},
    )


def outage_restored(
    complaint_ref: str,
    *,
    outage_id: str | None,
    resolved_at: datetime | None,
    service_type: str | None,
) -> Reply:
    """The outage this customer was waiting on is over.

    It reports what the network team recorded, not that this customer's line works: only the
    customer can confirm that, so the message says what to do if it does not.
    """
    if outage_id and resolved_at:
        news = (
            f"Our network team has resolved the outage in your area (outage {outage_id})"
            f" at {clock(resolved_at)}."
        )
    elif outage_id:
        news = f"The outage in your area (outage {outage_id}) is no longer active on our network."
    else:
        news = "Our network checks no longer show a problem in your area."
    device = "phone" if service_type == "mobile" else "router"
    return Reply(
        f"{AI_DISCLOSURE} {news} Your service should be working again. If it isn't back within"
        f" 10 minutes, restart your {device} once. If it still doesn't work after that, reply"
        f" here and I'll have your line checked. Your complaint reference is {complaint_ref}.",
        "outage_restored.v1",
        {
            "outage_id": outage_id,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
        },
    )


def closed_confirmed(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Thank you for confirming. I've closed complaint {complaint_ref}."
        " If the problem comes back, contact us and quote this reference.",
        "closed_confirmed.v1",
    )


def closed_quiet(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} We haven't heard back from you, so complaint {complaint_ref} is now"
        " closed. If you still have a problem, contact us and quote this reference.",
        "closed_quiet.v1",
    )


def need_identifier(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Your complaint is registered under reference {complaint_ref}."
        " To check your service I need your SkyLink customer ID (it starts with C and has"
        " seven digits; it is printed on your bill).",
        "need_identifier.v1",
    )


def need_verification(complaint_ref: str) -> Reply:
    """Asked whether or not the customer ID exists, so the question itself reveals nothing."""
    return Reply(
        f"{AI_DISCLOSURE} Thank you. To confirm this is your account, please send the last"
        " four digits of the mobile number registered on it."
        f" Your reference is {complaint_ref}.",
        "need_verification.v1",
    )


def verification_failed(complaint_ref: str) -> Reply:
    """Same wording for an unknown customer ID and for wrong digits: no account oracle."""
    return Reply(
        f"{AI_DISCLOSURE} Those details don't match our records. Please check your customer"
        " ID and send it again together with the last four digits of the registered mobile"
        f" number. Your reference is {complaint_ref}.",
        "verification_failed.v1",
    )


def handed_to_human(complaint_ref: str, why: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Your complaint is registered under reference {complaint_ref}."
        f" {why} A member of our team will take over and will have the details of this"
        " conversation, so you won't need to repeat them.",
        "handed_to_human.v1",
    )


def out_of_scope(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} I can help with SkyLink broadband and mobile service problems,"
        " outages and related credits, and I can't help with other requests. If you have a"
        f" service problem, tell me what is happening. Your reference is {complaint_ref}.",
        "out_of_scope.v1",
    )
