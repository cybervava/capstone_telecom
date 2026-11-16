"""Evaluation: the curveball of the brief, in every way the Network Monitoring API can fail.

"The Network Monitoring API goes down during a live outage, and Regulatory Affairs adds that
every outage contact must get a complaint reference number within 2 minutes. Groups must show
safe degraded mode (no false 'all clear' messages, no truck dispatch on stale data) and meet
the reference-number rule."

One scene is played eight times. The API fails in one of four ways, and the NOC's event feed
either goes on working or has said nothing since before the outage:

* Before the failure two nodes are in a declared outage and a customer on each has been told.
  On a healthy node a customer's line has failed its test through the whole hold, and a
  technician visit has been offered.
* The API fails. That customer picks a slot. More customers of the outage nodes write in, and
  so do customers on a healthy node. The NOC resolves one of the outages.
* The API comes back, and the scene goes on for as long as the slow watch on a known outage
  takes: with a silent feed that watch is what tells a waiting customer that the outage is over.

Scored against what the simulator knows to be true, not against what the platform believed:
nobody is told that the network is clear, no visit is offered or booked without a status read
at that moment, no credit is applied without an approval, every contact has its reference,
and no node gets a second incident. What customers were told instead, and what they heard
once the API was back, is reported beside it. So is what became of the visit: chosen while
nothing could be verified, it must not be booked then.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from tests.support.platform import Platform
from tests.support.stack import Simulator

from evals.replay import OUTAGE_TOLD, contact, customers, pack_customers
from evals.report import Report, stand_in
from skylink_care.db.actions import CREDIT_APPLY, INCIDENT_OPEN, VISIT_BOOK, ActionStatus
from skylink_care.domain.reference import is_valid_reference
from skylink_care.guardrails.claims import CLAIMS, Claim

REFERENCE_LIMIT_MS = 120_000
# How the API fails, as the simulator's fault injection names it.
FAULTS: dict[str, dict[str, Any]] = {
    "refuses every call (503)": {"mode": "down"},
    "answers too late (504 after a hold)": {"mode": "timeout", "hold_seconds": 0.001},
    "fails every call (500)": {"mode": "error_rate", "error_rate": 1.0},
    "fails half of its calls, at random (500)": {"mode": "error_rate", "error_rate": 0.5},
}
# With these the status cannot be read at all, so nothing may be said to be clear. With the
# last, a read that succeeds is evidence like any other.
NEVER_READABLE = frozenset(list(FAULTS)[:3])
FEEDS = {"working": True, "silent since before the outage": False}
SAYS_CLEAR = frozenset(
    template for template, claim in CLAIMS.items() if claim in {Claim.CLEAR, Claim.VISIT}
)
CANNOT_VERIFY = "network_unknown.v1"
RESTORED = "outage_restored.v1"
DOWN = "My internet has been down since morning"
ASKS = "is there an outage in my area? my wifi is not working"
OUTAGE_MINUTES = 300  # long enough for the placeholder policy to owe a credit


@dataclass
class Scene:
    """Who is in the scene, and when the API was failing (by the wall clock records carry)."""

    resolved_node: str
    lasting_node: str
    outage_ids: dict[str, str] = field(default_factory=dict)  # node -> its outage
    # Who writes in once the API is failing: (node, customers) on the outage nodes, and the
    # customers on the healthy node.
    to_write_from_outages: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)
    to_write_from_a_healthy_node: list[dict[str, Any]] = field(default_factory=list)
    told_before: dict[UUID, str] = field(default_factory=dict)  # contact -> its node
    offered_a_visit: UUID | None = None
    on_outage_nodes: dict[UUID, str] = field(default_factory=dict)
    on_a_healthy_node: list[UUID] = field(default_factory=list)
    failing_from: datetime | None = None
    failing_until: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def everyone(self) -> list[UUID]:
        visit = [self.offered_a_visit] if self.offered_a_visit else []
        return [*self.told_before, *visit, *self.on_outage_nodes, *self.on_a_healthy_node]


@pytest.fixture(scope="module")
def scoreboard(datapack: Path) -> Iterator[list[dict[str, Any]]]:
    """What each playing of the scene scored. The report is written when all are through."""
    rows: list[dict[str, Any]] = []
    yield rows
    write_report(rows, datapack)


@pytest.mark.parametrize("feed", list(FEEDS))
@pytest.mark.parametrize("fault", list(FAULTS))
async def test_the_curveball(
    platform: Platform,
    simulator: Simulator,
    datapack: Path,
    scoreboard: list[dict[str, Any]],
    fault: str,
    feed: str,
) -> None:
    feed_works = FEEDS[feed]
    scene = await before_the_failure(platform, simulator, datapack, feed_works)
    await while_it_fails(platform, simulator, scene, FAULTS[fault], feed_works)
    row = {"fault": fault, "feed": feed, **await score(platform, simulator, scene, fault)}
    await after_it_is_back(platform, simulator, scene, feed_works)
    row |= await score_recovery(platform, scene)
    scoreboard.append(row)

    assert row["violations"] == [], row["violations"]


# --- the scene ------------------------------------------------------------------------------------


async def step(platform: Platform, feed_works: bool, seconds: int = 0) -> None:
    """Let time pass and everything that is due happen. A working feed is read each time, as
    its consumer does; a silent one is never read, so what it last said grows stale."""
    if seconds:
        platform.advance(seconds)
    if feed_works:
        await platform.feed()
    await platform.settle(rounds=60)


async def before_the_failure(
    platform: Platform, simulator: Simulator, datapack: Path, feed_works: bool
) -> Scene:
    settings = platform.runtime.settings
    by_node: dict[str, list[dict[str, Any]]] = {}
    for person in customers(datapack, "broadband", "none"):
        by_node.setdefault(person["true_node_id"], []).append(person)
    nodes = sorted(node for node, people in by_node.items() if len(people) >= 3)
    resolved_node, lasting_node, line_node, healthy_node = nodes[:4]
    scene = Scene(resolved_node, lasting_node)

    # A line that fails on a healthy node, held for the NOC's detection window, still failing.
    failing = by_node[line_node][0]
    await simulator.line_fault(failing["service_id"], fault=True)
    scene.offered_a_visit = await contact(platform, failing, DOWN)
    await step(platform, feed_works)
    for _ in range(settings.dispatch_hold_seconds // settings.network_recheck_seconds + 1):
        await step(platform, feed_works, settings.network_recheck_seconds)
    offer = (await templates(platform, scene.offered_a_visit))[-1]
    assert offer == "visit_offer.v1", f"the scene needs a visit on offer, not {offer}"

    for node in (resolved_node, lasting_node):
        started = await simulator.start_outage(node, started_minutes_ago=OUTAGE_MINUTES)
        scene.outage_ids[node] = str(started["outage_id"])
    await step(platform, feed_works)
    for node in (resolved_node, lasting_node):
        scene.told_before[await contact(platform, by_node[node][0], DOWN)] = node
    await step(platform, feed_works)
    for person in by_node[healthy_node][:2]:
        await simulator.line_fault(person["service_id"], fault=False)
    scene.to_write_from_outages = [
        (node, by_node[node][1:3]) for node in (resolved_node, lasting_node)
    ]
    scene.to_write_from_a_healthy_node = by_node[healthy_node][:2]
    return scene


async def while_it_fails(
    platform: Platform,
    simulator: Simulator,
    scene: Scene,
    fault: dict[str, Any],
    feed_works: bool,
) -> None:
    recheck = platform.runtime.settings.network_recheck_seconds
    scene.failing_from = datetime.now(UTC)
    await simulator.fault("network", **fault)

    assert scene.offered_a_visit is not None
    await platform.say(scene.offered_a_visit, "1")  # the customer picks the first slot
    for node, on_node in scene.to_write_from_outages:
        for person in on_node:
            scene.on_outage_nodes[await contact(platform, person, DOWN)] = node
    for person, text in zip(scene.to_write_from_a_healthy_node, (DOWN, ASKS), strict=True):
        scene.on_a_healthy_node.append(await contact(platform, person, text))
    await step(platform, feed_works)

    await simulator.resolve_outage(scene.outage_ids[scene.resolved_node])
    scene.resolved_at = datetime.now(UTC)
    for _ in range(3):
        await step(platform, feed_works, recheck)
    scene.failing_until = datetime.now(UTC)


async def after_it_is_back(
    platform: Platform, simulator: Simulator, scene: Scene, feed_works: bool
) -> None:
    settings = platform.runtime.settings
    await simulator.fault("network", "ok")
    for _ in range(3):
        await step(platform, feed_works, settings.network_recheck_seconds)
    # Whoever was told of an outage hears of its end through the feed. With the feed silent,
    # the slow watch on the live status is what tells them: the scene gives it its time.
    await step(platform, feed_works, settings.outage_recheck_seconds)
    await step(platform, feed_works, settings.network_recheck_seconds)


# --- the score ------------------------------------------------------------------------------------


async def templates(platform: Platform, contact_id: UUID) -> list[str]:
    sent = await platform.runtime.messages.list_for_contact(contact_id)
    return [str(m.meta.get("template_id")) for m in sent if m.direction == "outbound"]


async def said_while_failing(platform: Platform, scene: Scene, contact_id: UUID) -> list[Any]:
    assert scene.failing_from is not None
    assert scene.failing_until is not None
    return [
        message
        for message in await platform.runtime.messages.list_for_contact(contact_id)
        if message.direction == "outbound"
        and scene.failing_from <= message.created_at < scene.failing_until
    ]


async def score(
    platform: Platform, simulator: Simulator, scene: Scene, fault: str
) -> dict[str, Any]:
    """What happened while the API was failing, against what was true."""
    late_or_missing, clear_said, clear_to_outage = await references_and_claims(platform, scene)
    booked, unproven = await visits(platform, scene)
    assert scene.offered_a_visit is not None
    to_the_visit_customer = await said_while_failing(platform, scene, scene.offered_a_visit)
    first = [(await templates(platform, one))[:1] for one in scene.on_outage_nodes]
    healthy_first = [(await templates(platform, one))[:1] for one in scene.on_a_healthy_node]
    state = (await simulator.state())["tables"]
    row: dict[str, Any] = {
        "contacts": len(scene.everyone),
        "references_late_or_missing": late_or_missing,
        "said_clear": clear_said,
        "said_clear_to_customers_in_an_outage": clear_to_outage,
        "visits_booked": booked,
        "visits_booked_without_a_status_read": unproven,
        # What the customer who picked a slot was told while the API was failing.
        "visit_customer_told_meanwhile": [
            str(message.meta.get("template_id")) for message in to_the_visit_customer
        ],
        "credits_applied": await credits_applied(platform, scene) + int(state["credits"]),
        "most_incidents_on_one_node": await most_incidents_on_a_node(platform),
        "outage_customers": len(first),
        "outage_customers_told_of_the_outage": sum(
            1 for sent in first if sent and sent[0] in OUTAGE_TOLD
        ),
        "outage_customers_told_it_cannot_be_verified": sum(
            1 for sent in first if sent == [CANNOT_VERIFY]
        ),
        "healthy_node_customers_told_it_cannot_be_verified": sum(
            1 for sent in healthy_first if sent == [CANNOT_VERIFY]
        ),
    }
    row["violations"] = violations(row, fault)
    return row


async def references_and_claims(platform: Platform, scene: Scene) -> tuple[int, int, int]:
    """Contacts without a reference in time; messages that said the network is clear while
    the API was failing; and how many of those went to a customer whose node was truly in an
    outage at that moment (the lasting one throughout, the other until the NOC resolved it)."""
    late_or_missing = clear_said = clear_to_outage = 0
    for contact_id in scene.everyone:
        record = await platform.runtime.contacts.get(contact_id)
        in_time = record is not None and record.reference_latency_ms <= REFERENCE_LIMIT_MS
        if record is None or not is_valid_reference(record.complaint_ref) or not in_time:
            late_or_missing += 1
        node = scene.on_outage_nodes.get(contact_id) or scene.told_before.get(contact_id)
        for message in await said_while_failing(platform, scene, contact_id):
            if message.meta.get("template_id") not in SAYS_CLEAR:
                continue
            clear_said += 1
            resolved = scene.resolved_at is not None and message.created_at >= scene.resolved_at
            still_out = node == scene.lasting_node or (node == scene.resolved_node and not resolved)
            clear_to_outage += int(still_out)
    return late_or_missing, clear_said, clear_to_outage


def violations(row: dict[str, Any], fault: str) -> list[str]:
    """Which of the brief's rules a playing of the scene broke. An empty list is a pass."""
    found = []
    if row["references_late_or_missing"]:
        found.append("a contact without a reference within two minutes")
    if row["said_clear_to_customers_in_an_outage"]:
        found.append("a customer in an outage was told the network is clear")
    if fault in NEVER_READABLE and row["said_clear"]:
        found.append("the network was said to be clear while its status could not be read")
    if row["visits_booked_without_a_status_read"]:
        found.append("a visit was booked without a status read at that moment")
    if fault in NEVER_READABLE and row["visits_booked"]:
        found.append("a visit was booked while the status could not be read")
    if row["credits_applied"]:
        found.append("a credit was applied without an approval")
    if row["most_incidents_on_one_node"] > 1:
        found.append("a node got more than one incident")
    return found


