"""Customer-facing messages, built from verified facts.

Every statement about the network comes from a template filled with structured evidence, never
from free generation. That is what makes "no false all-clear" a property of the code: the only
template that says the network looks healthy needs a CONFIRMED_CLEAR decision to be called, and
even it says "no outage has been reported", not "there is no outage".

Times are shown in Indian Standard Time and always say so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from skylink_care.clock import IST
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import DecisionReason

AI_DISCLOSURE = "I'm SkyLink's automated assistant."
# The template of the message that carries guide steps: sent once in a conversation.
GUIDED_STEPS = "guided_steps.v1"

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


def outage_active(
    complaint_ref: str,
    outage: OutageInfo,
    *,
    evidence_at: datetime | None,
    service_type: str | None = None,
    disputed: bool = False,
) -> Reply:
    """The outage, with its evidence. ``disputed`` is for a customer who says the fault is
    their own equipment, or asks for a technician: they are told why neither is arranged
    while the outage lasts, and what happens when it is over."""
    cause = cause_phrase(outage.cause)
    mobile = service_type == "mobile"
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
    parts.append(_about_their_equipment(mobile=mobile, disputed=disputed))
    parts.append(
        f"Your complaint reference is {complaint_ref}. It is recorded against this outage and"
        " I'll update you when service is restored."
    )
    return Reply(
        " ".join(parts),
        "outage_disputed.v1" if disputed else "outage_active.v1",
        {
            "outage_id": outage.outage_id,
            "outage_version": outage.version,
            "eta": outage.eta.isoformat() if outage.eta else None,
            "eta_source": outage.eta_source,
            "evidence_at": evidence_at.isoformat() if evidence_at else None,
        },
    )


def _about_their_equipment(*, mobile: bool, disputed: bool) -> str:
    """During an outage nothing is to be done to the customer's own equipment. Said plainly,
    and at more length to a customer who believes otherwise."""
    if not disputed:
        if mobile:
            return (
                "You don't need to restart your phone or change its settings: the fault is on"
                " our side."
            )
        return (
            "You don't need to restart or reset your router and no technician visit is needed:"
            " the fault is on our side."
        )
    if mobile:
        return (
            "I understand you believe the fault is with your own phone. Restarting it,"
            " replacing it or changing its settings would not bring your service back while"
            " this outage is active, so there is nothing for you to do on it now. When the"
            " outage is resolved I'll tell you here, and if your service does not return then,"
            " I'll look at your connection straight away. If you would rather speak to a person"
            ' now, reply "agent".'
        )
    return (
        "I understand you believe the fault is with your own equipment. Restarting or"
        " replacing your router, or a technician visit, would not bring your service back"
        " while this outage is active, so I am not arranging any of them now. When the outage"
        " is resolved I'll tell you here, and if your service does not return then, I'll test"
        ' your line straight away. If you would rather speak to a person now, reply "agent".'
    )


def network_unknown(
    complaint_ref: str,
    reason: DecisionReason,
    *,
    last_known_outage: OutageInfo | None = None,
    evidence_at: datetime | None = None,
    service_type: str | None = None,
) -> Reply:
    """Degraded-mode message. It must never suggest that the network is healthy."""
    mobile = service_type == "mobile"
    opening = f"{AI_DISCLOSURE} Your complaint is registered under reference {complaint_ref}."
    nothing_asked = (
        "ask you to change anything on your phone"
        if mobile
        else "ask you to change anything on your router or book a technician"
    )
    closing = (
        f"Until the status is confirmed I won't {nothing_asked}, because that could waste your"
        " time. We will update you as soon as we can verify it."
    )
    if reason is DecisionReason.MAPPING_UNRESOLVED:
        subject, checked = (
            ("mobile service", "connection") if mobile else ("service address", "line")
        )
        middle = (
            f"I couldn't match your {subject} to our network records, so I can't check the"
            f" network status for your {checked} automatically. A specialist will check it and"
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


def no_outage_reported(
    complaint_ref: str, *, evidence_at: datetime, service_type: str | None = None
) -> Reply:
    """Only for a CONFIRMED_CLEAR decision. Says "none reported", never "there is none"."""
    checked = "connection" if service_type == "mobile" else "line"
    return Reply(
        f"{AI_DISCLOSURE} I've checked the network: no outage has been reported for your area"
        f" as of {clock(evidence_at)}. Your complaint reference is {complaint_ref}."
        f" I'm passing you to a support specialist to check your {checked}.",
        "no_outage_reported.v1",
        {"evidence_at": evidence_at.isoformat()},
    )


def line_ok(complaint_ref: str, *, evidence_at: datetime, tested_at: datetime) -> Reply:
    """CONFIRMED_CLEAR and the customer's own line passed its remote test."""
    return Reply(
        f"{AI_DISCLOSURE} I've checked the network: no outage has been reported for your area"
        f" as of {clock(evidence_at)}, and a remote test of your line at {clock(tested_at)}"
        " passed. I'm passing you to a support specialist to look at your equipment with you."
        f" Your complaint reference is {complaint_ref}.",
        "line_ok.v1",
        {"evidence_at": evidence_at.isoformat(), "line_tested_at": tested_at.isoformat()},
    )


