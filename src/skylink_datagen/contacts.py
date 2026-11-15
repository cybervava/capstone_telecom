"""Contact history: plan episodes that satisfy every transcript constraint, then render them.

An episode is a primary contact plus, for repeat contacts, one follow-up by the same customer
within 7 days. Customers are never reused within 8 days across episodes, so the
repeat_within_7d flag recomputed from the data equals the planned quota exactly.
"""

from __future__ import annotations

import bisect
import random
from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import accumulate

from skylink_datagen.config import WINDOW_END, WINDOW_START, random_moment, scaled
from skylink_datagen.network import Outage
from skylink_datagen.nodes import Topology
from skylink_datagen.reference import ReferenceIndex

TRANSCRIPTS_FULL, MIN_TRANSCRIPTS = 20_000, 500
LABEL_SHARES = (("outage", 0.45), ("device", 0.35), ("billing", 0.20))
REPEAT_RATE = {"outage": 0.25, "device": 0.12, "billing": 0.06}
PRE_DETECTION_SHARE = 0.20
MOBILE_SHARE = {"outage": 0.20, "device": 0.15, "billing": 0.45}
REPEAT_WINDOW, CUSTOMER_GAP = timedelta(days=7), timedelta(days=8)
MAX_ATTEMPTS = 500


class ContactPlanError(RuntimeError):
    """Raised when the constraints cannot be met (e.g. far too few customers)."""


@dataclass(frozen=True)
class Planned:
    ts: datetime
    customer: int
    service: int
    label: str
    outage_id: str | None


@dataclass(frozen=True)
class OutagePool:
    outages: tuple[Outage, ...]
    cum_weights: tuple[float, ...]

    def pick(self, rng: random.Random) -> Outage:
        point = rng.random() * self.cum_weights[-1]
        return self.outages[bisect.bisect_right(self.cum_weights, point)]


def transcript_count(customers: int) -> int:
    return scaled(TRANSCRIPTS_FULL, customers, MIN_TRANSCRIPTS)


def label_counts(total: int) -> dict[str, int]:
    outage, device = round(total * 0.45), round(total * 0.35)
    return {"outage": outage, "device": device, "billing": total - outage - device}


def _pool(outages: tuple[Outage, ...], topology: Topology, stype: str, pre: bool) -> OutagePool:
    chosen: list[Outage] = []
    weights: list[float] = []
    for outage in outages:
        if pre and outage.planned:
            continue
        nodes = sum(topology.nodes[topology.index[n]].service_type == stype
                    for n in outage.affected_node_ids)  # fmt: skip
        if nodes:
            chosen.append(outage)
            weights.append(nodes * min(outage.duration_minutes, 24 * 60) ** 0.5)
    if not chosen:
        msg = f"no outage affects {stype} services; cannot plan outage contacts"
        raise ContactPlanError(msg)
    return OutagePool(tuple(chosen), tuple(accumulate(weights)))


