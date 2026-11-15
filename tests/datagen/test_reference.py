"""Reference data: columns, enums, uniqueness, foreign keys and topology."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pytest

from skylink_datagen.config import derive_rng
from skylink_datagen.nodes import build_topology
from tests.datagen.helpers import Dataset, ts

CITIES = {"BLR", "MUM", "DEL", "HYD", "CHN", "PUN", "KOL", "AMD"}
COLUMNS = {
    "nodes.csv": [
        "node_id", "node_type", "service_type", "parent_node_id", "city", "area",
        "pincode", "status",
    ],
    "addresses.csv": ["address_id", "line1", "area", "city", "pincode"],
    "customers.csv": [
        "customer_id", "full_name", "phone", "email", "segment", "status", "created_on",
        "consent_outage_notifications", "consent_channel", "consent_updated_at",
    ],
    "services.csv": [
        "service_id", "customer_id", "service_type", "plan_name", "monthly_fee_inr", "status",
        "address_id", "router_model", "router_serial", "activated_on", "billing_cycle_day",
    ],
    "address_node_map.csv": [
        "address_id", "service_type", "node_id", "source", "valid_from", "valid_to", "map_version",
    ],
    "truth_service_node.csv": ["service_id", "true_node_id", "mapping_defect"],
    "outages.csv": [
        "outage_id", "root_node_id", "affected_node_ids", "service_types", "cause", "planned",
        "started_at", "detected_at", "initial_eta", "resolved_at", "duration_minutes",
    ],
}  # fmt: skip
CONSUMER_BB = {"Fibre 100": "599.00", "Fibre 300": "999.00", "Fibre 500": "1499.00",
               "Fibre 1G": "3999.00"}  # fmt: skip
SME_BB = {"Biz Fibre 500": "4999.00", "Biz Fibre 1G": "9999.00", "Biz Leased 1G": "14999.00"}
MOBILE = {"Prepaid 28": "299.00", "Postpaid 499": "499.00", "Postpaid 999": "999.00"}
NODE_TYPE_CODE = {"AGG": "AGG", "OLT": "OLT", "TOWER": "TWR"}


@pytest.mark.parametrize("name", sorted(COLUMNS))
def test_exact_columns(data: Dataset, name: str) -> None:
    assert data.header(name) == COLUMNS[name]


def test_node_ids_types_and_parents(data: Dataset) -> None:
    for node in data.nodes.values():
        city, code, number = node["node_id"].split("-")
        assert city == node["city"]
        assert city in CITIES
        assert code == NODE_TYPE_CODE[node["node_type"]]
        assert re.fullmatch(r"\d{4}", number)
        assert node["status"] in {"ACTIVE", "DECOMMISSIONED"}
        assert re.fullmatch(r"\d{6}", node["pincode"])
        assert node["area"]
        if node["node_type"] == "AGG":
            assert node["service_type"] == ""
            assert node["parent_node_id"] == ""
            assert node["status"] == "ACTIVE"
            continue
        expected = "broadband" if node["node_type"] == "OLT" else "mobile"
        assert node["service_type"] == expected
        parent = data.nodes[node["parent_node_id"]]
        assert parent["node_type"] == "AGG"
        assert parent["city"] == node["city"]


def test_every_city_has_two_aggs_and_each_agg_has_active_children(data: Dataset) -> None:
    aggs = Counter(n["city"] for n in data.nodes.values() if n["node_type"] == "AGG")
    assert set(aggs) == CITIES
    assert min(aggs.values()) >= 2
    children: dict[str, set[str]] = defaultdict(set)
    for node in data.nodes.values():
        if node["parent_node_id"] and node["status"] == "ACTIVE":
            children[node["parent_node_id"]].add(node["node_type"])
    for node_id, node in data.nodes.items():
        if node["node_type"] == "AGG":
            assert children[node_id] == {"OLT", "TOWER"}, node_id


def test_some_access_nodes_are_decommissioned(data: Dataset) -> None:
    access = [n for n in data.nodes.values() if n["node_type"] != "AGG"]
    decom = [n for n in access if n["status"] == "DECOMMISSIONED"]
    assert {n["node_type"] for n in decom} == {"OLT", "TOWER"}
    # At small scale the floor of one per type dominates; ~1% is asserted at full scale below.
    assert len(decom) / len(access) <= 0.10


def test_full_scale_node_sizing_follows_contract() -> None:
    topology = build_topology(400_000, derive_rng(42, "nodes"))
    counts = Counter((n.node_type, n.status) for n in topology.nodes)
    olt, tower = counts[("OLT", "ACTIVE")], counts[("TOWER", "ACTIVE")]
    access = sum(v for (kind, _), v in counts.items() if kind != "AGG")
    assert 180_000 / olt == pytest.approx(250, rel=0.06)
    assert 328_000 / tower == pytest.approx(1500, rel=0.06)
    assert access / counts[("AGG", "ACTIVE")] == pytest.approx(12, rel=0.12)
    decom = counts[("OLT", "DECOMMISSIONED")] + counts[("TOWER", "DECOMMISSIONED")]
    assert decom / access == pytest.approx(0.01, abs=0.004)


def test_customer_fields(data: Dataset) -> None:
    rows = list(data.customers.values())
    for i, row in enumerate(rows, start=1):
        assert row["customer_id"] == f"C{i:07d}"
        assert re.fullmatch(r"\+91[6-9]\d{9}", row["phone"])
        assert re.fullmatch(r"[a-z]+\.[a-z]+\d+@example\.com", row["email"])
        first, last = row["full_name"].lower().split(" ", 1)
        assert row["email"].startswith(f"{first}.{last.replace(' ', '')}")
        date.fromisoformat(row["created_on"])
        assert ts(row["consent_updated_at"]).utcoffset().total_seconds() == 19800  # type: ignore[union-attr]
        assert row["segment"] in {"consumer", "sme"}
        assert row["status"] in {"active", "suspended"}
        consent = row["consent_outage_notifications"]
        assert consent in {"true", "false"}
        assert (row["consent_channel"] == "none") == (consent == "false")
        assert row["consent_channel"] in {"sms", "push", "none"}
    assert len({r["phone"] for r in rows}) == len(rows)
    assert len({r["email"] for r in rows}) == len(rows)


def test_customer_mix(data: Dataset) -> None:
    rows = list(data.customers.values())
    n = len(rows)
    assert sum(r["segment"] == "sme" for r in rows) / n == pytest.approx(0.08, abs=0.015)
    assert sum(r["status"] == "suspended" for r in rows) / n == pytest.approx(0.03, abs=0.01)
    consent = sum(r["consent_outage_notifications"] == "true" for r in rows) / n
    assert consent == pytest.approx(0.78, abs=0.02)


def test_services_fields_and_plans(data: Dataset, knowledge_dir: Path) -> None:
    models = {m["model_id"] for m in json.loads((knowledge_dir / "router_models.json").read_text())}
    for i, svc in enumerate(data.services.values(), start=1):
        assert svc["service_id"] == f"S{i:07d}"
        customer = data.customers[svc["customer_id"]]
        assert svc["address_id"] in data.addresses
        assert svc["status"] in {"active", "suspended"}
        assert 1 <= int(svc["billing_cycle_day"]) <= 28
        assert date.fromisoformat(svc["activated_on"]) >= date.fromisoformat(customer["created_on"])
        if svc["service_type"] == "mobile":
            assert MOBILE[svc["plan_name"]] == svc["monthly_fee_inr"]
            assert svc["router_model"] == ""
            assert svc["router_serial"] == ""
            continue
        assert svc["service_type"] == "broadband"
        plans = SME_BB if customer["segment"] == "sme" else CONSUMER_BB
        assert plans[svc["plan_name"]] == svc["monthly_fee_inr"]
        assert svc["router_model"] in models
        assert svc["router_serial"]


def test_service_mix_per_customer(data: Dataset) -> None:
    kinds: dict[str, list[str]] = defaultdict(list)
    for svc in data.services.values():
        kinds[svc["customer_id"]].append(svc["service_type"])
    assert set(kinds) == set(data.customers)
    assert all(sorted(set(v)) == sorted(v) for v in kinds.values())
    n = len(data.customers)
    assert sum("broadband" in v for v in kinds.values()) / n == pytest.approx(0.45, abs=0.02)
    assert sum("mobile" in v for v in kinds.values()) / n == pytest.approx(0.82, abs=0.02)
    assert sum(len(v) == 2 for v in kinds.values()) / n == pytest.approx(0.27, abs=0.02)


def test_serials_unique(data: Dataset) -> None:
    serials = [s["router_serial"] for s in data.services.values() if s["router_serial"]]
    assert len(serials) == len(set(serials))


def test_truth_covers_every_service_with_active_node_of_right_type(data: Dataset) -> None:
    assert list(data.truth) == list(data.services)
    for service_id, truth in data.truth.items():
        node = data.nodes[truth["true_node_id"]]
        assert node["status"] == "ACTIVE"
        assert node["service_type"] == data.services[service_id]["service_type"]
        assert truth["mapping_defect"] in {"none", "missing", "ambiguous", "stale"}


def test_address_area_and_pincode_agree_with_true_node(data: Dataset) -> None:
    for i, (address_id, address) in enumerate(data.addresses.items(), start=1):
        assert address_id == f"A{i:07d}"
        assert address["line1"]
    for service_id, truth in data.truth.items():
        node = data.nodes[truth["true_node_id"]]
        address = data.addresses[data.services[service_id]["address_id"]]
        key = (address["city"], address["area"], address["pincode"])
        assert key == (node["city"], node["area"], node["pincode"])
