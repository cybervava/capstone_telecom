"""Network topology: AGG nodes with OLT (broadband) and TOWER (mobile) access children."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

from skylink_datagen.config import FULL_SCALE_CUSTOMERS
from skylink_datagen.wordlists import AREAS, CITIES, CITY_WEIGHTS, PIN_PREFIX

BROADBAND_SHARE = 0.45
MOBILE_SHARE = 0.82
SERVICES_PER_OLT = 250
SERVICES_PER_TOWER = 1500
ACCESS_PER_AGG = 12
MIN_AGG_PER_CITY = 2
DECOMMISSIONED_SHARE = 0.01
ACCESS_KINDS: tuple[tuple[str, str, str], ...] = (
    ("OLT", "OLT", "broadband"),
    ("TOWER", "TWR", "mobile"),
)
NODE_COLUMNS = (
    "node_id", "node_type", "service_type", "parent_node_id", "city", "area", "pincode", "status",
)  # fmt: skip

AreaList = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: str
    service_type: str
    parent_node_id: str
    city: str
    area: str
    pincode: str
    status: str

    @property
    def active(self) -> bool:
        return self.status == "ACTIVE"

    def row(self) -> tuple[str, ...]:
        return (self.node_id, self.node_type, self.service_type, self.parent_node_id,
                self.city, self.area, self.pincode, self.status)  # fmt: skip


@dataclass(frozen=True)
class CitySize:
    agg: int
    olt: int
    tower: int

    def active(self, node_type: str) -> int:
        return self.olt if node_type == "OLT" else self.tower


@dataclass(frozen=True)
class Topology:
    nodes: tuple[Node, ...]
    index: dict[str, int]
    areas: dict[str, AreaList]
    by_area: dict[tuple[str, int, str], tuple[int, ...]]
    by_city: dict[tuple[str, str], tuple[int, ...]]
    decommissioned: dict[str, tuple[int, ...]]
    children: dict[str, tuple[str, ...]]

    def active_of_type(self, node_type: str) -> tuple[int, ...]:
        return tuple(i for i, n in enumerate(self.nodes) if n.node_type == node_type and n.active)


def city_size(customers: int, weight: float) -> CitySize:
    share = customers * weight
    olt = max(1, round(share * BROADBAND_SHARE / SERVICES_PER_OLT))
    tower = max(1, round(share * MOBILE_SHARE / SERVICES_PER_TOWER))
    agg = max(MIN_AGG_PER_CITY, round((olt + tower) / ACCESS_PER_AGG))
    return CitySize(agg=agg, olt=max(olt, agg), tower=max(tower, agg))


def _decommission_plan(
    sizes: dict[str, CitySize], rng: random.Random
) -> dict[tuple[str, str], int]:
    plan: dict[tuple[str, str], int] = defaultdict(int)
    for node_type, _, _ in ACCESS_KINDS:
        total_active = sum(size.active(node_type) for size in sizes.values())
        count = max(1, round(total_active * DECOMMISSIONED_SHARE))
        order = list(CITIES)
        rng.shuffle(order)
        for i in range(count):
            plan[(order[i % len(order)], node_type)] += 1
    return dict(plan)


def _spread(buckets: int, items: int, rng: random.Random) -> list[int]:
    """Assign items to buckets so every bucket gets at least one item (items >= buckets)."""
    assignment = [i % buckets for i in range(items)]
    rng.shuffle(assignment)
    return assignment


def _access_nodes(
    city: str,
    kind: tuple[str, str, str],
    counts: tuple[int, int],
    aggs: tuple[str, ...],
    areas: AreaList,
    rng: random.Random,
) -> list[tuple[Node, int]]:
    node_type, code, service_type = kind
    active, decommissioned = counts
    total = active + decommissioned
    decom_positions = frozenset(rng.sample(range(total), decommissioned))
    parents, area_of = _spread(len(aggs), active, rng), _spread(len(areas), active, rng)
    out: list[tuple[Node, int]] = []
    k = 0
    for i in range(total):
        if i in decom_positions:
            parent, area, status = (
                rng.randrange(len(aggs)),
                rng.randrange(len(areas)),
                "DECOMMISSIONED",
            )
        else:
            parent, area, status = parents[k], area_of[k], "ACTIVE"
            k += 1
        node_id = f"{city}-{code}-{i + 1:04d}"
        name, pin = areas[area]
        node = Node(node_id, node_type, service_type, aggs[parent], city, name, pin, status)
        out.append((node, area))
    return out


def _city_nodes(
    city: str, size: CitySize, decom: dict[tuple[str, str], int], rng: random.Random
) -> tuple[AreaList, list[tuple[Node, int]]]:
    n_areas = min(len(AREAS[city]), size.olt, size.tower)
    areas = tuple((AREAS[city][i], f"{PIN_PREFIX[city]}{i + 1:03d}") for i in range(n_areas))
    out: list[tuple[Node, int]] = []
    for i in range(size.agg):
        name, pin = areas[i % n_areas]
        out.append((Node(f"{city}-AGG-{i + 1:04d}", "AGG", "", "", city, name, pin, "ACTIVE"), -1))
    agg_ids = tuple(node.node_id for node, _ in out)
    for kind in ACCESS_KINDS:
        counts = (size.active(kind[0]), decom.get((city, kind[0]), 0))
        out.extend(_access_nodes(city, kind, counts, agg_ids, areas, rng))
    return areas, out


def _index(areas: dict[str, AreaList], placed: list[tuple[Node, int]]) -> Topology:
    by_area: dict[tuple[str, int, str], list[int]] = defaultdict(list)
    by_city: dict[tuple[str, str], list[int]] = defaultdict(list)
    decom: dict[str, list[int]] = defaultdict(list)
    children: dict[str, list[str]] = defaultdict(list)
    for i, (node, area) in enumerate(placed):
        if node.node_type == "AGG":
            continue
        if not node.active:
            decom[node.service_type].append(i)
            continue
        by_area[(node.city, area, node.service_type)].append(i)
        by_city[(node.city, node.service_type)].append(i)
        children[node.parent_node_id].append(node.node_id)
    nodes = tuple(node for node, _ in placed)
    return Topology(
        nodes=nodes,
        index={node.node_id: i for i, node in enumerate(nodes)},
        areas=areas,
        by_area={k: tuple(v) for k, v in by_area.items()},
        by_city={k: tuple(v) for k, v in by_city.items()},
        decommissioned={k: tuple(v) for k, v in decom.items()},
        children={k: tuple(v) for k, v in children.items()},
    )


def build_topology(customers: int, rng: random.Random) -> Topology:
    if customers <= 0 or customers > 100 * FULL_SCALE_CUSTOMERS:
        msg = f"customers out of range: {customers}"
        raise ValueError(msg)
    sizes = {city: city_size(customers, w) for city, w in zip(CITIES, CITY_WEIGHTS, strict=True)}
    decom = _decommission_plan(sizes, rng)
    areas: dict[str, AreaList] = {}
    placed: list[tuple[Node, int]] = []
    for city in CITIES:
        areas[city], city_nodes = _city_nodes(city, sizes[city], decom, rng)
        placed.extend(city_nodes)
    return _index(areas, placed)
