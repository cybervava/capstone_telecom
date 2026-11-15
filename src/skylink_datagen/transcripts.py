"""Assign legacy outcomes (visits, resolutions, channel, PII) and render transcript records."""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from skylink_datagen.config import fmt_ts
from skylink_datagen.contacts import ContactPlanError, Planned, repeat_flags
from skylink_datagen.dialogue import DialogueSpec, Speaker, render
from skylink_datagen.reference import ReferenceIndex, customer_id, phone_for, service_id
from skylink_datagen.wordlists import FIRST_NAMES

VISIT_SHARE = {"outage": 0.30, "device": 0.25}
NECESSARY_SHARE = {"outage": 0.0, "device": 0.70}
PII_SHARE, CHAT_SHARE = 0.30, 0.40
RESOLUTIONS: dict[str, tuple[tuple[str, ...], tuple[int, ...]]] = {
    "outage": (("reboot_script", "escalated", "resolved_on_call"), (70, 20, 10)),
    "device": (("reboot_script", "resolved_on_call", "escalated"), (40, 45, 15)),
    "billing": (("billing_adjustment", "resolved_on_call", "escalated"), (50, 40, 10)),
}


@dataclass(frozen=True)
class Outcome:
    technician_visit: bool
    visit_necessary: bool | None
    resolution: str


def _visits(contacts: list[Planned], ref: ReferenceIndex, rng: random.Random) -> dict[int, bool]:
    """Index -> visit_necessary for every contact that ends in a technician visit."""
    chosen: dict[int, bool] = {}
    for label, share in VISIT_SHARE.items():
        indices = [i for i, c in enumerate(contacts) if c.label == label]
        eligible = [i for i in indices if not ref.service_is_mobile[contacts[i].service]]
        quota = round(share * len(indices))
        if quota > len(eligible):
            msg = f"only {len(eligible)} broadband {label} contacts for {quota} visits"
            raise ContactPlanError(msg)
        necessary = round(NECESSARY_SHARE[label] * quota)
        for rank, index in enumerate(rng.sample(eligible, quota)):
            chosen[index] = rank < necessary
    return chosen


def _outcomes(contacts: list[Planned], ref: ReferenceIndex, rng: random.Random) -> list[Outcome]:
    visits = _visits(contacts, ref, rng)
    outcomes: list[Outcome] = []
    for i, contact in enumerate(contacts):
        if i in visits:
            outcomes.append(Outcome(True, visits[i], "technician_visit"))
            continue
        names, weights = RESOLUTIONS[contact.label]
        outcomes.append(Outcome(False, None, rng.choices(names, weights=weights)[0]))
    return outcomes


def _speaker(ref: ReferenceIndex, customer: int) -> Speaker:
    return Speaker(
        first_name=FIRST_NAMES[ref.customer_first[customer]],
        phone=phone_for(customer),
        customer_id=customer_id(customer),
        email=ref.customer_email[customer],
    )


def build_transcripts(
    contacts: list[Planned], ref: ReferenceIndex, rng: random.Random, text_rng: random.Random
) -> Iterator[dict[str, Any]]:
    outcomes = _outcomes(contacts, ref, rng)
    pii = frozenset(rng.sample(range(len(contacts)), round(PII_SHARE * len(contacts))))
    repeats = repeat_flags(contacts)
    for i, (contact, outcome) in enumerate(zip(contacts, outcomes, strict=True)):
        mobile = bool(ref.service_is_mobile[contact.service])
        spec = DialogueSpec(contact.label, mobile, outcome.resolution, i in pii)
        first, transcript = render(text_rng, spec, _speaker(ref, contact.customer))
        yield {
            "contact_id": f"H{i + 1:07d}",
            "ts": fmt_ts(contact.ts),
            "channel": "chat" if rng.random() < CHAT_SHARE else "call",
            "customer_id": customer_id(contact.customer),
            "service_id": service_id(contact.service),
            "label": contact.label,
            "outage_id": contact.outage_id,
            "first_customer_message": first,
            "transcript": transcript,
            "legacy_resolution": outcome.resolution,
            "technician_visit": outcome.technician_visit,
            "visit_necessary": outcome.visit_necessary,
            "repeat_within_7d": repeats[i],
        }