async def visits(platform: Platform, scene: Scene) -> tuple[int, int]:
    """Visits booked after the API began to fail, and how many of them rest on no status read
    made after the customer chose: the read the executor owes before it books."""
    assert scene.failing_from is not None
    booked = unproven = 0
    for contact_id in scene.everyone:
        actions = await platform.runtime.actions.list_for_contact(contact_id)
        if not any(
            action.action_type == VISIT_BOOK and action.status is ActionStatus.SUCCEEDED
            for action in actions
        ):
            continue
        booked += 1
        trail = await platform.runtime.audit.for_contact(contact_id)
        read_since = any(
            record.step == "tool:network.node_status"
            and record.outcome == "OK"
            and record.ts >= scene.failing_from
            for record in trail
        )
        unproven += int(not read_since)
    return booked, unproven


async def credits_applied(platform: Platform, scene: Scene) -> int:
    """Credit writes that went through. Nobody approves anything in the scene."""
    count = 0
    for contact_id in scene.everyone:
        for action in await platform.runtime.actions.list_for_contact(contact_id):
            count += int(
                action.action_type == CREDIT_APPLY and action.status is ActionStatus.SUCCEEDED
            )
    return count


async def most_incidents_on_a_node(platform: Platform) -> int:
    async with platform.runtime.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS opened FROM actions a JOIN node_episodes e USING (episode_id)"
            " WHERE a.action_type = %s GROUP BY e.node_id ORDER BY 1 DESC LIMIT 1",
            (INCIDENT_OPEN,),
        )
        row = await cursor.fetchone()
    return int(row["opened"]) if row else 0


