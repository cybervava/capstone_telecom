"""Fixtures for mock_oss: a tiny data pack written to tmp_path and an in-process app."""

from __future__ import annotations

import csv
import itertools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mock_oss.clock import ManualClock
from mock_oss.config import CLIENT_ENV_VARS, Settings
from mock_oss.main import create_app
from mock_oss.seed import seed_database

START = datetime(2026, 11, 15, 4, 0, 0, tzinfo=UTC)

NODES = [
    # node_id, node_type, service_type, parent, city, area, pincode, status
    ("BLR-AGG-0001", "AGG", "", "", "BLR", "Indiranagar", "560038", "ACTIVE"),
    ("BLR-OLT-0001", "OLT", "broadband", "BLR-AGG-0001", "BLR", "Indiranagar", "560038", "ACTIVE"),
    ("BLR-OLT-0002", "OLT", "broadband", "BLR-AGG-0001", "BLR", "Ulsoor", "560008", "ACTIVE"),
    (
        "BLR-OLT-0003",
        "OLT",
        "broadband",
        "BLR-AGG-0001",
        "BLR",
        "Ulsoor",
        "560008",
        "DECOMMISSIONED",
    ),
    ("BLR-TWR-0001", "TOWER", "mobile", "BLR-AGG-0001", "BLR", "Indiranagar", "560038", "ACTIVE"),
    ("MUM-AGG-0001", "AGG", "", "", "MUM", "Bandra", "400050", "ACTIVE"),
    ("MUM-OLT-0001", "OLT", "broadband", "MUM-AGG-0001", "MUM", "Bandra", "400050", "ACTIVE"),
    ("MUM-TWR-0001", "TOWER", "mobile", "MUM-AGG-0001", "MUM", "Bandra", "400050", "ACTIVE"),
]

FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Divya", "Kiran", "Neha", "Vikram", "Lata", "Om"]
LAST = ["Menon", "Iyer", "Rao", "Shah", "Nair", "Das", "Gupta", "Patel", "Joshi", "Bose"]


def _customers() -> list[dict[str, str]]:
    rows = []
    for i in range(1, 13):
        first, last = FIRST[(i - 1) % 10], LAST[(i * 3) % 10]
        consent = i != 3
        rows.append(
            {
                "customer_id": f"C{i:07d}",
                "full_name": f"{first} {last}",
                "phone": f"+9198450{i:05d}",
                "email": f"{first.lower()}.{last.lower()}{i}@example.com",
                "segment": "sme" if i == 6 else "consumer",
                "status": "suspended" if i == 12 else "active",
                "created_on": "2024-01-01",
                "consent_outage_notifications": "true" if consent else "false",
                "consent_channel": ("push" if i == 2 else "sms") if consent else "none",
                "consent_updated_at": "2026-08-02T15:30:00+05:30",
            }
        )
    return rows


# service_id, customer, type, plan, fee, address, router, true node, defect, mapped nodes
SERVICES = [
    ("S0000001", 1, "broadband", "Fibre 300", "999.00", 1, "AL-AX18", "BLR-OLT-0001", "none"),
    ("S0000002", 2, "broadband", "Fibre 100", "599.00", 2, "AL-AX18", "BLR-OLT-0001", "missing"),
    ("S0000003", 3, "broadband", "Fibre 500", "1499.00", 3, "NX-R2", "BLR-OLT-0002", "ambiguous"),
    ("S0000004", 4, "broadband", "Fibre 1G", "3999.00", 4, "NX-R2", "BLR-OLT-0002", "stale"),
    ("S0000005", 5, "broadband", "Fibre 300", "999.00", 5, "AL-AX18", "MUM-OLT-0001", "none"),
    ("S0000006", 6, "broadband", "Biz Fibre 1G", "9999.00", 6, "NX-R2", "BLR-OLT-0001", "none"),
    ("S0000007", 5, "mobile", "Postpaid 499", "499.00", 5, "", "MUM-TWR-0001", "none"),
    ("S0000008", 7, "mobile", "Prepaid 28", "299.00", 7, "", "BLR-TWR-0001", "none"),
    ("S0000009", 8, "mobile", "Postpaid 999", "999.00", 8, "", "BLR-TWR-0001", "none"),
    ("S0000010", 9, "mobile", "Prepaid 28", "299.00", 9, "", "BLR-TWR-0001", "none"),
    ("S0000011", 10, "mobile", "Postpaid 499", "499.00", 10, "", "MUM-TWR-0001", "none"),
    ("S0000012", 11, "mobile", "Postpaid 499", "499.00", 11, "", "BLR-TWR-0001", "none"),
    ("S0000013", 12, "mobile", "Prepaid 28", "299.00", 12, "", "BLR-TWR-0001", "none"),
    ("S0000014", 1, "mobile", "Postpaid 999", "999.00", 1, "", "BLR-TWR-0001", "none"),
]

