"""Outage history: roots, affected nodes and timing."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from skylink_datagen.config import (
    WINDOW_DAYS,
    WINDOW_END,
    WINDOW_START,
    fmt_bool,
    fmt_ts,
    random_moment,
    scaled,
)
from skylink_datagen.nodes import Topology

OUTAGE_COLUMNS = (
    "outage_id", "root_node_id", "affected_node_ids", "service_types", "cause", "planned",
    "started_at", "detected_at", "initial_eta", "resolved_at", "duration_minutes",
)  # fmt: skip
UNPLANNED_CAUSES = (("FIBRE_CUT", "AGG"), ("POWER_FAILURE", "AGG"),
                    ("EQUIPMENT_FAULT", "OLT"), ("TOWER_FAULT", "TOWER"))  # fmt: skip
UNPLANNED_WEIGHTS = (25, 15, 35, 25)
PLANNED_ROOTS, PLANNED_ROOT_WEIGHTS = ("AGG", "OLT", "TOWER"), (30, 50, 20)
OUTAGES_PER_DAY, MIN_OUTAGES, PLANNED_SHARE = 10, 40, 0.10
MEDIAN_MINUTES, SIGMA = 120, 1.25
MIN_MINUTES, MAX_MINUTES = 15, 6 * 24 * 60
SERVICE_ORDER = ("broadband", "mobile")


@dataclass(frozen=True)
class Outage:
    outage_id: str
    root_node_id: str
    affected_node_ids: tuple[str, ...]
    service_types: tuple[str, ...]
    cause: str
    planned: bool
    started_at: datetime
    detected_at: datetime
    initial_eta: datetime
    resolved_at: datetime

    @property
    def duration_minutes(self) -> int:
        return int((self.resolved_at - self.started_at).total_seconds()) // 60

    def row(self) -> tuple[str, ...]:
        return (self.outage_id, self.root_node_id, "|".join(self.affected_node_ids),
                "|".join(self.service_types), self.cause, fmt_bool(self.planned),
                fmt_ts(self.started_at), fmt_ts(self.detected_at), fmt_ts(self.initial_eta),
                fmt_ts(self.resolved_at), str(self.duration_minutes))  # fmt: skip


def outage_count(customers: int) -> int:
    return scaled(OUTAGES_PER_DAY * WINDOW_DAYS, customers, MIN_OUTAGES)


def sample_unplanned_minutes(rng: random.Random) -> int:
    raw = rng.lognormvariate(math.log(MEDIAN_MINUTES), SIGMA)
    return min(MAX_MINUTES, max(MIN_MINUTES, round(raw)))


def _scope(topology: Topology, root: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    node = topology.nodes[root]
    affected = topology.children[node.node_id] if node.node_type == "AGG" else (node.node_id,)
    present = {topology.nodes[topology.index[n]].service_type for n in affected}
    return affected, tuple(s for s in SERVICE_ORDER if s in present)


def _initial_eta(rng: random.Random, detected: datetime, resolved: datetime) -> datetime:
    remaining = (resolved - detected).total_seconds()
    estimate = remaining * rng.lognormvariate(0.0, 0.6)
    estimate = min(72 * 3600.0, max(30 * 60.0, estimate))
    quarters = math.ceil(estimate / 900)
    return detected + timedelta(seconds=quarters * 900)


def _unplanned(rng: random.Random, topology: Topology, roots: dict[str, tuple[int, ...]]) -> Outage:
    cause, root_type = rng.choices(UNPLANNED_CAUSES, weights=UNPLANNED_WEIGHTS)[0]
    root = rng.choice(roots[root_type])
    minutes = sample_unplanned_minutes(rng)
    started = random_moment(rng, WINDOW_START, WINDOW_END - timedelta(minutes=minutes))
    detected = started + timedelta(seconds=rng.randint(60, 600))
    resolved = started + timedelta(minutes=minutes)
    affected, types = _scope(topology, root)
    eta = _initial_eta(rng, detected, resolved)
    node_id = topology.nodes[root].node_id
    return Outage("", node_id, affected, types, cause, False, started, detected, eta, resolved)


def _planned(rng: random.Random, topology: Topology, roots: dict[str, tuple[int, ...]]) -> Outage:
    root_type = rng.choices(PLANNED_ROOTS, weights=PLANNED_ROOT_WEIGHTS)[0]
    root = rng.choice(roots[root_type])
    day = WINDOW_START.date() + timedelta(days=rng.randrange(WINDOW_DAYS))
    minutes = rng.randrange(60, 241, 15)
    offset = rng.randrange(0, 240 - minutes + 1, 5)
    started = datetime.combine(day, time(1, 0), tzinfo=WINDOW_START.tzinfo)
    started += timedelta(minutes=offset)
    eta = started + timedelta(minutes=minutes)
    early = rng.choice((0, 0, 0, 5, 10)) if minutes > 60 else 0
    resolved = eta - timedelta(minutes=early)
    affected, types = _scope(topology, root)
    node_id = topology.nodes[root].node_id
    cause = "PLANNED_MAINTENANCE"
    return Outage("", node_id, affected, types, cause, True, started, started, eta, resolved)


def _assign_ids(drafts: list[Outage]) -> tuple[Outage, ...]:
    ordered = sorted(drafts, key=lambda o: (o.started_at, o.root_node_id, o.cause))
    per_day: dict[str, int] = {}
    out: list[Outage] = []
    for draft in ordered:
        day = f"{draft.started_at:%Y%m%d}"
        per_day[day] = per_day.get(day, 0) + 1
        out.append(replace(draft, outage_id=f"OUT-{day}-{per_day[day]:04d}"))
    return tuple(out)


def generate_outages(customers: int, topology: Topology, rng: random.Random) -> tuple[Outage, ...]:
    roots = {kind: topology.active_of_type(kind) for kind in ("AGG", "OLT", "TOWER")}
    total = outage_count(customers)
    planned = round(total * PLANNED_SHARE)
    drafts = [_planned(rng, topology, roots) for _ in range(planned)]
    drafts.extend(_unplanned(rng, topology, roots) for _ in range(total - planned))
    return _assign_ids(drafts)
