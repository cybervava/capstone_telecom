"""Replaying contacts through the platform, and reading back what happened to each.

The evaluations that replay contacts do not look at state inside the workflow. They read what a
customer and an auditor could: the messages sent, in order, with the template each was built
from; the items queued for a person; and the visit bookings that reached Field Service.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from itertools import cycle
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.support.platform import Platform

from skylink_care.db.actions import VISIT_BOOK, ActionStatus
from skylink_datagen.phrases import BROADBAND_SYMPTOMS, CONNECTIVITY_TAILS, MOBILE_SYMPTOMS

OUTAGE_TOLD = frozenset({"outage_active.v1", "outage_disputed.v1"})
SUSPECTED = "network_unknown.v1:SUSPECTED_NODE_FAULT"
HELD = frozenset({"line_fault_hold.v1", SUSPECTED})
DEVICE_FLOW = frozenset({"guided_steps.v1", "checks_clear.v1"})
DISPATCH = frozenset(
    {"visit_offer.v1", "visit_slot_taken.v1", "visit_being_booked.v1", "visit_booked.v1"}
)
AT_CONTACT, HELD_THEN_CONFIRMED, MISSED = "at contact", "held, then confirmed", "missed"


@dataclass(frozen=True)
class Seen:
    """What happened on one contact, from its records."""

    # The template of each message sent, in order. A degraded-mode message carries its reason.
    templates: tuple[str, ...]
    handoffs: tuple[str, ...]
    visits_booked: int

    @property
    def first(self) -> str:
        return self.templates[0] if self.templates else "nothing sent"

    def any_of(self, wanted: frozenset[str]) -> bool:
        return any(template in wanted for template in self.templates)


async def seen(platform: Platform, contact_id: UUID) -> Seen:
    runtime = platform.runtime
    templates = []
    for message in await runtime.messages.list_for_contact(contact_id):
        if message.direction != "outbound":
            continue
        template = str(message.meta.get("template_id"))
        reason = (message.meta.get("facts") or {}).get("reason")
        templates.append(f"{template}:{reason}" if template == "network_unknown.v1" else template)
    queued = [
        item.reason
        for item in await runtime.handoffs.list_open(limit=10_000)
        if item.contact_id == contact_id
    ]
    booked = [
        action
        for action in await runtime.actions.list_for_contact(contact_id)
        if action.action_type == VISIT_BOOK and action.status is ActionStatus.SUCCEEDED
    ]
    return Seen(tuple(templates), tuple(queued), len(booked))


def outage_outcome(record: Seen) -> str:
    """Was the outage detected for this contact, by the rule of the execution plan: told at
    contact time, or held at contact time and told once the NOC declared it; and in both cases
    with no device steps and no visit started."""
    if record.any_of(DEVICE_FLOW) or record.any_of(DISPATCH) or record.visits_booked:
        return MISSED
    if record.first in OUTAGE_TOLD:
        return AT_CONTACT
    if record.first in HELD and record.any_of(OUTAGE_TOLD):
        return HELD_THEN_CONFIRMED
    return MISSED


class Phrases:
    """Customer wordings from the phrase bank of the synthetic transcripts, in a fixed order."""

    def __init__(self) -> None:
        self._broadband = cycle(BROADBAND_SYMPTOMS)
        self._mobile = cycle(MOBILE_SYMPTOMS)
        self._tails = cycle(CONNECTIVITY_TAILS)

    def complaint(self, service_type: str, *, also: str = "") -> str:
        symptom = next(self._mobile if service_type == "mobile" else self._broadband)
        return " ".join(part for part in (symptom, next(self._tails), also) if part)


def customers(datapack: Path, service_type: str, defect: str) -> list[dict[str, Any]]:
    """Customers of the data pack with an active service of this type whose address mapping
    has this defect. The ground truth (the node that really serves them) comes with them; the
    platform is never shown it.

    A mobile customer must have no other service, so that words like "network nahi aa raha"
    cannot be taken for their broadband.
    """
    with (datapack / "truth_service_node.csv").open(encoding="utf-8") as handle:
        truth = {row["service_id"]: row for row in csv.DictReader(handle)}
    with (datapack / "services.csv").open(encoding="utf-8") as handle:
        services = [row for row in csv.DictReader(handle) if row["status"] == "active"]
    held = Counter(service["customer_id"] for service in services)
    return [
        {
            "customer_id": service["customer_id"],
            "service_id": service["service_id"],
            "service_type": service["service_type"],
            "monthly_fee_inr": service["monthly_fee_inr"],
            "true_node_id": truth[service["service_id"]]["true_node_id"],
        }
        for service in services
        if service["service_type"] == service_type
        and (service_type != "mobile" or held[service["customer_id"]] == 1)
        and truth[service["service_id"]]["mapping_defect"] == defect
    ]


def pack_customers(datapack: Path) -> int:
    """How many customers the data pack of the replay has."""
    with (datapack / "customers.csv").open(encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


async def contact(platform: Platform, person: dict[str, Any], text: str) -> UUID:
    intake = await platform.contact(text, authenticated_customer_id=person["customer_id"])
    return intake.contact_id
