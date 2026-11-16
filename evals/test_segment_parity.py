"""Evaluation: the same situation ends the same way, whichever segment the customer is in.

The brief asks for a bias check: decisions tested across customer segments, and no protected
attribute or close proxy used. The platform holds one such attribute of a customer, the
segment (consumer or SME), and reads it nowhere (a unit test holds that:
``tests/unit/test_decision_inputs.py``). This evaluation shows the same by outcome. Customers
of both segments are put through the same situations, and what happens to them is compared:
what they are told, whether a person takes over and how urgently, whether a visit is offered,
whether a credit is owed and how it is worked out.

One thing does differ between the segments, and is reported instead of hidden: the monthly
fee. A credit is a share of the fee, so its amount differs, and with the amount the level of
approval it needs (above INR 500, a supervisor). The same rule gives every customer the same
share for the same outage, except where the policy's minimum of INR 50 lifts a small credit.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from tests.support.credit_journeys import SUPERVISOR_ABOVE, expected_credit, pending
from tests.support.platform import Platform
from tests.support.stack import Simulator

from evals.report import Report, percent, stand_in

SEGMENTS = ("consumer", "sme")
# Customers of each segment in each situation, each on a node of their own.
PER_SEGMENT = 5
SHORT_OUTAGE, LONG_OUTAGE = 300, 4320  # minutes: five hours, and three days
Signature = tuple[Any, ...]


@dataclass(frozen=True)
class Seen:
    """What one customer met with, in the terms that are compared across segments."""

    signature: Signature
    # For a credit: the monthly fee, the amount, and the level of approval it waits for.
    fee: Decimal | None = None
    amount: Decimal | None = None
    approval: str | None = None


async def _told(platform: Platform, contact_id: Any) -> tuple[str, ...]:
    messages = await platform.runtime.messages.list_for_contact(contact_id)
    return tuple(
        str(message.meta.get("template_id"))
        for message in messages
        if message.direction == "outbound"
    )


async def _handed(platform: Platform, contact_id: Any) -> tuple[tuple[str, int], ...]:
    items = await platform.runtime.handoffs.list_open(limit=1000)
    return tuple(
        sorted((item.reason, item.priority) for item in items if item.contact_id == contact_id)
    )


async def _signed_in(platform: Platform, customer: dict[str, Any], *texts: str) -> Any:
    first, *more = texts
    intake = await platform.contact(first, authenticated_customer_id=customer["customer_id"])
    await platform.settle()
    for text in more:
        await platform.say(intake.contact_id, text)
        await platform.settle()
    return intake.contact_id


async def in_an_outage(platform: Platform, simulator: Simulator, customer: dict[str, Any]) -> Seen:
    await simulator.start_outage(customer["true_node_id"])
    await platform.feed()
    contact = await _signed_in(platform, customer, "my internet is down")
    return Seen((await _told(platform, contact), await _handed(platform, contact)))


def after_an_outage_of(
    minutes: int,
) -> Callable[[Platform, Simulator, dict[str, Any]], Awaitable[Seen]]:
    async def scene(platform: Platform, simulator: Simulator, customer: dict[str, Any]) -> Seen:
        before = {item.approval_id for item in await pending(platform, "supervisor")}
        outage = await simulator.start_outage(customer["true_node_id"], started_minutes_ago=minutes)
        await platform.feed()
        contact = await _signed_in(platform, customer, "my internet is down")
        await simulator.resolve_outage(outage["outage_id"])
        await platform.feed()
        await platform.settle()
        mine = [
            item for item in await pending(platform, "supervisor") if item.approval_id not in before
        ]
        fee = Decimal(customer["monthly_fee_inr"])
        owed = expected_credit(customer["monthly_fee_inr"], minutes)
        amount = Decimal(mine[0].summary["amount_inr"]) if mine else None
        return Seen(
            (
                await _told(platform, contact),
                await _handed(platform, contact),
                # Owed by the policy's own rule, to the paisa, and waiting for a person.
                "the policy's amount"
                if amount == owed
                else f"{amount} where the policy says {owed}",
                len(mine),
            ),
            fee=fee,
            amount=amount,
            approval=mine[0].required_role if mine else None,
        )

    return scene


async def clear_and_passing(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> Seen:
    contact = await _signed_in(platform, customer, "my wifi keeps dropping")
    return Seen((await _told(platform, contact), await _handed(platform, contact)))


async def a_line_that_fails(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> Seen:
    settings = platform.runtime.settings
    await simulator.line_fault(customer["service_id"], fault=True)
    contact = await _signed_in(platform, customer, "my internet is down")
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        platform.advance(settings.network_recheck_seconds)
        await platform.settle()
    await simulator.line_fault(customer["service_id"], fault=False)
    return Seen((await _told(platform, contact), await _handed(platform, contact)))


async def nothing_can_be_read(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> Seen:
    await simulator.fault("network", "down")
    contact = await _signed_in(platform, customer, "my internet is down")
    await simulator.fault("network", "ok")
    return Seen((await _told(platform, contact), await _handed(platform, contact)))


async def a_billing_question(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> Seen:
    contact = await _signed_in(platform, customer, "why is my bill higher this month?")
    return Seen((await _told(platform, contact), await _handed(platform, contact)))


async def asks_for_a_person(
    platform: Platform, simulator: Simulator, customer: dict[str, Any]
) -> Seen:
    contact = await _signed_in(platform, customer, "my internet is down, I want to talk to a human")
    return Seen((await _told(platform, contact), await _handed(platform, contact)))


SITUATIONS: dict[str, Callable[[Platform, Simulator, dict[str, Any]], Awaitable[Seen]]] = {
    "An outage on the customer's node": in_an_outage,
    "An outage of five hours has ended": after_an_outage_of(SHORT_OUTAGE),
    "An outage of three days has ended": after_an_outage_of(LONG_OUTAGE),
    "A clear node and a line that passes its test": clear_and_passing,
    "A line that fails its test through the whole hold": a_line_that_fails,
    "The Network Monitoring API is down": nothing_can_be_read,
    "A billing question": a_billing_question,
    "The customer asks for a person": asks_for_a_person,
}


async def on_nodes_of_their_own(simulator: Simulator, segment: str) -> list[dict[str, Any]]:
    """Customers of the segment with a healthy broadband line, no two on one node."""
    found = await simulator.find_customers(
        50, segment=segment, service_type="broadband", mapping_defect="none", status="active"
    )
    chosen: dict[str, dict[str, Any]] = {}
    for customer in found:
        chosen.setdefault(customer["true_node_id"], customer)
    people = list(chosen.values())[:PER_SEGMENT]
    for customer in people:
        await simulator.line_fault(customer["service_id"], fault=False)
    return people


@pytest.fixture(scope="module")
def scoreboard() -> Iterator[dict[str, dict[str, list[Seen]]]]:
    rows: dict[str, dict[str, list[Seen]]] = {}
    yield rows
    write_report(rows)


@pytest.mark.parametrize("situation", list(SITUATIONS))
async def test_the_same_situation_ends_the_same_way_in_both_segments(
    platform: Platform,
    simulator: Simulator,
    scoreboard: dict[str, dict[str, list[Seen]]],
    situation: str,
) -> None:
    seen: dict[str, list[Seen]] = {}
    for segment in SEGMENTS:
        people = await on_nodes_of_their_own(simulator, segment)
        assert len(people) >= 3, f"too few {segment} customers in the data pack: {len(people)}"
        seen[segment] = [
            await SITUATIONS[situation](platform, simulator, customer) for customer in people
        ]
    scoreboard[situation] = seen

    consumer, sme = ({item.signature for item in seen[segment]} for segment in SEGMENTS)
    assert consumer == sme, f"only consumers: {consumer - sme}; only SME: {sme - consumer}"


def _share(item: Seen) -> str:
    if item.amount is None or item.fee is None:
        return "no credit"
    return f"{(100 * item.amount / item.fee).quantize(Decimal('0.01'))}%"


def write_report(rows: dict[str, dict[str, list[Seen]]]) -> None:
    if set(rows) != set(SITUATIONS):
        # A run of some of the situations (pytest -k) is not the evaluation: the report of
        # the whole is left as it is.
        return
    same = [
        name
        for name, seen in rows.items()
        if _signatures(seen, "consumer") == _signatures(seen, "sme")
    ]
    customers = sum(len(items) for seen in rows.values() for items in seen.values())
    report = Report(
        name="segment_parity",
        title="The same situation, the same outcome, in both segments",
        proves=(
            "Consumer and SME customers put through the same situations meet with the same"
            " messages, the same handoffs at the same priority, the same decision on a visit"
            " and the same rule for a credit. The customer's segment is not an input to any"
            " decision; this shows it by outcome."
        ),
        target=(
            "No difference between the segments in any situation, other than the amount of a"
            " credit and what follows from the amount, both explained by the monthly fee."
        ),
        headline=(
            f"{len(same)} of {len(rows)} situations end the same way in both segments"
            f" ({percent(len(same), len(rows))}%), over {customers} customers."
        ),
        met=len(same) == len(rows) and bool(rows),
        provider=stand_in("offline"),
        numbers={
            "situations": len(rows),
            "situations_with_the_same_outcomes": len(same),
            "customers": customers,
            "per_segment": {
                name: {segment: len(items) for segment, items in seen.items()}
                for name, seen in rows.items()
            },
        },
        assumptions=[
            "A1: synthetic customers. In the stand-in data the segment decides which plans a"
            " customer can have and nothing else; a real customer base may tie the segment to"
            " things this data does not hold.",
            "Segment is the one attribute of this kind the platform is given. It holds no"
            " age, gender, language, religion or income, and reads the customer's name only"
            " to mask it.",
            "Where a customer lives decides which network node serves them, and with that"
            " whether an outage is theirs. That is the platform's job and not a proxy the"
            " evaluation can separate: here every customer is put on a node of their own and"
            " the node is given the situation.",
            "A few customers of each segment in each situation: this shows the absence of a"
            " rule that treats segments differently, not a statistical equality of outcomes"
            " over SkyLink's customers.",
        ],
    )
    report.table(
        "Outcomes by situation",
        ["Situation", "Consumer", "SME", "Same"],
        [
            [
                name,
                _outcomes(seen["consumer"]),
                _outcomes(seen["sme"]),
                "yes" if name in same else "NO",
            ]
            for name, seen in rows.items()
        ],
    )
    credits = [
        [name, segment, str(item.fee), str(item.amount), _share(item), item.approval or "none"]
        for name, seen in rows.items()
        for segment, items in seen.items()
        for item in _one_of_each_fee(items)
        if item.fee is not None
    ]
    if credits:
        report.table(
            "What does differ: the credit, by monthly fee",
            [
                "Situation",
                "Segment",
                "Monthly fee (INR)",
                "Credit (INR)",
                "Share of the fee",
                "Approved by",
            ],
            credits,
        )
        report.assumptions.append(
            "The credit is the same share of the monthly fee for the same outage in both"
            f" segments, except where the policy's minimum lifts it. Above INR {SUPERVISOR_ABOVE}"
            " a supervisor approves instead of an agent: SME plans cost more, so their"
            " credits reach that level sooner. If supervisors are slower than agents, SME"
            " customers wait longer for a larger credit. That follows from the amount, is"
            " the placeholder policy's rule (A4), and is for SkyLink to confirm."
        )
    report.write()


def _signatures(seen: dict[str, list[Seen]], segment: str) -> set[Signature]:
    return {item.signature for item in seen[segment]}


def _outcomes(items: list[Seen]) -> str:
    counted = Counter(_words(item.signature) for item in items)
    return "; ".join(f"{count} of them: {what}" for what, count in counted.most_common())


def _words(signature: Signature) -> str:
    told, handed, *credit = signature
    parts = [" → ".join(told) or "nothing sent"]
    if handed:
        parts.append(
            "handed over: " + ", ".join(f"{reason} (priority {p})" for reason, p in handed)
        )
    if credit:
        parts.append(f"credit: {credit[0]}, {credit[1]} approval(s) waiting")
    return ", ".join(parts)


def _one_of_each_fee(items: list[Seen]) -> list[Seen]:
    by_fee: dict[Decimal | None, Seen] = {}
    for item in items:
        by_fee.setdefault(item.fee, item)
    return [by_fee[fee] for fee in sorted(by_fee, key=lambda fee: fee or Decimal(0))]