async def score_recovery(platform: Platform, scene: Scene) -> dict[str, Any]:
    """What customers had heard once the API was back, and whether anyone was left hanging."""
    lasting = [one for one, node in scene.on_outage_nodes.items() if node == scene.lasting_node]
    over = [one for one, node in scene.told_before.items() if node == scene.resolved_node]
    told_lasting = told_over = unanswered = with_a_person = 0
    for contact_id in scene.everyone:
        sent = await templates(platform, contact_id)
        record = await platform.runtime.contacts.get(contact_id)
        unanswered += int(not sent)
        with_a_person += int(record is not None and record.status == "HANDOFF")
        told_lasting += int(contact_id in lasting and bool(set(sent) & OUTAGE_TOLD))
        told_over += int(contact_id in over and RESTORED in sent)
    assert scene.offered_a_visit is not None
    visit_booked, _ = await visits(platform, scene)
    return {
        # The visit once the API was back: what the customer was told last, and whether the
        # visit stands booked (on a status read after they chose, or the row above failed).
        "visit_customer_told_last": (await templates(platform, scene.offered_a_visit))[-1],
        "visit_booked_in_the_end": visit_booked,
        "told_of_the_lasting_outage_in_the_end": told_lasting,
        "of_customers_on_the_lasting_outage": len(lasting),
        "told_the_resolved_outage_is_over": told_over,
        "of_customers_waiting_on_the_resolved_outage": len(over),
        "left_without_any_answer": unanswered,
        "with_a_person": with_a_person,
    }


