"""Evaluation: is an outage detected for the customers it affects.

Brief metric: "Outage detected for affected customers >= 95%". Contacts are replayed through
the real workflow from customers whose true node is in outage at contact time: after the NOC
has declared it and before, with and without an alarm on the node, for broadband and mobile,
and for every kind of defect in the address mapping. Each kind is scored on its own and the
overall figure weights them by how often they occur in the labelled contacts of the data pack
(``evals/cases/contact_mix.json``). A kind that could not be replayed counts as missed.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import cycle
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.support.platform import Platform
from tests.support.stack import Simulator

from evals.replay import (
    AT_CONTACT,
    HELD_THEN_CONFIRMED,
    MISSED,
    Phrases,
    contact,
    customers,
    outage_outcome,
    pack_customers,
    seen,
)
from evals.report import Report, frozen, percent, stand_in

TARGET_PERCENT = 95.0
DEFECTS = ("none", "missing", "ambiguous", "stale")
AFTER, BEFORE = "after_detection", "before_detection"
# How many contacts of each kind are replayed at most.
PER_KIND = 18


@dataclass(frozen=True)
class Condition:
    timing: str
    alarm: bool = False


CONDITIONS = (Condition(AFTER), Condition(BEFORE, alarm=True), Condition(BEFORE, alarm=False))


@dataclass
class Case:
    service_type: str
    defect: str
    condition: Condition
    contact_id: UUID
    outcome: str = MISSED
    first: str = ""

    @property
    def kind(self) -> str:
        return f"{self.service_type}/{self.condition.timing}/{self.defect}"


def plan_cases(datapack: Path) -> list[tuple[dict[str, Any], str, Condition]]:
    """Customers for every kind of contact, and the condition their node is put in.

    A node is in one condition for the whole run, so the customers on it share it. Customers
    with a mapping defect are few and are placed first, so that every kind gets some.
    """
    node_condition: dict[str, Condition] = {}
    taken: dict[tuple[str, str, Condition], int] = {}
    seen_customers: set[str] = set()
    chosen = []
    for service_type in ("broadband", "mobile"):
        for defect in ("missing", "ambiguous", "stale", "none"):
            for person in customers(datapack, service_type, defect):
                node = person["true_node_id"]
                if node not in node_condition:
                    # The condition this kind of contact has the fewest cases of so far.
                    node_condition[node] = min(
                        CONDITIONS, key=lambda c: taken.get((service_type, defect, c), 0)
                    )
                key = (service_type, defect, node_condition[node])
                if taken.get(key, 0) >= PER_KIND or person["customer_id"] in seen_customers:
                    continue
                taken[key] = taken.get(key, 0) + 1
                seen_customers.add(person["customer_id"])
                chosen.append((person, defect, node_condition[node]))
    return chosen


async def set_the_network(
    simulator: Simulator, chosen: list[tuple[dict[str, Any], str, Condition]], gaps: list[int]
) -> dict[str, int]:
    """Start an outage on every node in the plan: declared already, or to be declared by the
    NOC after a gap drawn from the measured ones. Returns the gap of each undeclared node."""
    waits: dict[str, int] = {}
    spread = cycle(gaps)
    for person, _, condition in chosen:
        node = person["true_node_id"]
        if node in waits:
            continue
        mobile = person["service_type"] == "mobile"
        cause = "TOWER_FAULT" if mobile else "FIBRE_CUT"
        if condition.timing == AFTER:
            waits[node] = 0
            await simulator.start_outage(node, cause=cause, started_minutes_ago=30)
            continue
        waits[node] = next(spread)
        started = await simulator.start_outage(node, cause=cause, detect_after_seconds=waits[node])
        if not condition.alarm and started.get("alarm_id"):
            # The stand-in NOC raises a critical alarm with every outage. In the pack only half
            # the contacts before detection had one to go by: for the other half it is cleared.
            await simulator.post(f"/admin/v1/alarms/{started['alarm_id']}/clear")
    return waits


def weighted(cases: list[Case], mix: dict[str, dict[str, int]]) -> tuple[float, list[list[Any]]]:
    """The detection rate over all outage contacts, and the table behind it.

    Contacts before detection are weighted by how many of them had a critical alarm to go by.
    """
    total = sum(entry["contacts"] for entry in mix.values())
    detected_weight = 0.0
    rows = []
    for kind, entry in sorted(mix.items()):
        mine = [case for case in cases if case.kind == kind]
        shares = {False: 1.0}
        if BEFORE in kind:
            with_alarm = entry["with_a_critical_alarm"] / entry["contacts"]
            shares = {True: with_alarm, False: 1 - with_alarm}
        rate = 0.0
        for alarm, share in shares.items():
            part = [c for c in mine if c.condition.alarm == alarm] if BEFORE in kind else mine
            hit = sum(c.outcome != MISSED for c in part)
            rate += share * (hit / len(part) if part else 0.0)
        detected_weight += rate * entry["contacts"]
        rows.append(
            [
                kind,
                entry["contacts"],
                len(mine),
                sum(c.outcome == AT_CONTACT for c in mine),
                sum(c.outcome == HELD_THEN_CONFIRMED for c in mine),
                sum(c.outcome == MISSED for c in mine),
                f"{round(100 * rate, 1)}%" if mine else "not replayed: counted as missed",
            ]
        )
    return percent(detected_weight, total), rows


async def replay(
    platform: Platform, simulator: Simulator, datapack: Path, gaps: list[int]
) -> tuple[list[Case], int]:
    """Every chosen customer writes in while their node is in outage; then time passes until
    the NOC has declared every outage. Returns the cases and how many nodes were in outage."""
    chosen = plan_cases(datapack)
    waits = await set_the_network(simulator, chosen, gaps)
    await platform.feed()
    phrases = Phrases()
    cases = [
        Case(
            person["service_type"],
            defect,
            condition,
            await contact(platform, person, phrases.complaint(person["service_type"])),
        )
        for person, defect, condition in chosen
    ]
    await platform.settle(rounds=60)
    # The NOC declares each outage after its gap. Every look at the network in between is the
    # platform's own: its re-check timers, and the feed.
    elapsed = 0
    for gap in [*gaps, max(gaps) + 120]:
        platform.advance(gap + 1 - elapsed)
        elapsed = gap + 1
        await platform.feed()
        await platform.settle(rounds=60)
    for case in cases:
        record = await seen(platform, case.contact_id)
        case.outcome, case.first = outage_outcome(record), record.first
    return cases, len(waits)


def first_messages_of_the_missed(cases: list[Case]) -> dict[str, int]:
    """What each missed contact was told first, by kind of contact."""
    told: dict[str, int] = {}
    for case in cases:
        if case.outcome != MISSED:
            continue
        alarm = ""
        if case.condition.timing == BEFORE:
            alarm = " (alarm on the node)" if case.condition.alarm else " (no alarm)"
        key = f"{case.kind}{alarm}: {case.first}"
        told[key] = told.get(key, 0) + 1
    return told


def assumptions(cases: int, pack: int, hold: int, mix: dict[str, Any]) -> list[str]:
    total = mix["outage_contacts"]["total"]
    before = percent(mix["before_detection"]["contacts"], total)
    slowest = max(mix["before_detection"]["seconds_until_the_noc_declares_quantiles"].values())
    return [
        f"The weights are the labelled outage contacts of the {mix['source']['customers']:,}"
        f"-customer synthetic pack: {total:,} contacts, {before}% of them before the NOC had"
        f" declared the outage. The replay itself runs on a {pack:,}-customer pack from the"
        " same generator.",
        "An outage the NOC has not declared is the stand-in gateway's own model: the node"
        " status and the feed show nothing, every line on the node fails its test, and in"
        " half the cases (as measured in the pack) a critical alarm is up on the node.",
        f"In the pack the NOC declares an outage at most {slowest} seconds after it starts,"
        f" and the hold is {hold} seconds. A NOC that takes longer than the hold would turn"
        " held contacts without an alarm into visit offers: the hold must be set from the"
        " real delay.",
        f"{cases} contacts were replayed, and some kinds are rare: where the pack"
        " has two or three such customers the rate of that kind rests on two or three"
        " contacts. The kinds that decide the figure are the large ones.",
        "Customers are identified by the channel; wordings come from the phrase bank of the"
        " synthetic transcripts and are read by the offline keyword rules, not by a"
        " language model. A complaint those rules do not recognise is answered as out of"
        " scope, and counts as missed; the first run of this evaluation found five such"
        " phrases, which the rules now know.",
        "What is missed is by design of the thin mobile path (A9): a mobile connection has"
        " no remote test, so a mobile customer is not held before the NOC declares unless"
        " an alarm is up, and a defective tower mapping cannot be repaired.",
    ]


def the_report(
    cases: list[Case], nodes: int, pack: int, settings: Any, mix: dict[str, Any]
) -> tuple[Report, float]:
    gaps = sorted(set(mix["before_detection"]["seconds_until_the_noc_declares_quantiles"].values()))
    overall, rows = weighted(cases, mix["outage_contacts"]["by_service_timing_and_mapping"])
    missed_first = first_messages_of_the_missed(cases)
    report = Report(
        name="outage_detection",
        title="Outage detected for the customers it affects",
        proves=(
            "A customer whose node is in outage when they contact us is told about the outage:"
            " at once when the NOC has declared it, or after a hold when it has not yet, and in"
            " neither case sent device steps or a technician."
        ),
        target=f"At least {TARGET_PERCENT}% of affected contacts (brief).",
        headline=(
            f"{overall}% of affected contacts, weighted by how often each kind occurs;"
            f" {len(cases)} contacts replayed on {nodes} nodes, undeclared outages declared"
            f" {min(gaps)} to {max(gaps)} seconds after the contact."
        ),
        met=overall >= TARGET_PERCENT,
        provider=stand_in(settings.llm_provider),
        numbers={
            "detected_percent_weighted": overall,
            "contacts_replayed": len(cases),
            "nodes": nodes,
            "seconds_until_declared": gaps,
            "hold_seconds": settings.dispatch_hold_seconds,
            "missed_by_kind_and_first_message": missed_first,
        },
        assumptions=assumptions(len(cases), pack, settings.dispatch_hold_seconds, mix),
    )
    report.table(
        "By kind of contact (service / timing / mapping defect)",
        ["Kind", "In the pack", "Replayed", "At contact", "Held, then told", "Missed", "Rate"],
        rows,
    )
    report.table(
        "What the missed contacts were told first",
        ["Kind and first message", "Contacts"],
        [[key, count] for key, count in sorted(missed_first.items())] or [["none", 0]],
    )
    return report, overall


async def test_outage_is_detected_for_the_customers_it_affects(
    platform: Platform, simulator: Simulator, datapack: Path
) -> None:
    mix = frozen("contact_mix.json")
    quantiles = mix["before_detection"]["seconds_until_the_noc_declares_quantiles"]
    gaps = sorted(set(quantiles.values()))

    cases, nodes = await replay(platform, simulator, datapack, gaps)

    report, overall = the_report(
        cases, nodes, pack_customers(datapack), platform.runtime.settings, mix
    )
    report.write()

    assert overall >= TARGET_PERCENT, report.headline
