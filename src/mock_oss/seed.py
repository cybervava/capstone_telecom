"""Load the reference CSVs of the data pack (docs/contracts/data.md) into SQLite.

Re-runnable: the whole store is rebuilt inside one transaction, so a failed seed leaves the
previous database untouched.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from mock_oss.clock import iso, parse_iso
from mock_oss.db import create_indexes, create_schema, drop_all, open_connection

log = logging.getLogger("mock_oss.seed")

DEFAULT_LINE_FAULT_RATE = 0.012
Row = tuple[object, ...]


class SeedError(Exception):
    """The data pack is missing a file or has malformed content."""


@dataclass(frozen=True)
class SeedStats:
    nodes: int
    customers: int
    services: int
    line_faults: int
    seconds: float


def is_line_faulted(service_id: str, *, seed: int, rate: float) -> bool:
    """Deterministic selection independent of Python's randomised ``hash()``."""
    digest = hashlib.sha256(f"{seed}:line-fault:{service_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


def _rows(path: Path, columns: Sequence[str]) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise SeedError(f"{path.name}: missing columns {sorted(missing)}")
        try:
            yield from reader
        except csv.Error as exc:
            raise SeedError(f"{path.name}:{reader.line_num}: {exc}") from exc


def _converted(
    path: Path, columns: Sequence[str], convert: Callable[[dict[str, str]], Row]
) -> Iterator[Row]:
    for line_no, row in enumerate(_rows(path, columns), start=2):
        try:
            yield convert(row)
        except (ValueError, InvalidOperation, KeyError, TypeError) as exc:
            raise SeedError(f"{path.name}:{line_no}: {exc}") from exc


def _node(r: dict[str, str]) -> Row:
    return (
        r["node_id"],
        r["node_type"],
        r["service_type"] or None,
        r["parent_node_id"] or None,
        r["city"],
        r["area"],
        r["pincode"],
        r["status"],
    )


def _address(r: dict[str, str]) -> Row:
    return (r["address_id"], r["line1"], r["area"], r["city"], r["pincode"])


def _customer(r: dict[str, str]) -> Row:
    consent = r["consent_outage_notifications"] == "true"
    return (
        r["customer_id"],
        r["full_name"],
        r["phone"],
        r["email"],
        r["email"].lower(),
        r["segment"],
        r["status"],
        r["created_on"],
        int(consent),
        r["consent_channel"] if consent else "none",
        iso(parse_iso(r["consent_updated_at"])),
    )


def _money(text: str) -> tuple[str, int]:
    amount = Decimal(text).quantize(Decimal("0.01"))
    return f"{amount:.2f}", int(amount * 100)


def _truth(r: dict[str, str]) -> Row:
    return (r["service_id"], r["true_node_id"], r["mapping_defect"])


def _mapping(r: dict[str, str]) -> Row:
    return (r["address_id"], r["service_type"], r["node_id"])


NODE_COLS = [
    "node_id",
    "node_type",
    "service_type",
    "parent_node_id",
    "city",
    "area",
    "pincode",
    "status",
]
ADDRESS_COLS = ["address_id", "line1", "area", "city", "pincode"]
CUSTOMER_COLS = [
    "customer_id",
    "full_name",
    "phone",
    "email",
    "segment",
    "status",
    "created_on",
    "consent_outage_notifications",
    "consent_channel",
    "consent_updated_at",
]
SERVICE_COLS = [
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
]
TRUTH_COLS = ["service_id", "true_node_id", "mapping_defect"]
MAP_COLS = ["address_id", "service_type", "node_id", "valid_to"]
REQUIRED_FILES = [
    "nodes.csv",
    "addresses.csv",
    "customers.csv",
    "services.csv",
    "truth_service_node.csv",
]


class _ServiceLoader:
    """Converts service rows and collects seeded line faults on the way through."""

    def __init__(self, seed: int, rate: float) -> None:
        self.seed = seed
        self.rate = rate
        self.faults: list[Row] = []

    def __call__(self, r: dict[str, str]) -> Row:
        fee, paise = _money(r["monthly_fee_inr"])
        if r["service_type"] == "broadband" and is_line_faulted(
            r["service_id"], seed=self.seed, rate=self.rate
        ):
            self.faults.append((r["service_id"], "FAIL"))
        return (
            r["service_id"],
            r["customer_id"],
            r["service_type"],
            r["plan_name"],
            fee,
            paise,
            r["status"],
            r["address_id"],
            r["router_model"] or None,
            r["router_serial"] or None,
            r["activated_on"],
            int(r["billing_cycle_day"]),
        )


def _insert(conn: sqlite3.Connection, table: str, width: int, rows: Iterator[Row]) -> int:
    marks = ", ".join("?" * width)
    cursor = conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)  # noqa: S608
    return cursor.rowcount


def _current_mappings(path: Path) -> Iterator[Row]:
    for row in _rows(path, MAP_COLS):
        if row["valid_to"] == "":
            yield _mapping(row)


def _load_all(conn: sqlite3.Connection, data: Path, seed: int, rate: float) -> SeedStats:
    started = time.monotonic()
    nodes = _insert(conn, "nodes", 8, _converted(data / "nodes.csv", NODE_COLS, _node))
    _insert(conn, "addresses", 5, _converted(data / "addresses.csv", ADDRESS_COLS, _address))
    customers = _insert(
        conn, "customers", 11, _converted(data / "customers.csv", CUSTOMER_COLS, _customer)
    )
    loader = _ServiceLoader(seed, rate)
    services = _insert(
        conn, "services", 12, _converted(data / "services.csv", SERVICE_COLS, loader)
    )
    _insert(conn, "truth", 3, _converted(data / "truth_service_node.csv", TRUTH_COLS, _truth))
    map_file = data / "address_node_map.csv"
    if map_file.exists():
        _insert(conn, "address_node_map", 3, _current_mappings(map_file))
    else:
        log.warning("address_node_map.csv not found; mapped_node_ids will be empty")
    _insert(conn, "seed_line_faults", 2, iter(loader.faults))
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [("customers", str(customers)), ("seed", str(seed)), ("seeded", "true")],
    )
    return SeedStats(nodes, customers, services, len(loader.faults), time.monotonic() - started)


def seed_database(
    data_dir: Path,
    db_path: Path,
    *,
    seed: int = 42,
    line_fault_rate: float = DEFAULT_LINE_FAULT_RATE,
) -> SeedStats:
    for name in REQUIRED_FILES:
        if not (data_dir / name).is_file():
            raise SeedError(f"{data_dir / name}: required file not found")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_connection(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            drop_all(conn)
            create_schema(conn, with_indexes=False)
            stats = _load_all(conn, data_dir, seed, line_fault_rate)
            create_indexes(conn)
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise SeedError(f"constraint violated in data pack: {exc}") from exc
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    log.info(
        "seeded %s customers, %s services, %s line faults in %.1fs",
        stats.customers,
        stats.services,
        stats.line_faults,
        stats.seconds,
    )
    return stats