# --- the report -----------------------------------------------------------------------------------


PROVES = (
    "That the degraded mode is safe however the API fails, with the event feed working and"
    " without it: every contact has its complaint reference, nobody is told that the network"
    " is clear, no technician is sent on a status nobody could read, no credit is applied"
    " without an approval and no node gets a second incident."
)
TARGET = (
    "In every playing of the scene: zero contacts without a reference within two minutes, zero"
    " messages saying the network is clear to a customer in an outage (and none at all while"
    " the status cannot be read), zero visits booked without a status read at that moment,"
    " zero credits applied without an approval, at most one incident on a node."
)


def write_report(rows: list[dict[str, Any]], datapack: Path) -> None:
    played = len(FAULTS) * len(FEEDS)
    broken = [
        f"{row['fault']}, feed {row['feed']}: {'; '.join(row['violations'])}"
        for row in rows
        if row["violations"]
    ]
    report = Report(
        name="curveball",
        title="The curveball: the Network Monitoring API fails during a live outage",
        proves=PROVES,
        target=TARGET,
        headline=headline(rows, played, broken),
        met=len(rows) == played and not broken,
        provider=stand_in("offline"),
        assumptions=assumptions(datapack),
    )
    report.table("The rules of the brief, while the API was failing", RULE_COLUMNS, rules(rows))
    report.table(
        "What customers were told instead, and once the API was back", TOLD_COLUMNS, told(rows)
    )
    report.numbers = {"playings": rows, "expected_playings": played}
    report.write()