class ContactPlanner:
    def __init__(self, ref: ReferenceIndex, topology: Topology, outages: tuple[Outage, ...],
                 rng: random.Random) -> None:  # fmt: skip
        self._ref, self._topology, self._rng = ref, topology, rng
        self._pools = {(s, pre): _pool(outages, topology, s, pre)
                       for s in ("broadband", "mobile") for pre in (True, False)}  # fmt: skip
        self._windows: dict[int, list[tuple[datetime, datetime]]] = {}
        for outage in outages:
            for node_id in outage.affected_node_ids:
                node = topology.index[node_id]
                self._windows.setdefault(node, []).append((outage.started_at, outage.resolved_at))
        self._by_type = {flag: array("i", (i for i, m in enumerate(ref.service_is_mobile)
                                           if m == flag)) for flag in (0, 1)}  # fmt: skip
        self._used: dict[int, list[datetime]] = {}

    def _free(self, customer: int, times: tuple[datetime, ...]) -> bool:
        return all(abs(t - u) > CUSTOMER_GAP for u in self._used.get(customer, ()) for t in times)

    def _commit(self, service: int, times: tuple[datetime, ...], label: str,
                outage_id: str | None) -> list[Planned]:  # fmt: skip
        customer = self._ref.service_customer[service]
        self._used.setdefault(customer, []).extend(times)
        return [Planned(t, customer, service, label, outage_id) for t in times]

    def _in_outage(self, node: int, times: tuple[datetime, ...]) -> bool:
        return any(s <= t <= e for s, e in self._windows.get(node, ()) for t in times)

    def _outage_times(self, outage: Outage, pre: bool, follow: bool) -> tuple[datetime, ...] | None:
        rng = self._rng
        if pre:
            lag = int((outage.detected_at - outage.started_at).total_seconds())
            first = outage.started_at + timedelta(seconds=rng.randint(0, lag - 1))
        else:
            room = outage.resolved_at - outage.detected_at - timedelta(minutes=2 if follow else 0)
            if room <= timedelta(0):
                return None
            offset = int(room.total_seconds() * rng.random() ** 2)
            first = outage.detected_at + timedelta(seconds=offset)
        if not follow:
            return (first,)
        low = max(first, outage.detected_at) + timedelta(seconds=60)
        high = min(outage.resolved_at, first + REPEAT_WINDOW)
        return (first, random_moment(rng, low, high)) if high > low else None

    def outage_episode(self, pre: bool, follow: bool) -> list[Planned]:
        rng = self._rng
        for _ in range(MAX_ATTEMPTS):
            stype = "mobile" if rng.random() < MOBILE_SHARE["outage"] else "broadband"
            outage = self._pools[(stype, pre)].pick(rng)
            nodes = [
                self._topology.index[n]
                for n in outage.affected_node_ids
                if self._topology.nodes[self._topology.index[n]].service_type == stype
            ]
            services = self._ref.node_services.get(rng.choice(nodes))
            times = self._outage_times(outage, pre, follow)
            if not services or times is None:
                continue
            service = rng.choice(services)
            if self._free(self._ref.service_customer[service], times):
                return self._commit(service, times, "outage", outage.outage_id)
        raise ContactPlanError("could not place an outage contact; increase --customers")

    def _free_times(self, follow: bool) -> tuple[datetime, ...]:
        rng = self._rng
        if not follow:
            return (random_moment(rng, WINDOW_START, WINDOW_END),)
        first = random_moment(rng, WINDOW_START, WINDOW_END - REPEAT_WINDOW)
        return (
            first,
            first + timedelta(seconds=rng.randint(3600, int(REPEAT_WINDOW.total_seconds()))),
        )

    def other_episode(self, label: str, follow: bool) -> list[Planned]:
        rng = self._rng
        for _ in range(MAX_ATTEMPTS):
            pool = self._by_type[1 if rng.random() < MOBILE_SHARE[label] else 0]
            service = rng.choice(pool)
            times = self._free_times(follow)
            if label == "device" and self._in_outage(self._ref.service_node[service], times):
                continue
            if self._free(self._ref.service_customer[service], times):
                return self._commit(service, times, label, None)
        raise ContactPlanError(f"could not place a {label} contact; increase --customers")

    def plan(self, total: int) -> list[Planned]:
        planned: list[Planned] = []
        for label, count in label_counts(total).items():
            repeats = round(count * REPEAT_RATE[label])
            episodes = count - repeats
            pre: frozenset[int] = frozenset()
            if label == "outage":
                quota = round(count * PRE_DETECTION_SHARE)
                pre = frozenset(self._rng.sample(range(episodes), quota))
            for k in range(episodes):
                follow = k < repeats
                if label == "outage":
                    planned.extend(self.outage_episode(k in pre, follow))
                else:
                    planned.extend(self.other_episode(label, follow))
        return sorted(planned, key=lambda c: (c.ts, c.customer, c.service))


def repeat_flags(contacts: list[Planned]) -> list[bool]:
    """True when the same customer has a later contact within 7 days."""
    by_customer: dict[int, list[datetime]] = {}
    for contact in contacts:
        by_customer.setdefault(contact.customer, []).append(contact.ts)
    flags = []
    for contact in contacts:
        later = by_customer[contact.customer]
        flags.append(any(timedelta(0) < t - contact.ts <= REPEAT_WINDOW for t in later))
    return flags
