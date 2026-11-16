"""Evaluation: how many unnecessary technician visits the new workflow avoids (a simulation).

Brief metric: "Unnecessary truck rolls cut >= 50%". The baseline is what the labelled contacts
of the data pack record: which contacts ended in a technician visit, and whether the visit was
needed. The same three kinds of contact are replayed through the real workflow with a customer
who wants a technician, takes one whenever offered, and goes on asking until a person takes
over:

* an outage on the customer's node (the old process sent a technician anyway);
* a real fault on the customer's own line (the visit was needed);
* a fault the customer could fix themselves (it was not).

What the replay can show is what the workflow books and where it hands over to a person. What
that person then decides it cannot show, and the report says how the result depends on it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import cycle
from pathlib import Path
from statistics import median
from typing import Any
from uuid import UUID

from tests.support.platform import Platform
from tests.support.stack import Simulator

from evals.replay import DISPATCH, Phrases, contact, customers, seen
from evals.report import Report, frozen, percent, stand_in

TARGET_PERCENT = 50.0
MIN_NEEDED_PERCENT = 90.0
OUTAGE, NEEDED, NOT_NEEDED = "outage", "line fault (visit needed)", "home equipment (not needed)"
# Where each kind of contact is counted in the baseline's technician visits.
BASELINE = {
    OUTAGE: "outage/unnecessary",
    NEEDED: "device/necessary",
    NOT_NEEDED: "device/unnecessary",
}
UNNECESSARY = (OUTAGE, NOT_NEEDED)
# What the network showed when an outage customer wrote: declared, and an alarm to go by.
CONDITIONS = (
    ("declared by the NOC", True, False),
    ("not yet declared, alarm on the node", False, True),
    ("not yet declared, no alarm", False, False),
)
SHARES = (0.0, 0.25, 0.5, 0.75, 1.0)
# What the customer writes after every answer that is not a visit, and how often at most.
ASKS_AGAIN = {
    OUTAGE: "I still want a technician to come and check",
    NOT_NEEDED: "that did not help, send a technician",
}
MAX_ASKS = 3
PER_KIND = 12
STEP_SECONDS = 30


@dataclass
class Case:
    kind: str
    person: dict[str, Any]
    contact_id: UUID
    # For an outage: was it declared when the customer wrote, and was there an alarm to go by.
    declared: bool = True
    alarm: bool = False
    answered_offer: bool = False
    asked_again: int = 0
    visits: int = 0
    offered: bool = False
    handoffs: tuple[str, ...] = ()
    templates: tuple[str, ...] = ()
    minutes_to_booking: float | None = None


@dataclass
class Plan:
    cases: list[Case] = field(default_factory=list)
    waits: dict[str, int] = field(default_factory=dict)


async def start_contacts(
    platform: Platform, simulator: Simulator, datapack: Path, gaps: list[int]
) -> Plan:
    """Put the network in its states, then let every customer write in.

    Half the nodes of the pack are put in outage, a third each declared, undeclared with an
    alarm and undeclared without. The other half stay healthy: on each, one customer's line
    is made to fail (a visit is needed) and another's is left working (it is not). One failing
    line per node, so that a line fault is not taken for the start of a node fault.
    """
    by_node: dict[str, list[dict[str, Any]]] = {}
    for person in customers(datapack, "broadband", "none"):
        by_node.setdefault(person["true_node_id"], []).append(person)
    nodes = sorted(node for node, people in by_node.items() if len(people) >= 3)
    per_condition = min(PER_KIND // 3, len(nodes) // 6)
    in_outage, healthy = nodes[: 3 * per_condition], nodes[3 * per_condition :][:PER_KIND]
    phrases, plan, spread = Phrases(), Plan(), cycle(gaps)
    pending: list[tuple[str, dict[str, Any], bool, bool]] = []

    for index, node in enumerate(in_outage):
        declared, alarm = ((True, False), (False, True), (False, False))[index % 3]
        wait = 0 if declared else next(spread)
        started = await simulator.start_outage(
            node, **({"started_minutes_ago": 30} if declared else {"detect_after_seconds": wait})
        )
        if not declared and not alarm and started.get("alarm_id"):
            await simulator.post(f"/admin/v1/alarms/{started['alarm_id']}/clear")
        plan.waits[node] = wait
        pending += [(OUTAGE, person, declared, alarm) for person in by_node[node][:3]]
    for node in healthy:
        failing, working = by_node[node][:2]
        await simulator.line_fault(failing["service_id"], fault=True)
        await simulator.line_fault(working["service_id"], fault=False)
        pending += [(NEEDED, failing, True, False), (NOT_NEEDED, working, True, False)]
    await platform.feed()
    for kind, person, declared, alarm in pending:
        text = phrases.complaint("broadband", also="please send a technician")
        contact_id = await contact(platform, person, text)
        plan.cases.append(Case(kind, person, contact_id, declared=declared, alarm=alarm))
    await platform.settle(rounds=60)
    return plan


async def take_offers(platform: Platform, plan: Plan) -> bool:
    """Every customer who has been offered a visit and has not answered takes the first slot:
    that is the customer this simulation assumes, for every kind of contact."""
    answered = False
    for case in plan.cases:
        record = await seen(platform, case.contact_id)
        if record.any_of(frozenset({"visit_offer.v1"})) and not case.answered_offer:
            case.answered_offer = answered = True
            await platform.say(case.contact_id, "1")
    return answered


async def run_the_clock(platform: Platform, plan: Plan, seconds: int) -> None:
    """Let time pass in small steps, with every customer taking a visit as soon as offered."""
    for _ in range(seconds // STEP_SECONDS):
        platform.advance(STEP_SECONDS)
        await platform.feed()
        await platform.settle(rounds=60)
        if await take_offers(platform, plan):
            await platform.settle(rounds=60)


async def insist(platform: Platform, plan: Plan) -> None:
    """After every answer that is not a visit the customer asks for a technician again, until
    a person takes over or they have asked ``MAX_ASKS`` more times. A visit offered in answer
    is taken."""
    for _ in range(MAX_ASKS):
        asked = False
        for case in plan.cases:
            record = await seen(platform, case.contact_id)
            if case.kind not in ASKS_AGAIN or record.handoffs or record.visits_booked:
                continue
            await platform.say(case.contact_id, ASKS_AGAIN[case.kind])
            case.asked_again += 1
            asked = True
        if not asked:
            return
        await platform.settle(rounds=60)
        if await take_offers(platform, plan):
            await platform.settle(rounds=60)


async def read_back(platform: Platform, simulator: Simulator, plan: Plan) -> None:
    for case in plan.cases:
        record = await seen(platform, case.contact_id)
        case.visits, case.handoffs = record.visits_booked, record.handoffs
        case.templates = record.templates
        case.offered = record.any_of(DISPATCH)
        booked = await simulator.get("/field/v1/appointments", service_id=case.person["service_id"])
        stored = await platform.runtime.contacts.get(case.contact_id)
        if booked["appointments"] and stored is not None:
            made = datetime.fromisoformat(booked["appointments"][0]["created_at"])
            case.minutes_to_booking = round((made - stored.received_at).total_seconds() / 60, 1)


@dataclass(frozen=True)
class Measured:
    """What the replay showed, set against the technician visits of the baseline."""

    cases: tuple[Case, ...]
    legacy: dict[str, int]
    # How many labelled contacts the baseline's visits were counted in.
    labelled_contacts: int
    hold_seconds: int
    provider: str

    def of(self, kind: str) -> list[Case]:
        return [case for case in self.cases if case.kind == kind]

    def booked(self, kind: str) -> int:
        return sum(case.visits > 0 for case in self.of(kind))

    def handed(self, kind: str) -> int:
        """Contacts that ended with a person, with no visit booked by the workflow."""
        return sum(bool(case.handoffs) and not case.visits for case in self.of(kind))

    def unsettled(self, kind: str) -> int:
        """Contacts that ended with neither a visit nor a person: still being answered when
        the customer stopped asking, or told nothing at all."""
        return sum(not case.visits and not case.handoffs for case in self.of(kind))

    @property
    def baseline(self) -> int:
        return sum(self.legacy[BASELINE[kind]] for kind in UNNECESSARY)

    def unnecessary(self, granted: float = 0.0) -> float:
        """Unnecessary visits at the baseline's volumes: those the workflow booked, plus this
        share of the contacts that ended with a person. Nothing unproven counts in favour: a
        contact that ended with neither a visit nor a person, and a kind of contact that was
        not replayed, count as visits that still happen."""
        total = 0.0
        for kind in UNNECESSARY:
            mine = self.of(kind)
            visits = self.booked(kind) + self.unsettled(kind) + granted * self.handed(kind)
            total += self.legacy[BASELINE[kind]] * (visits / len(mine) if mine else 1.0)
        return total

    @property
    def most_a_person_may_grant(self) -> float | None:
        """The largest share of the contacts that ended with a person that can be sent a
        technician with the target still met, in percent; None when no share is small
        enough."""
        least, most = self.unnecessary(0.0), self.unnecessary(1.0)
        allowed = self.baseline * (1 - TARGET_PERCENT / 100)
        if least > allowed:
            return None
        return 100.0 if most <= allowed else percent(allowed - least, most - least)

    def cut(self, granted: float = 0.0) -> float:
        return percent(self.baseline - self.unnecessary(granted), self.baseline)

    @property
    def needed_booked_percent(self) -> float:
        return percent(self.booked(NEEDED), len(self.of(NEEDED)))

    @property
    def waits(self) -> list[float]:
        """Minutes from the contact to the booking, for the visits that were needed."""
        return [
            case.minutes_to_booking
            for case in self.of(NEEDED)
            if case.minutes_to_booking is not None
        ]

    @property
    def met(self) -> bool:
        """Judged on the visits the workflow books, which is what a replay can measure."""
        return self.cut() >= TARGET_PERCENT and self.needed_booked_percent >= MIN_NEEDED_PERCENT


def by_kind(measured: Measured) -> list[list[Any]]:
    return [
        [
            kind,
            measured.legacy[BASELINE[kind]],
            len(measured.of(kind)),
            sum(case.offered for case in measured.of(kind)),
            measured.booked(kind),
            measured.handed(kind),
            measured.unsettled(kind),
        ]
        for kind in (OUTAGE, NEEDED, NOT_NEEDED)
    ]


def if_people_send_technicians(measured: Measured) -> list[list[Any]]:
    return [
        [f"{round(100 * part)}%", round(measured.unnecessary(part)), f"{measured.cut(part)}%"]
        for part in SHARES
    ]


def groups(measured: Measured) -> list[tuple[str, list[Case]]]:
    """Each kind of contact, the outage ones by what the network showed."""
    return [
        (
            f"{OUTAGE}, {label}",
            [c for c in measured.of(OUTAGE) if (c.declared, c.alarm) == (declared, alarm)],
        )
        for label, declared, alarm in CONDITIONS
    ] + [(kind, measured.of(kind)) for kind in (NEEDED, NOT_NEEDED)]


def what_customers_were_sent(measured: Measured) -> list[list[Any]]:
    """The messages each contact got, in order, by the template each was built from."""
    rows = []
    for label, part in groups(measured):
        sent = Counter(" > ".join(case.templates) or "nothing" for case in part)
        rows += [[label, sequence, count] for sequence, count in sorted(sent.items())]
    return rows


def where_contacts_ended(measured: Measured) -> list[list[Any]]:
    """The reasons for which contacts were queued for a person."""
    rows = []
    for label, part in groups(measured):
        reasons = Counter(reason for case in part for reason in case.handoffs)
        rows.append(
            [
                label,
                len(part),
                sum(case.visits > 0 for case in part),
                sum(bool(case.handoffs) for case in part),
                ", ".join(f"{reason} ({n})" for reason, n in sorted(reasons.items())) or "-",
            ]
        )
    return rows


def headline(measured: Measured) -> str:
    waits = measured.waits
    insisted = [case for kind in UNNECESSARY for case in measured.of(kind)]
    handed = sum(measured.handed(kind) for kind in UNNECESSARY)
    limit = measured.most_a_person_may_grant
    end_to_end = (
        "the target is missed whatever those people decide"
        if limit is None
        else f"the target holds while those people send a technician to at most {limit}% of them"
    )
    return (
        f"The workflow booked a visit for {measured.booked(OUTAGE)} of"
        f" {len(measured.of(OUTAGE))} outage contacts and {measured.booked(NOT_NEEDED)} of"
        f" {len(measured.of(NOT_NEEDED))} contacts with a fault the customer could fix: at the"
        f" baseline's volumes {round(measured.unnecessary())} of {measured.baseline}"
        f" unnecessary visits, a cut of {measured.cut()}% in what the workflow itself books."
        f" The simulated customer went on asking, and {handed} of {len(insisted)} ended with a"
        f" person, who decides with the evidence in front of them. If every customer went on"
        f" asking like that, {end_to_end}. Needed visits: booked for"
        f" {measured.needed_booked_percent}% of cases, a median of"
        f" {median(waits) if waits else 'n/a'} minutes after the contact."
    )


def assumptions(measured: Measured) -> list[str]:
    asks = max((case.asked_again for case in measured.cases), default=0)
    legacy = measured.legacy
    return [
        f"Baseline: the {sum(legacy.values()):,} technician visits recorded in the"
        f" {measured.labelled_contacts:,} labelled contacts of the synthetic pack,"
        f" {measured.baseline:,} of them unnecessary ({legacy[BASELINE[OUTAGE]]:,} sent during"
        f" an outage, {legacy[BASELINE[NOT_NEEDED]]:,} for a fault the customer could have"
        " fixed). These are the generator's rates, not SkyLink's.",
        "What a contact's ground truth looks like to the platform is an assumption of the"
        " simulation: a needed visit is a line that keeps failing its remote test on a"
        " healthy node; an unneeded one for a device fault is a line that passes it.",
        "The simulated customer asks for a technician in the first message, takes the first"
        " slot whenever a visit is offered, and after every other answer asks again (here up"
        f" to {asks} more times) until a person takes over. That is the customer most likely"
        " to get a visit they do not need, not the usual one. A customer who does not ask"
        " again was not replayed here.",
        "The workflow books no visit without proof of a line fault, so what is left of the"
        " baseline's unnecessary visits is decided by people: by how many customers go on"
        " asking once they have been told of the outage or that their line is fine, and by"
        " how many of those the person sends a technician to. No replay can measure either."
        " The first table of this report shows what each combination leaves. If every"
        " customer went on asking and every person gave in, nothing would be cut.",
        "Only broadband contacts with a sound address mapping are replayed here (the"
        " baseline has no visits for mobile). Outage contacts with a defective mapping are"
        " covered by the detection evaluation, where none was sent device steps or a visit.",
        f"{len(measured.cases)} contacts were replayed. They show what the rules do; they are"
        " not a sample to take rates from.",
        f"The price of the hold is delay: a needed visit is booked about"
        f" {measured.hold_seconds // 60} minutes later than a process that books at once would.",
    ]


def numbers(measured: Measured) -> dict[str, Any]:
    waits = measured.waits
    return {
        "baseline_unnecessary_visits": measured.baseline,
        "unnecessary_visits_booked_by_the_workflow": round(measured.unnecessary(), 1),
        "cut_percent_in_what_the_workflow_books": measured.cut(),
        "contacts_that_ended_with_a_person": {
            kind: f"{measured.handed(kind)} of {len(measured.of(kind))}" for kind in UNNECESSARY
        },
        "contacts_that_ended_with_neither": {
            kind: measured.unsettled(kind) for kind in UNNECESSARY
        },
        "cut_percent_if_every_such_person_sends_a_technician": measured.cut(1.0),
        "largest_share_people_may_grant_percent": measured.most_a_person_may_grant,
        "needed_visits_booked_percent": measured.needed_booked_percent,
        "minutes_from_contact_to_booking": {
            "median": median(waits) if waits else None,
            "max": max(waits) if waits else None,
        },
        "hold_seconds": measured.hold_seconds,
        "cases_replayed": len(measured.cases),
    }


def add_tables(report: Report, measured: Measured) -> None:
    report.table(
        "End to end: what is left if customers go on asking and people give in",
        [
            "Share of the customers who end with a person that is sent a technician",
            "Unnecessary visits if every customer went on asking",
            "Cut",
        ],
        if_people_send_technicians(measured),
    )
    report.table(
        "By kind of contact",
        [
            "Kind",
            "Visits in the baseline",
            "Replayed",
            "Offered a visit",
            "Visit booked",
            "Ended with a person, no visit",
            "Ended with neither",
        ],
        by_kind(measured),
    )
    report.table(
        "Where contacts ended, and why they were queued for a person",
        ["Contact", "Replayed", "Visit booked", "Queued for a person", "Reasons"],
        where_contacts_ended(measured),
    )
    report.table(
        "What the customers were sent, in order",
        ["Contact", "Messages (templates)", "Contacts"],
        what_customers_were_sent(measured),
    )


def the_report(measured: Measured) -> Report:
    report = Report(
        name="truck_rolls",
        title="Unnecessary technician visits (simulation)",
        proves=(
            "Where the old process sent a technician for nothing, the workflow books none: not"
            " during an outage, though the customer asks again, and not for a fault the"
            " customer can fix themselves. A customer who goes on asking is handed to a person"
            " with the evidence. Where a visit was needed, it is still booked."
        ),
        target=(
            f"Unnecessary visits cut by at least {TARGET_PERCENT}% (brief), judged on the"
            " visits the workflow books; needed visits still booked for at least"
            f" {MIN_NEEDED_PERCENT}% of cases."
        ),
        headline=headline(measured),
        met=measured.met,
        verdict=(
            "met for the visits the workflow books; end to end it depends on decisions by"
            " people that a replay cannot measure"
            if measured.met
            else ""
        ),
        provider=stand_in(measured.provider),
        numbers=numbers(measured),
        assumptions=assumptions(measured),
    )
    add_tables(report, measured)
    return report


async def test_unnecessary_technician_visits_are_cut_by_at_least_half(
    platform: Platform, simulator: Simulator, datapack: Path
) -> None:
    mix = frozen("contact_mix.json")
    gaps = sorted(set(mix["before_detection"]["seconds_until_the_noc_declares_quantiles"].values()))
    hold = platform.runtime.settings.dispatch_hold_seconds

    plan = await start_contacts(platform, simulator, datapack, gaps)
    await run_the_clock(platform, plan, hold + 150)
    await insist(platform, plan)
    await read_back(platform, simulator, plan)

    measured = Measured(
        tuple(plan.cases),
        mix["legacy_technician_visits"],
        sum(mix["contacts"].values()),
        hold,
        platform.runtime.settings.llm_provider,
    )
    report = the_report(measured)
    report.write()

    assert measured.cut() >= TARGET_PERCENT, report.headline
    assert measured.needed_booked_percent >= MIN_NEEDED_PERCENT, report.headline
