"""network_events.jsonl: outage lifecycle events and alarms, one ordered stream."""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from skylink_datagen.config import WINDOW_END, WINDOW_START, fmt_ts, random_moment
from skylink_datagen.network import Outage
from skylink_datagen.nodes import Topology

ALARM_TYPES = ("LOS", "LINK_DOWN", "HIGH_BER", "POWER", "HIGH_TEMP")
CAUSE_ALARMS = {
    "FIBRE_CUT": ("LOS", "LINK_DOWN"),
    "POWER_FAILURE": ("POWER",),
    "EQUIPMENT_FAULT": ("LINK_DOWN", "HIGH_TEMP", "HIGH_BER"),
    "TOWER_FAULT": ("POWER", "LINK_DOWN"),
    "PLANNED_MAINTENANCE": ("LINK_DOWN",),
}
NOISE_PER_OUTAGE_ALARM = 3
NOISE_SEVERITIES, NOISE_SEVERITY_WEIGHTS = ("critical", "major", "minor"), (15, 40, 45)
UPDATE_COUNTS, UPDATE_WEIGHTS = (0, 1, 2, 3), (30, 35, 22, 13)
PRIORITY = {"ALARM_RAISED": 0, "OUTAGE_STARTED": 1, "OUTAGE_UPDATED": 2,
            "OUTAGE_RESOLVED": 3, "ALARM_CLEARED": 4}  # fmt: skip


@dataclass(frozen=True)
class Alarm:
    node_id: str
    alarm_type: str
    severity: str
    raised_at: datetime
    cleared_at: datetime


@dataclass(frozen=True)
class Pending:
    """An event before it gets its position (seq) and id."""

    emitted_at: datetime
    kind: str
    order: int
    node_ids: tuple[str, ...]
    extra: tuple[tuple[str, Any], ...]

    def sort_key(self) -> tuple[datetime, int, int]:
        return (self.emitted_at, PRIORITY[self.kind], self.order)


def _outage_alarms(rng: random.Random, outage: Outage) -> list[Alarm]:
    lag = int((outage.detected_at - outage.started_at).total_seconds())
    raised = outage.started_at + timedelta(seconds=rng.randint(0, max(lag - 1, 0)))
    kind = rng.choice(CAUSE_ALARMS[outage.cause])
    alarms = [Alarm(outage.root_node_id, kind, "critical", raised, outage.resolved_at)]
    if rng.random() < 0.3:
        node = rng.choice(outage.affected_node_ids)
        second = rng.choice(CAUSE_ALARMS[outage.cause])
        alarms.append(Alarm(node, second, "major", raised, outage.resolved_at))
    return alarms


def _noise_alarm(rng: random.Random, node_ids: tuple[str, ...]) -> Alarm:
    raised = random_moment(rng, WINDOW_START, WINDOW_END - timedelta(hours=2))
    severity = rng.choices(NOISE_SEVERITIES, weights=NOISE_SEVERITY_WEIGHTS)[0]
    cleared = raised + timedelta(seconds=rng.randint(60, 90 * 60))
    return Alarm(rng.choice(node_ids), rng.choice(ALARM_TYPES), severity, raised, cleared)


def _alarm_events(alarms: list[Alarm], start_order: int) -> list[Pending]:
    ordered = sorted(alarms, key=lambda a: (a.raised_at, a.node_id, a.alarm_type, a.severity))
    events: list[Pending] = []
    for i, alarm in enumerate(ordered, start=1):
        extra = (("alarm_id", f"ALM-{i:07d}"), ("alarm_type", alarm.alarm_type),
                 ("severity", alarm.severity))  # fmt: skip
        nodes = (alarm.node_id,)
        order = start_order + 2 * i
        events.append(Pending(alarm.raised_at, "ALARM_RAISED", order, nodes, extra))
        events.append(Pending(alarm.cleared_at, "ALARM_CLEARED", order + 1, nodes, extra))
    return events


def _eta_updates(rng: random.Random, outage: Outage) -> list[tuple[datetime, datetime, str]]:
    span = int((outage.resolved_at - outage.detected_at).total_seconds())
    count = min(rng.choices(UPDATE_COUNTS, weights=UPDATE_WEIGHTS)[0], max(span // 120 - 1, 0))
    moments = sorted(rng.sample(range(60, span - 59), count)) if count else []
    updates: list[tuple[datetime, datetime, str]] = []
    previous = outage.initial_eta
    for offset in moments:
        at = outage.detected_at + timedelta(seconds=offset)
        error = (outage.resolved_at - at).total_seconds() * rng.uniform(-0.3, 0.6)
        eta = max(outage.resolved_at + timedelta(seconds=error), at + timedelta(minutes=10))
        eta = eta.replace(second=0, microsecond=0) + timedelta(minutes=5 - eta.minute % 5)
        if eta == previous:
            eta += timedelta(minutes=15)
        source = "field_update" if rng.random() < 0.7 else "noc_estimate"
        updates.append((at, eta, source))
        previous = eta
    return updates


def _outage_events(rng: random.Random, outage: Outage, order: int) -> list[Pending]:
    base = (("outage_id", outage.outage_id), ("service_types", list(outage.service_types)),
            ("cause", outage.cause), ("planned", outage.planned),
            ("started_at", fmt_ts(outage.started_at)))  # fmt: skip
    steps = [(outage.detected_at, "OUTAGE_STARTED", outage.initial_eta, "noc_estimate")]
    steps.extend((at, "OUTAGE_UPDATED", eta, src) for at, eta, src in _eta_updates(rng, outage))
    _, _, last_eta, last_source = steps[-1]
    steps.append((outage.resolved_at, "OUTAGE_RESOLVED", last_eta, last_source))
    events: list[Pending] = []
    for version, (at, kind, eta, source) in enumerate(steps, start=1):
        resolved = fmt_ts(outage.resolved_at) if kind == "OUTAGE_RESOLVED" else None
        extra = (*base[:1], ("version", version), *base[1:], ("eta", fmt_ts(eta)),
                 ("eta_source", source), ("resolved_at", resolved))  # fmt: skip
        events.append(Pending(at, kind, order + version, outage.affected_node_ids, extra))
    return events


def _event_ids(rng: random.Random, count: int) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    while len(ids) < count:
        candidate = f"evt_{rng.getrandbits(64):016x}"
        if candidate not in seen:
            seen.add(candidate)
            ids.append(candidate)
    return ids


def build_events(
    outages: tuple[Outage, ...], topology: Topology, rng: random.Random
) -> list[Pending]:
    pending: list[Pending] = []
    outage_alarms: list[Alarm] = []
    for i, outage in enumerate(outages):
        outage_alarms.extend(_outage_alarms(rng, outage))
        pending.extend(_outage_events(rng, outage, i * 10))
    active = tuple(n.node_id for n in topology.nodes if n.active)
    noise = [_noise_alarm(rng, active) for _ in range(NOISE_PER_OUTAGE_ALARM * len(outage_alarms))]
    pending.extend(_alarm_events(outage_alarms + noise, len(outages) * 10))
    return sorted(pending, key=Pending.sort_key)


def render_events(pending: list[Pending], rng: random.Random) -> Iterator[dict[str, Any]]:
    for seq, (event, event_id) in enumerate(
        zip(pending, _event_ids(rng, len(pending)), strict=True), start=1
    ):
        record: dict[str, Any] = {"seq": seq, "event_id": event_id, "type": event.kind,
                                  "emitted_at": fmt_ts(event.emitted_at),
                                  "node_ids": list(event.node_ids)}  # fmt: skip
        record.update(event.extra)
        yield record