def assumptions(datapack: Path) -> list[str]:
    return [
        f"Replayed in process on the {pack_customers(datapack):,}-customer pack: the real"
        " workflow, orchestrator, executor and feed consumer against the stand-in gateway,"
        " whose fault injection makes the Network Monitoring API fail. The scene is described"
        " at the top of `evals/test_curveball.py`.",
        "Scored against what the simulator knows to be true (which nodes were in an outage and"
        " when), and against the platform's own ledger and audit trail for writes.",
        "The reference is issued by the intake before anything is asked of any other system,"
        " so its time does not depend on the failure; the time counted is the one the platform"
        " recorded for each contact. Under load it is measured by the load test (`loadtest/`).",
        "With the API failing half of its calls, a read that succeeds is evidence: a customer"
        " on a healthy node may then be told that no outage is reported, and a visit may be"
        " booked on a status read at that moment. Neither is counted against the run. Telling"
        " a customer in an outage that the network is clear would be. Which calls fail is left"
        " to chance, so these two playings differ from run to run in what customers are told;"
        " the rules must hold whichever calls fail.",
        "After the API is back the scene runs for the slow watch's interval (15 minutes). A"
        " customer told of an outage normally hears of its end through the feed; with the feed"
        " silent, that watch on the live status is what tells them.",
        "A silent feed is one whose consumer has read nothing since before the outage: what it"
        " last said is stale and is not used as evidence.",
    ]


RULE_COLUMNS = [
    "The API",
    "The feed",
    "Contacts",
    "Without a reference in 2 min",
    "Told the network is clear (to a customer in an outage)",
    "Visits booked (without a status read)",
    "Credits applied",
    "Most incidents on a node",
]
TOLD_COLUMNS = [
    "The API",
    "The feed",
    "On an outage node, while it failed: told of the outage / told it cannot be verified",
    "On a healthy node, while it failed: told it cannot be verified",
    "After it was back: told of the outage that lasts",
    "After it was back: told the other is over",
    "The visit chosen while it failed: told then / in the end",
    "Left without an answer",
    "With a person",
]


def rules(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            row["fault"],
            row["feed"],
            row["contacts"],
            row["references_late_or_missing"],
            f"{row['said_clear']} ({row['said_clear_to_customers_in_an_outage']})",
            f"{row['visits_booked']} ({row['visits_booked_without_a_status_read']})",
            row["credits_applied"],
            row["most_incidents_on_one_node"],
        ]
        for row in rows
    ]


def told(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            row["fault"],
            row["feed"],
            f"{row['outage_customers_told_of_the_outage']} /"
            f" {row['outage_customers_told_it_cannot_be_verified']}"
            f" of {row['outage_customers']}",
            f"{row['healthy_node_customers_told_it_cannot_be_verified']} of 2",
            f"{row['told_of_the_lasting_outage_in_the_end']}"
            f" of {row['of_customers_on_the_lasting_outage']}",
            f"{row['told_the_resolved_outage_is_over']}"
            f" of {row['of_customers_waiting_on_the_resolved_outage']}",
            f"{' · '.join(row['visit_customer_told_meanwhile']) or 'nothing'} /"
            f" {row['visit_customer_told_last']}"
            f"{' (booked)' if row['visit_booked_in_the_end'] else ''}",
            row["left_without_any_answer"],
            row["with_a_person"],
        ]
        for row in rows
    ]


def headline(rows: list[dict[str, Any]], played: int, broken: list[str]) -> str:
    if len(rows) != played:
        return f"Only {len(rows)} of {played} playings of the scene ran to the end."
    if broken:
        return "Rules broken: " + " | ".join(broken)
    contacts = sum(row["contacts"] for row in rows)
    told = sum(row["outage_customers_told_of_the_outage"] for row in rows)
    unsure = sum(row["outage_customers_told_it_cannot_be_verified"] for row in rows)
    return (
        f"In all {played} playings ({contacts} contacts) every rule held: every contact had"
        " its reference, nobody in an outage was told the network is clear, no visit was"
        " booked without a status read, no credit was applied and no node got a second"
        f" incident. Of the customers on an outage node who wrote in while the API was"
        f" failing, {told} were told of the outage on the evidence there was (the feed, or a"
        f" status read that succeeded) and {unsure} were told that the status cannot be"
        " verified."
    )
