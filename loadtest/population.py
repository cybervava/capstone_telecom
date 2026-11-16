"""Who calls during the surge, drawn from the synthetic data pack the simulator was seeded with.

A number of aggregation nodes fail, and every broadband node under them with them. Most callers
of the surge are customers on those nodes; the rest are customers elsewhere with the ordinary
mix of problems. Each caller makes one contact: the surge is of first contacts, the case in
which nobody may be turned away. All of it is synthetic data; no real customer is involved.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

WEB = "web"
CHANNEL = "channel"
AFFECTED_TEXTS = (
    "My internet has been down since morning",
    "No internet since morning, the router shows a red light",
    "wifi is not working at all",
    "broadband is down again, please fix it",
    "internet band hai subah se",
    "net nahi chal raha",
)
# What customers elsewhere write on an ordinary day, with how often.
ELSEWHERE_TEXTS = (
    ("My internet is very slow today", 3),
    ("wifi keeps dropping since yesterday", 2),
    ("connection keeps disconnecting every few minutes", 2),
    ("No internet since morning, the router shows a red light", 1),
    ("why is my bill higher this month", 1),
    ("I want to talk to a person", 1),
)


@dataclass(frozen=True)
class Caller:
    customer_id: str
    phone: str
    node_id: str
    affected: bool
    route: str
    text: str


@dataclass(frozen=True)
class Pack:
    """What the load test needs of the data pack."""

    # Customers the assistant can serve without a person: active, with an active broadband
    # service whose address is mapped to the node it really hangs on. (customer id, phone).
    by_node: Mapping[str, tuple[tuple[str, str], ...]]
    # The broadband nodes under each aggregation node.
    under: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class Cast:
    """The aggregation nodes that fail in a run, and its callers in the order they arrive."""

    roots: tuple[str, ...]
    callers: tuple[Caller, ...]


def load_pack(data_dir: Path) -> Pack:
    phones = {
        row["customer_id"]: row["phone"]
        for row in _rows(data_dir / "customers.csv")
        if row["status"] == "active"
    }
    true_node = {
        row["service_id"]: row["true_node_id"]
        for row in _rows(data_dir / "truth_service_node.csv")
        if row["mapping_defect"] == "none"
    }
    by_node: dict[str, list[tuple[str, str]]] = {}
    seen: set[str] = set()
    for row in _rows(data_dir / "services.csv"):
        customer_id, node_id = row["customer_id"], true_node.get(row["service_id"])
        usable = row["service_type"] == "broadband" and row["status"] == "active"
        if not usable or node_id is None or customer_id not in phones or customer_id in seen:
            continue
        seen.add(customer_id)
        by_node.setdefault(node_id, []).append((customer_id, phones[customer_id]))
    under: dict[str, list[str]] = {}
    for row in _rows(data_dir / "nodes.csv"):
        if row["node_type"] == "OLT" and row["status"] == "ACTIVE" and row["parent_node_id"]:
            under.setdefault(row["parent_node_id"], []).append(row["node_id"])
    return Pack(
        by_node={node: tuple(members) for node, members in by_node.items()},
        under={root: tuple(nodes) for root, nodes in under.items()},
    )


def cast(
    pack: Pack,
    arrivals: int,
    rng: random.Random,
    *,
    affected_share: float = 0.7,
    channel_share: float = 0.3,
    calling_share: float = 0.5,
    not_roots: Collection[str] = (),
    not_customers: Collection[str] = (),
) -> Cast:
    """Choose which aggregation nodes fail and who calls, one contact each.

    Enough nodes fail for ``calling_share`` of their customers to make up the affected callers.
    ``not_roots`` and ``not_customers`` keep a second run clear of the first: its outage is
    elsewhere and nobody calls twice.
    """
    wanted = round(arrivals * affected_share)
    roots = sorted(root for root in pack.under if root not in not_roots)
    rng.shuffle(roots)
    chosen: list[str] = []
    pool: list[tuple[str, str, str]] = []
    for root in roots:
        if len(pool) * calling_share >= wanted:
            break
        members = _members(pack, pack.under[root], not_customers)
        if members:
            chosen.append(root)
            pool.extend(members)
    if len(pool) < wanted:
        raise ValueError(f"the data pack has {len(pool)} customers to be affected, not {wanted}")
    out_of_play = {node for root in (*chosen, *not_roots) for node in pack.under.get(root, ())}
    elsewhere = _members(
        pack, [node for node in pack.by_node if node not in out_of_play], not_customers
    )
    if len(elsewhere) < arrivals - wanted:
        raise ValueError("the data pack is too small for the callers from elsewhere")
    texts, weights = zip(*ELSEWHERE_TEXTS, strict=True)
    callers = [
        _caller(person, rng, channel_share, affected=True, text=rng.choice(AFFECTED_TEXTS))
        for person in rng.sample(pool, wanted)
    ] + [
        _caller(person, rng, channel_share, affected=False, text=rng.choices(texts, weights)[0])
        for person in rng.sample(elsewhere, arrivals - wanted)
    ]
    rng.shuffle(callers)
    return Cast(roots=tuple(chosen), callers=tuple(callers))


def _members(
    pack: Pack, nodes: Collection[str], not_customers: Collection[str]
) -> list[tuple[str, str, str]]:
    return [
        (customer_id, phone, node)
        for node in nodes
        for customer_id, phone in pack.by_node.get(node, ())
        if customer_id not in not_customers
    ]


def _caller(
    person: tuple[str, str, str],
    rng: random.Random,
    channel_share: float,
    *,
    affected: bool,
    text: str,
) -> Caller:
    customer_id, phone, node = person
    route = CHANNEL if rng.random() < channel_share else WEB
    return Caller(customer_id, phone, node, affected, route, text)


def _rows(path: Path) -> Iterator[dict[str, str]]:
    """The rows of a file of the pack, one at a time: the files run to half a million rows."""
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)