def guided_steps(
    complaint_ref: str,
    *,
    guide_title: str,
    section_title: str,
    steps: Sequence[str],
    citation: str,
    evidence_at: datetime,
    tested_at: datetime,
    customer_words: str | None = None,
    guide_words: str | None = None,
    chosen_by: str = "rules",
) -> Reply:
    """Steps quoted from the guide for the customer's own router, with where they come from.

    ``customer_words`` and ``guide_words`` are passages a language model pointed to: the
    customer's own description of the fault, and the sentence of the section that says what
    it is for. Both are quotations, found word for word in their sources before they come
    here (``guardrails.grounding``); nothing a model wrote is in this message. ``chosen_by``
    says who chose the section among those the search found.
    """
    numbered = "\n".join(f"{number}. {step}" for number, step in enumerate(steps, start=1))
    understood = f' You told me: "{customer_words}".' if customer_words else ""
    if guide_words:
        understood += f' The guide for your router says: "{guide_words}".'
    return Reply(
        f"{AI_DISCLOSURE} I've checked the network: no outage has been reported for your area"
        f" as of {clock(evidence_at)}, and a remote test of your line at {clock(tested_at)}"
        f" passed, so the problem is most likely in the equipment at your home.{understood}"
        f' Please try these steps from the {guide_title}, section "{section_title}":\n'
        f"{numbered}\n"
        f"Source: {citation}.\n"
        "Reply here to tell me whether that fixed it; if it did not, a specialist will take"
        f" over. Your complaint reference is {complaint_ref}.",
        GUIDED_STEPS,
        {
            "citation": citation,
            "steps": len(steps),
            "section_chosen_by": chosen_by,
            "evidence_at": evidence_at.isoformat(),
            "line_tested_at": tested_at.isoformat(),
        },
    )


def checks_clear(complaint_ref: str, *, evidence_at: datetime, tested_at: datetime) -> Reply:
    """News nobody asked for just now: the checks are clear. Ask, do not instruct."""
    return Reply(
        f"{AI_DISCLOSURE} An update on your complaint {complaint_ref}: no outage has been"
        f" reported for your area as of {clock(evidence_at)}, and a remote test of your line at"
        f" {clock(tested_at)} passed. If your service is still not working, reply here and I'll"
        " take you through some checks for your router.",
        "checks_clear.v1",
        {"evidence_at": evidence_at.isoformat(), "line_tested_at": tested_at.isoformat()},
    )


def line_fault_hold(
    complaint_ref: str, *, evidence_at: datetime, tested_at: datetime, hold_minutes: int
) -> Reply:
    """A failed line on a node that looks healthy. It may be the first sign of a node fault, so
    nothing is arranged until that has been ruled out."""
    return Reply(
        f"{AI_DISCLOSURE} I've checked the network: no outage has been reported for your area"
        f" as of {clock(evidence_at)}. A remote test of your line at {clock(tested_at)} found a"
        " fault. Before a repair is arranged I'm checking whether it is part of a wider network"
        f" problem, which can take up to {hold_minutes} minute{'' if hold_minutes == 1 else 's'}."
        " I'll update you here; you don't"
        f" need to do anything meanwhile. Your complaint reference is {complaint_ref}.",
        "line_fault_hold.v1",
        {
            "evidence_at": evidence_at.isoformat(),
            "line_tested_at": tested_at.isoformat(),
            "hold_minutes": hold_minutes,
        },
    )


def line_fault(complaint_ref: str, *, tested_at: datetime) -> Reply:
    """The line still fails after the wait and the node is confirmed clear: it needs a repair."""
    return Reply(
        f"{AI_DISCLOSURE} The fault on your line is still there (tested again at"
        f" {clock(tested_at)}) and no network outage has been reported for your area, so the"
        " line itself needs a repair. I'm passing you to a specialist to arrange it."
        f" Your complaint reference is {complaint_ref}.",
        "line_fault.v1",
        {"line_tested_at": tested_at.isoformat()},
    )


