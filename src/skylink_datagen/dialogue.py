"""Render a transcript from phrase banks.

Legacy agents did not know about outages: every connectivity contact gets the reboot script
first, whatever the true cause was.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from skylink_datagen import phrases as p


@dataclass(frozen=True)
class Speaker:
    first_name: str
    phone: str
    customer_id: str
    email: str


@dataclass(frozen=True)
class DialogueSpec:
    label: str
    mobile: bool
    resolution: str
    pii: bool


def _agent(text: str) -> str:
    return f"Agent: {text}"


def _customer(text: str) -> str:
    return f"Customer: {text}"


def _opening(rng: random.Random, spec: DialogueSpec) -> str:
    if spec.label == "billing":
        symptom, tail = rng.choice(p.BILLING_SYMPTOMS), rng.choice(p.BILLING_TAILS)
    else:
        bank = p.MOBILE_SYMPTOMS if spec.mobile else p.BROADBAND_SYMPTOMS
        symptom, tail = rng.choice(bank), rng.choice(p.CONNECTIVITY_TAILS)
    greeting = rng.choice(p.GREETINGS)
    body = symptom + (rng.choice((". ", ", ", " ", "... ")) + tail if tail else "")
    text = f"{greeting}, {body}" if greeting else body
    text += rng.choice(("", "", ".", "!", "??", " pls"))
    return text[0].upper() + text[1:] if rng.random() < 0.55 else text


def _identifier(rng: random.Random, who: Speaker) -> str:
    kind = rng.choice(("phone", "phone", "customer_id", "email"))
    values = {
        "phone": who.phone if rng.random() < 0.5 else who.phone[3:],
        "customer_id": who.customer_id,
        "email": who.email,
    }
    return rng.choice(p.PII_TEMPLATES[kind]).format(value=values[kind])


def _greeting_turns(rng: random.Random, spec: DialogueSpec, who: Speaker) -> tuple[str, list[str]]:
    opening = _opening(rng, spec)
    in_opening = spec.pii and rng.random() < 0.5
    first = f"{opening} {_identifier(rng, who)}" if in_opening else opening
    name = f", {who.first_name}" if rng.random() < 0.7 else ""
    opener = rng.choice(p.AGENT_OPENERS).format(name=name)
    if spec.pii and not in_opening:
        return first, [_customer(first), _agent(f"{opener} {p.PII_ASK}"),
                       _customer(_identifier(rng, who))]  # fmt: skip
    return first, [_customer(first), _agent(opener)]


def _connectivity_turns(rng: random.Random, spec: DialogueSpec) -> list[str]:
    probes = p.MOBILE_PROBES if spec.mobile else p.BROADBAND_PROBES
    reboot = p.REBOOT_MOBILE if spec.mobile else p.REBOOT_BROADBAND
    turns: list[str] = []
    if rng.random() < 0.6:
        probe, answers = rng.choice(probes)
        turns += [_agent(probe), _customer(rng.choice(answers))]
    turns.append(_agent(rng.choice(reboot)))
    if spec.resolution == "resolved_on_call":
        return [*turns, _customer(rng.choice(p.CUSTOMER_ACKS["resolved_on_call"])),
                _agent(rng.choice(p.CLOSERS["resolved_on_call"]))]  # fmt: skip
    turns += [_customer(rng.choice(p.STILL_BROKEN)), _agent(rng.choice(p.CLOSERS[spec.resolution]))]
    if rng.random() < 0.7:
        turns.append(_customer(rng.choice(p.CUSTOMER_ACKS[spec.resolution])))
    return turns


def _billing_turns(rng: random.Random, spec: DialogueSpec) -> list[str]:
    turns = [
        _agent(rng.choice(p.BILLING_PROBES)),
        _customer(rng.choice(p.BILLING_DETAILS)),
        _agent(rng.choice(p.BILLING_EXPLAIN)),
    ]
    closer = _agent(rng.choice(p.BILLING_CLOSERS[spec.resolution]))
    if spec.resolution == "resolved_on_call":
        return [*turns, _customer(rng.choice(p.BILLING_UNDERSTOOD)), closer]
    acks = p.CUSTOMER_ACKS["billing_adjustment"]
    return [*turns, closer, *([_customer(rng.choice(acks))] if rng.random() < 0.7 else [])]


def _merge_turns(lines: list[str]) -> list[str]:
    """Join consecutive lines by the same speaker into one turn."""
    merged: list[str] = []
    for line in lines:
        speaker, _, text = line.partition(": ")
        if merged and merged[-1].startswith(f"{speaker}: "):
            merged[-1] = f"{merged[-1]} {text}"
        else:
            merged.append(line)
    return merged


def render(rng: random.Random, spec: DialogueSpec, who: Speaker) -> tuple[str, str]:
    """Return (first_customer_message, transcript) with 4-10 prefixed lines."""
    first, lines = _greeting_turns(rng, spec, who)
    body = _billing_turns(rng, spec) if spec.label == "billing" else _connectivity_turns(rng, spec)
    lines.extend(body)
    if len(lines) <= 8 and rng.random() < 0.3:
        lines.extend(rng.choice(p.SMALL_TALK))
    return first, "\n".join(_merge_turns(lines))
