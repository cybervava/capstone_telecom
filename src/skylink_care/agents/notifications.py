"""Proactive Notification: what affected customers are told before they call.

The texts are short, carry no personal data, and are built from the same verified outage facts
as the conversation replies. Whether a message may be sent at all is not decided here: consent is
checked by the executor at the moment of sending.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from skylink_care.agents.replies import cause_phrase, clock, eta_source_phrase
from skylink_care.domain.models import OutageInfo

OUTAGE_STARTED = "outage_started.v1"
OUTAGE_RESOLVED = "outage_resolved.v1"
REQUESTED_BY = "agent:notification"
# Asks the gateway to use the channel on the customer's consent record at the moment of sending.
PREFERRED_CHANNEL = "preferred"


@dataclass(frozen=True)
class Notification:
    template_id: str
    text: str


def outage_started(
    outage: OutageInfo, *, service_types: Sequence[str], area: str | None
) -> Notification:
    services = _services(service_types)
    where = f" in {area}" if area else ""
    if outage.planned:
        opening = (
            f"SkyLink: planned maintenance is affecting your {services}{where}"
            f" (ref {outage.outage_id})."
        )
    else:
        opening = (
            f"SkyLink: we are aware of an outage affecting your {services}{where}, caused by"
            f" {cause_phrase(outage.cause)} (ref {outage.outage_id}). Our engineers are working"
            " on it."
        )
    if outage.eta is not None:
        estimate = (
            f" Expected back by {clock(outage.eta)} ({eta_source_phrase(outage.eta_source)})."
        )
    else:
        estimate = " There is no restoration estimate yet."
    advice = (
        " You do not need to restart your router or contact us."
        if "broadband" in service_types
        else " You do not need to contact us."
    )
    return Notification(
        OUTAGE_STARTED,
        f"{opening}{estimate}{advice} We will message you when service is restored.",
    )


def outage_resolved(outage: OutageInfo, *, service_types: Sequence[str]) -> Notification:
    when = f" at {clock(outage.resolved_at)}" if outage.resolved_at else ""
    device = "router" if "broadband" in service_types else "phone"
    return Notification(
        OUTAGE_RESOLVED,
        f"SkyLink: the outage affecting your {_services(service_types)} has been resolved{when}"
        f" (ref {outage.outage_id}). If your service is not back within 10 minutes, restart"
        f" your {device} once and contact us if it still does not work. We are sorry for the"
        " interruption.",
    )


def request_body(notification: Notification, *, customer_id: str, outage_id: str) -> dict[str, Any]:
    """The request exactly as it will be sent. It is frozen in the action ledger."""
    return {
        "customer_id": customer_id,
        "channel": PREFERRED_CHANNEL,
        "template_id": notification.template_id,
        "body": notification.text,
        "outage_id": outage_id,
    }


def action_key(customer_id: str, outage_id: str, template_id: str) -> str:
    """One message per customer, outage and template: an ETA change never sends a second one."""
    return f"notify:{customer_id}:{outage_id}:{template_id}"


def _services(service_types: Sequence[str]) -> str:
    names = sorted(set(service_types))
    if len(names) == 1:
        return f"{names[0]} service"
    return f"{' and '.join(names)} services"