MAPPED = {
    "S0000002": [],
    "S0000003": ["BLR-OLT-0002", "BLR-OLT-0001"],
    "S0000004": ["BLR-OLT-0003"],
}


def _write(path: Path, header: list[str], rows: list[list[str]] | list[tuple[str, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _address_rows() -> list[tuple[str, ...]]:
    rows = []
    node_by_id = {n[0]: n for n in NODES}
    for svc in SERVICES:
        node = node_by_id[svc[7]]
        address_id = f"A{svc[5]:07d}"
        rows.append((address_id, f"{svc[5]} Main Road", node[5], node[4], node[6]))
    unique = {r[0]: r for r in rows}
    return [unique[k] for k in sorted(unique)]


def write_data_pack(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    _write(
        directory / "nodes.csv",
        [
            "node_id",
            "node_type",
            "service_type",
            "parent_node_id",
            "city",
            "area",
            "pincode",
            "status",
        ],
        NODES,
    )
    _write(
        directory / "addresses.csv",
        ["address_id", "line1", "area", "city", "pincode"],
        _address_rows(),
    )
    customers = _customers()
    _write(directory / "customers.csv", list(customers[0]), [list(c.values()) for c in customers])
    svc_rows = [
        (
            s[0],
            f"C{s[1]:07d}",
            s[2],
            s[3],
            s[4],
            "active",
            f"A{s[5]:07d}",
            s[6],
            f"SN{s[0]}" if s[6] else "",
            "2024-03-11",
            "5",
        )
        for s in SERVICES
    ]
    _write(
        directory / "services.csv",
        [
            "service_id",
            "customer_id",
            "service_type",
            "plan_name",
            "monthly_fee_inr",
            "status",
            "address_id",
            "router_model",
            "router_serial",
            "activated_on",
            "billing_cycle_day",
        ],
        svc_rows,
    )
    _write(
        directory / "truth_service_node.csv",
        ["service_id", "true_node_id", "mapping_defect"],
        [(s[0], s[7], s[8]) for s in SERVICES],
    )
    map_rows = []
    for s in SERVICES:
        for node_id in MAPPED.get(s[0], [s[7]]):
            map_rows.append(
                (f"A{s[5]:07d}", s[2], node_id, "inventory", "2024-01-01", "", "2026-11-01")
            )
    _write(
        directory / "address_node_map.csv",
        [
            "address_id",
            "service_type",
            "node_id",
            "source",
            "valid_from",
            "valid_to",
            "map_version",
        ],
        map_rows,
    )
    return directory


KEYS = {client: f"test-key-{client}" for client in CLIENT_ENV_VARS}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return write_data_pack(tmp_path / "pack")


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(START)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "oss.sqlite",
        host="127.0.0.1",
        port=8081,
        line_test_delay_ms=(0, 0),
        api_keys=dict(KEYS),
    )


@pytest.fixture
def app(settings: Settings, data_dir: Path, clock: ManualClock) -> FastAPI:
    seed_database(data_dir, settings.db_path, seed=42, line_fault_rate=0.0)
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def auth(client_name: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[client_name]}


_counter = itertools.count(1)


def idem(client_name: str = "executor", key: str | None = None) -> dict[str, str]:
    return {**auth(client_name), "Idempotency-Key": key or f"k-{next(_counter)}"}


@pytest.fixture
def admin(client: TestClient) -> Callable[..., dict[str, object]]:
    """POST to an admin endpoint and return the JSON body, asserting success."""

    def _call(path: str, body: dict[str, object]) -> dict[str, object]:
        resp = client.post(f"/admin/v1/{path}", json=body, headers=auth("admin"))
        assert resp.status_code in (200, 201), resp.text
        result: dict[str, object] = resp.json()
        return result

    return _call
