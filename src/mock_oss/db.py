"""SQLite schema and connection handling (WAL, busy timeout, short transactions)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REFERENCE_TABLES: dict[str, str] = {
    "nodes": """node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL, service_type TEXT,
        parent_node_id TEXT, city TEXT NOT NULL, area TEXT NOT NULL, pincode TEXT NOT NULL,
        status TEXT NOT NULL""",
    "addresses": """address_id TEXT PRIMARY KEY, line1 TEXT NOT NULL, area TEXT NOT NULL,
        city TEXT NOT NULL, pincode TEXT NOT NULL""",
    "customers": """customer_id TEXT PRIMARY KEY, full_name TEXT NOT NULL, phone TEXT NOT NULL,
        email TEXT NOT NULL, email_lower TEXT NOT NULL, segment TEXT NOT NULL,
        status TEXT NOT NULL, created_on TEXT NOT NULL, consent INTEGER NOT NULL,
        consent_channel TEXT NOT NULL, consent_updated_at TEXT NOT NULL""",
    "services": """service_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
        service_type TEXT NOT NULL, plan_name TEXT NOT NULL, monthly_fee TEXT NOT NULL,
        monthly_fee_paise INTEGER NOT NULL, status TEXT NOT NULL, address_id TEXT NOT NULL,
        router_model TEXT, router_serial TEXT, activated_on TEXT NOT NULL,
        billing_cycle_day INTEGER NOT NULL""",
    "truth": """service_id TEXT PRIMARY KEY, true_node_id TEXT NOT NULL,
        mapping_defect TEXT NOT NULL""",
    "address_node_map": """address_id TEXT NOT NULL, service_type TEXT NOT NULL,
        node_id TEXT NOT NULL""",
    "seed_line_faults": "service_id TEXT PRIMARY KEY, result TEXT NOT NULL",
    "meta": "key TEXT PRIMARY KEY, value TEXT NOT NULL",
}

RUNTIME_TABLES: dict[str, str] = {
    "counters": "name TEXT PRIMARY KEY, value INTEGER NOT NULL",
    "events": "seq INTEGER PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL",
    "outages": """outage_id TEXT PRIMARY KEY, root_node_id TEXT NOT NULL, cause TEXT NOT NULL,
        planned INTEGER NOT NULL, service_types TEXT NOT NULL, affected TEXT NOT NULL,
        started_at TEXT NOT NULL, detected_at TEXT NOT NULL, detected INTEGER NOT NULL,
        eta TEXT NOT NULL, eta_source TEXT NOT NULL, version INTEGER NOT NULL,
        resolved_at TEXT, alarm_id TEXT, request_id TEXT""",
    "outage_nodes": """outage_id TEXT NOT NULL, node_id TEXT NOT NULL,
        PRIMARY KEY (outage_id, node_id)""",
    "alarms": """alarm_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, alarm_type TEXT NOT NULL,
        severity TEXT NOT NULL, raised_at TEXT NOT NULL, cleared_at TEXT, outage_id TEXT""",
    "line_fault_overrides": "service_id TEXT PRIMARY KEY, fault INTEGER NOT NULL, result TEXT",
    "consent_overrides": """customer_id TEXT PRIMARY KEY, outage_notifications INTEGER NOT NULL,
        channel TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "credits": """credit_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, service_id TEXT NOT NULL,
        amount TEXT NOT NULL, reason_code TEXT NOT NULL, outage_id TEXT,
        policy_version TEXT NOT NULL, complaint_ref TEXT NOT NULL, approved_by TEXT,
        status TEXT NOT NULL, created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        request_id TEXT""",
    "appointments": """appointment_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
        service_id TEXT NOT NULL, slot_id TEXT NOT NULL, start_at TEXT NOT NULL,
        end_at TEXT NOT NULL, reason TEXT NOT NULL, complaint_ref TEXT, status TEXT NOT NULL,
        created_at TEXT NOT NULL, cancelled_at TEXT, idempotency_key TEXT NOT NULL,
        request_id TEXT""",
    "incidents": """incident_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
        service_type TEXT NOT NULL, kind TEXT NOT NULL, outage_id TEXT, summary TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, request_id TEXT""",
    "incident_complaints": """incident_id TEXT NOT NULL, complaint_ref TEXT NOT NULL,
        contact_id TEXT NOT NULL, customer_id TEXT, linked_at TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, request_id TEXT, PRIMARY KEY (incident_id, complaint_ref)""",
    "complaints": """complaint_ref TEXT PRIMARY KEY, received_at TEXT NOT NULL,
        channel TEXT NOT NULL, category TEXT NOT NULL, customer_id TEXT,
        registered_at TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_id TEXT""",
    "messages": """message_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, channel TEXT NOT NULL,
        template_id TEXT NOT NULL, body TEXT NOT NULL, outage_id TEXT, status TEXT NOT NULL,
        created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_id TEXT""",
    "idempotency": """endpoint TEXT NOT NULL, key TEXT NOT NULL, body_hash TEXT NOT NULL,
        status INTEGER NOT NULL, response TEXT NOT NULL, resource_id TEXT NOT NULL,
        created_at TEXT NOT NULL, request_id TEXT, PRIMARY KEY (endpoint, key)""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_nodes_parent ON nodes (parent_node_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_phone ON customers (phone)",
    "CREATE INDEX IF NOT EXISTS ix_customers_email ON customers (email_lower)",
    "CREATE INDEX IF NOT EXISTS ix_services_customer ON services (customer_id)",
    "CREATE INDEX IF NOT EXISTS ix_truth_node ON truth (true_node_id)",
    "CREATE INDEX IF NOT EXISTS ix_map_address ON address_node_map (address_id, service_type)",
    "CREATE INDEX IF NOT EXISTS ix_outage_nodes_node ON outage_nodes (node_id)",
    "CREATE INDEX IF NOT EXISTS ix_alarms_node ON alarms (node_id, cleared_at)",
    "CREATE INDEX IF NOT EXISTS ix_credits_customer ON credits (customer_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_appointments_slot ON appointments (slot_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_appointments_service ON appointments (service_id)",
    "CREATE INDEX IF NOT EXISTS ix_incidents_node ON incidents (node_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_messages_customer ON messages (customer_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_outage ON messages (outage_id)",
]

# Runtime tables wiped by /admin/v1/reset. The feed is history, not state: ``events`` and the
# ``counters`` behind its sequence survive, so a consumer's cursor stays valid across a reset.
RESETTABLE = [name for name in RUNTIME_TABLES if name not in {"counters", "events"}]
WRITE_TABLES = [
    "credits",
    "appointments",
    "incidents",
    "incident_complaints",
    "complaints",
    "messages",
]


def _ddl(name: str, columns: str) -> str:
    # Table names and columns are module constants, never request data.
    return f"CREATE TABLE IF NOT EXISTS {name} ({columns})"


def create_schema(conn: sqlite3.Connection, *, with_indexes: bool = True) -> None:
    for name, columns in {**REFERENCE_TABLES, **RUNTIME_TABLES}.items():
        conn.execute(_ddl(name, columns))
    if with_indexes:
        create_indexes(conn)


def create_indexes(conn: sqlite3.Connection) -> None:
    for statement in INDEXES:
        conn.execute(statement)


def drop_all(conn: sqlite3.Connection) -> None:
    for name in [*REFERENCE_TABLES, *RUNTIME_TABLES]:
        conn.execute(f"DROP TABLE IF EXISTS {name}")


def clear_runtime(conn: sqlite3.Connection) -> None:
    for name in RESETTABLE:
        conn.execute(f"DELETE FROM {name}")  # noqa: S608 - constant table names


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    if table not in RUNTIME_TABLES and table not in REFERENCE_TABLES:
        raise ValueError(f"unknown table {table!r}")
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608 - whitelisted
    return int(row[0])


def open_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class Database:
    """Opens a short-lived connection per unit of work; SQLite connections are cheap."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialise(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = open_connection(self.path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            create_schema(conn)
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = open_connection(self.path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` transaction: commit on success, roll back on any error."""
        conn = open_connection(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()


def next_counter(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (?, 1) "
        "ON CONFLICT(name) DO UPDATE SET value = value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return int(row[0])


def read_counter(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
    return int(row[0]) if row is not None else 0
