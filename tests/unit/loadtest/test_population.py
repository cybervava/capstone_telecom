"""Who calls in a load run: drawn from the data pack, once each, mostly from the failed nodes."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from loadtest.population import CHANNEL, WEB, Pack, cast, load_pack

ROOTS = ("BLR-AGG-0001", "BLR-AGG-0002", "PUN-AGG-0001")
PER_NODE = 20


def write(path: Path, header: str, rows: list[str]) -> None:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    """Three aggregation nodes with two broadband nodes each, twenty customers on a node, and
    one customer of every kind that must not be drawn."""
    nodes = ["node_id,node_type,service_type,parent_node_id,city,area,pincode,status"]
    customers, services, truth = [], [], []
    number = 0
    for root in ROOTS:
        nodes.append(f"{root},AGG,,,BLR,Area,560001,ACTIVE")
        for leaf in (1, 2):
            node = f"{root.replace('AGG', 'OLT')}-{leaf}"
            nodes.append(f"{node},OLT,broadband,{root},BLR,Area,560001,ACTIVE")
            for _ in range(PER_NODE):
                number += 1
                customers.append(f"C{number:07d},Name,+9198{number:08d},active")
                services.append(f"S{number:07d},C{number:07d},broadband,active")
                truth.append(f"S{number:07d},{node},none")
    node = "BLR-OLT-0001-1"
    customers += [
        "C9000001,Name,+919000000001,suspended",
        "C9000002,Name,+919000000002,active",
        "C9000003,Name,+919000000003,active",
        "C9000004,Name,+919000000004,active",
    ]
    services += [
        "S9000001,C9000001,broadband,active",  # the customer is suspended
        "S9000002,C9000002,mobile,active",  # not broadband
        "S9000003,C9000003,broadband,suspended",  # the service is not active
        "S9000004,C9000004,broadband,active",  # its address maps to the wrong node
    ]
    truth += [
        f"S9000001,{node},none",
        f"S9000002,{node},none",
        f"S9000003,{node},none",
        f"S9000004,{node},stale",
    ]
    write(tmp_path / "nodes.csv", nodes[0], nodes[1:])
    write(tmp_path / "customers.csv", "customer_id,full_name,phone,status", customers)
    write(tmp_path / "services.csv", "service_id,customer_id,service_type,status", services)
    write(tmp_path / "truth_service_node.csv", "service_id,true_node_id,mapping_defect", truth)
    return tmp_path


@pytest.fixture
def pack(pack_dir: Path) -> Pack:
    return load_pack(pack_dir)


def test_only_customers_the_assistant_can_serve_alone_are_in_the_pack(pack: Pack) -> None:
    everyone = {customer for members in pack.by_node.values() for customer, _ in members}

    assert len(everyone) == len(ROOTS) * 2 * PER_NODE
    assert not everyone & {"C9000001", "C9000002", "C9000003", "C9000004"}
    assert {root: len(nodes) for root, nodes in pack.under.items()} == dict.fromkeys(ROOTS, 2)


def test_most_callers_are_on_the_failed_nodes_and_nobody_calls_twice(pack: Pack) -> None:
    drawn = cast(pack, 40, random.Random(3), calling_share=1.0)

    down = {node for root in drawn.roots for node in pack.under[root]}
    affected = [caller for caller in drawn.callers if caller.affected]
    assert len(drawn.callers) == 40
    assert len({caller.customer_id for caller in drawn.callers}) == 40
    assert len(affected) == 28  # 70%
    assert all(caller.node_id in down for caller in affected)
    assert all(caller.node_id not in down for caller in drawn.callers if not caller.affected)
    assert len(drawn.roots) == 1  # forty customers under one root are enough for 28 callers


def test_enough_nodes_fail_for_the_share_of_their_customers_that_calls(pack: Pack) -> None:
    """With half of the affected customers calling, 28 callers need 56 affected customers:
    two aggregation nodes of forty."""
    drawn = cast(pack, 40, random.Random(3), calling_share=0.5)

    assert len(drawn.roots) == 2


def test_callers_come_by_the_web_and_through_a_contact_centre(pack: Pack) -> None:
    drawn = cast(pack, 60, random.Random(5), calling_share=1.0, channel_share=0.3)

    routes = [caller.route for caller in drawn.callers]
    assert set(routes) == {WEB, CHANNEL}
    assert 8 <= routes.count(CHANNEL) <= 28
    assert all(caller.phone.startswith("+9198") for caller in drawn.callers)


def test_the_same_seed_draws_the_same_cast(pack: Pack) -> None:
    assert cast(pack, 40, random.Random(11)) == cast(pack, 40, random.Random(11))
    assert cast(pack, 40, random.Random(11)) != cast(pack, 40, random.Random(12))


def test_a_second_run_fails_other_nodes_and_draws_other_customers(pack: Pack) -> None:
    rng = random.Random(3)
    first = cast(pack, 30, rng, calling_share=1.0)
    second = cast(
        pack,
        30,
        rng,
        calling_share=1.0,
        not_roots=first.roots,
        not_customers={caller.customer_id for caller in first.callers},
    )

    assert not set(first.roots) & set(second.roots)
    assert not {c.customer_id for c in first.callers} & {c.customer_id for c in second.callers}
    earlier = {node for root in first.roots for node in pack.under[root]}
    # Nobody in the second run is on a node of the first run's outage, which may still settle.
    assert all(caller.node_id not in earlier for caller in second.callers)


def test_a_surge_larger_than_the_pack_is_refused(pack: Pack) -> None:
    with pytest.raises(ValueError, match="data pack"):
        cast(pack, 1000, random.Random(1))