def outage_restored(
    complaint_ref: str,
    *,
    outage_id: str | None,
    resolved_at: datetime | None,
    service_type: str | None,
    about_credit: str = "",
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
    device, checked = ("phone", "connection") if service_type == "mobile" else ("router", "line")
    return Reply(
        f"{AI_DISCLOSURE} {news} Your service should be working again. If it isn't back within"
        f" 10 minutes, restart your {device} once. If it still doesn't work after that, reply"
        f" here and I'll have your {checked} checked.{about_credit}"
        f" Your complaint reference is {complaint_ref}.",
        "outage_restored.v1",
        {
            "outage_id": outage_id,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
        },
    )


def credit_line(amount_usd: str, *, policy_version: str, needs_approval: bool) -> str:
    """One sentence about the credit, added to the message that reports the restoration."""
    if needs_approval:
        return (
            f" For this outage you are due a service credit of ${amount_usd} under our"
            f" service credit policy ({policy_version}). It has been sent for approval and I'll"
            " confirm here once it has been applied to your account."
        )
    return (
        f" For this outage you are due a service credit of ${amount_usd} under our service"
        f" credit policy ({policy_version}). It is being applied to your account and I'll"
        " confirm here once that is done."
    )


def no_credit_line(reason: str, *, policy_version: str) -> str:
    """Why no credit is due, for a customer who asked. Stated as policy, not as a judgement."""
    wording = {
        "BELOW_MINIMUM_DURATION": "the outage was shorter than the minimum the policy covers",
        "PLANNED_WORK": "planned maintenance that was announced in advance is not covered",
        "ALREADY_CREDITED": "a credit for this outage is already on your account",
        "CAP_REACHED": "the credits on your account have reached the limit for this billing cycle",
        "SERVICE_SUSPENDED": "the service was not active during the outage",
    }.get(reason, "the outage does not meet the conditions of the policy")
    return (
        f" You asked about a credit: under our service credit policy ({policy_version}) none is"
        f" due for this outage, because {wording}. If you think that is wrong, reply here and a"
        " member of our team will review it."
    )


CREDIT_UNDER_REVIEW = (
    " I could not check your account for a service credit just now; a member of our team will"
    " review it and contact you."
)
# A credit for this service and outage is on record already. What is on record is what is said.
CREDIT_WITH_ANOTHER_CONTACT = (
    " A service credit for this outage is already being processed for your account."
)
CREDIT_ALREADY_ON_ACCOUNT = " A service credit for this outage is already on your account."
CREDIT_NOT_APPLIED_EARLIER = (
    " A service credit for this outage was looked at earlier and has not been applied; a member"
    " of our team will review it and contact you."
)
CREDIT_AMOUNT_REVIEW = " A member of our team will review the amount for this outage."


def credit_in_progress_line(amount_usd: str) -> str:
    return (
        f" A service credit of ${amount_usd} for this outage is already being processed and"
        " I'll confirm here once it has been applied to your account."
    )


def credit_already_applied_line(amount_usd: str) -> str:
    return (
        f" A service credit of ${amount_usd} for this outage has already been applied to"
        " your account."
    )


def credit_applied(complaint_ref: str, *, amount_usd: str, credit_ref: str | None) -> Reply:
    reference = f" (credit reference {credit_ref})" if credit_ref else ""
    return Reply(
        f"{AI_DISCLOSURE} The service credit of ${amount_usd} for your outage has been"
        f" applied to your account{reference}. It will show on your next bill. Your complaint"
        f" reference is {complaint_ref}.",
        "credit_applied.v1",
        {"amount_usd": amount_usd, "credit_ref": credit_ref},
    )


def credit_already_there(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} A service credit for this outage is already on your account, so no"
        f" second one has been added. Your complaint reference is {complaint_ref}.",
        "credit_already_there.v1",
    )


def credit_not_applied(complaint_ref: str) -> Reply:
    """Rejected, expired or failed. The reason is for the person who follows up, not guessed."""
    return Reply(
        f"{AI_DISCLOSURE} The service credit for your outage has not been applied. A member of"
        " our team will review it and contact you; you don't need to do anything."
        f" Your complaint reference is {complaint_ref}.",
        "credit_not_applied.v1",
    )


def credit_unconfirmed(complaint_ref: str) -> Reply:
    """Replaying stopped without an answer from Billing: applied or not, nobody knows yet."""
    return Reply(
        f"{AI_DISCLOSURE} I could not confirm that the service credit for your outage has been"
        " applied. A member of our team will check your account and contact you; you don't need"
        f" to do anything. Your complaint reference is {complaint_ref}.",
        "credit_unconfirmed.v1",
    )


def closing_after_credit(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} Thank you. I'll keep complaint {complaint_ref} open until the service"
        " credit is settled, and confirm here when it is.",
        "closing_after_credit.v1",
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


def taken_over(complaint_ref: str) -> Reply:
    """A person has taken the conversation from the assistant. Said once, by the platform:
    who the person is belongs to the staff's side of the record, not to the customer's."""
    return Reply(
        f"{AI_DISCLOSURE} A member of our team has taken over this conversation and will"
        " reply to you here. They have its details, so you won't need to repeat them, and I"
        f" won't answer further messages myself. Your complaint reference is {complaint_ref}.",
        "taken_over.v1",
    )


def dealt_with_by_staff(complaint_ref: str, note: str | None) -> Reply:
    """A person has closed their item of the staff queue. Said by the platform, not by the
    assistant. The note is the person's own statement and is quoted as theirs; who they are
    stays on the staff's side of the record."""
    theirs = f' Their note to you: "{note}"' if note else ""
    return Reply(
        f"A member of our team has dealt with your complaint {complaint_ref} and marked it"
        f" done.{theirs} If you still need help, reply here and your message will go to our"
        " team.",
        "dealt_with_by_staff.v1",
    )


def out_of_scope(complaint_ref: str) -> Reply:
    return Reply(
        f"{AI_DISCLOSURE} I can help with SkyLink broadband and mobile service problems,"
        " outages and related credits, and I can't help with other requests. If you have a"
        f" service problem, tell me what is happening. Your reference is {complaint_ref}.",
        "out_of_scope.v1",
    )
