"""NOC simulation: outages, alarms and the ordered event feed.

Detection is lazy: an outage whose ``detected_at`` has passed is "detected" (and its
``OUTAGE_STARTED`` event emitted) the next time anyone reads the network, so tests drive it by
advancing the clock rather than by sleeping.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mock_oss.clock import iso
from mock_oss.db import Database, next_counter
from mock_oss.errors import ApiError
from mock_oss.schemas import OutageIn

EVENT_SEQ = "event_seq"


def emit(conn: sqlite3.Connection, event_type: str, emitted_at: str, fields: dict[str, Any]) -> int:
    seq = next_counter(conn, EVENT_SEQ)
    event = {
        "seq": seq,
        "event_id": f"evt_{uuid.uuid4().hex[:16]}",
        "type": event_type,
        "emitted_at": emitted_at,
        **fields,
    }
    conn.execute(
        "INSERT INTO events (seq, type, payload) VALUES (?, ?, ?)",
        (seq, event_type, json.dumps(event)),
    )
    return seq


def require_active_node(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NODE_NOT_FOUND", f"node {node_id} not found")
    if row["status"] == "DECOMMISSIONED":
        raise ApiError(410, "NODE_DECOMMISSIONED", f"node {node_id} is decommissioned")
    return row


# ---------------------------------------------------------------- alarms


def alarm_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "alarm_id": row["alarm_id"],
        "node_id": row["node_id"],
        "alarm_type": row["alarm_type"],
        "severity": row["severity"],
        "raised_at": row["raised_at"],
    }


def _alarm_event_fields(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "node_ids": [row["node_id"]],
        "alarm_id": row["alarm_id"],
        "alarm_type": row["alarm_type"],
        "severity": row["severity"],
    }


def raise_alarm(
    conn: sqlite3.Connection,
    node_id: str,
    alarm_type: str,
    severity: str,
    now: str,
    outage_id: str | None = None,
) -> str:
    alarm_id = f"ALM-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO alarms (alarm_id, node_id, alarm_type, severity, raised_at, outage_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (alarm_id, node_id, alarm_type, severity, now, outage_id),
    )
    row = conn.execute("SELECT * FROM alarms WHERE alarm_id = ?", (alarm_id,)).fetchone()
    emit(conn, "ALARM_RAISED", now, _alarm_event_fields(row))
    return alarm_id


def clear_alarm(conn: sqlite3.Connection, alarm_id: str, now: str) -> dict[str, Any]:
    """Idempotent: clearing an already cleared alarm changes nothing and emits nothing."""
    row = conn.execute("SELECT * FROM alarms WHERE alarm_id = ?", (alarm_id,)).fetchone()
    if row is None:
        raise ApiError(404, "ALARM_NOT_FOUND", f"alarm {alarm_id} not found")
    if row["cleared_at"] is None:
        conn.execute("UPDATE alarms SET cleared_at = ? WHERE alarm_id = ?", (now, alarm_id))
        emit(conn, "ALARM_CLEARED", now, _alarm_event_fields(row))
        row = conn.execute("SELECT * FROM alarms WHERE alarm_id = ?", (alarm_id,)).fetchone()
    return {**alarm_view(row), "cleared_at": row["cleared_at"]}


def active_alarms(conn: sqlite3.Connection, node: sqlite3.Row) -> list[dict[str, Any]]:
    """Alarms raised on the node or on its parent AGG and not yet cleared."""
    rows = conn.execute(
        "SELECT * FROM alarms WHERE cleared_at IS NULL AND (node_id = ? OR node_id = ?) "
        "ORDER BY raised_at, alarm_id",
        (node["node_id"], node["parent_node_id"] or node["node_id"]),
    ).fetchall()
    return [alarm_view(r) for r in rows]


# ---------------------------------------------------------------- outages


def outage_view(row: sqlite3.Row) -> dict[str, Any]:
    """The outage object of the status endpoint."""
    return {
        "outage_id": row["outage_id"],
        "version": row["version"],
        "cause": row["cause"],
        "planned": bool(row["planned"]),
        "service_types": json.loads(row["service_types"]),
        "started_at": row["started_at"],
        "eta": row["eta"],
        "eta_source": row["eta_source"],
        "resolved_at": row["resolved_at"],
    }


def admin_outage_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        **outage_view(row),
        "root_node_id": row["root_node_id"],
        "affected_node_ids": json.loads(row["affected"]),
        "detected_at": row["detected_at"],
        "detected": bool(row["detected"]),
        "alarm_id": row["alarm_id"],
    }


def _emit_outage(conn: sqlite3.Connection, event_type: str, outage_id: str, at: str) -> None:
    row = _outage(conn, outage_id)
    fields = {"node_ids": json.loads(row["affected"]), **outage_view(row)}
    emit(conn, event_type, at, fields)


def _outage(conn: sqlite3.Connection, outage_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM outages WHERE outage_id = ?", (outage_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "OUTAGE_NOT_FOUND", f"outage {outage_id} not found")
    return row


def _affected(conn: sqlite3.Connection, node: sqlite3.Row) -> list[sqlite3.Row]:
    if node["node_type"] != "AGG":
        return [node]
    return list(
        conn.execute(
            "SELECT * FROM nodes WHERE parent_node_id = ? AND status = 'ACTIVE' ORDER BY node_id",
            (node["node_id"],),
        ).fetchall()
    )


@dataclass(frozen=True)
class StartedOutage:
    outage_id: str
    alarm_id: str | None
    affected_node_ids: list[str]


def start_outage(
    conn: sqlite3.Connection, body: OutageIn, now: datetime, request_id: str
) -> StartedOutage:
    root = require_active_node(conn, body.node_id)
    affected = _affected(conn, root)
    affected_ids = [n["node_id"] for n in affected]
    service_types = sorted({n["service_type"] for n in affected if n["service_type"]})
    day = now.strftime("%Y%m%d")
    outage_id = f"OUT-{day}-{next_counter(conn, f'outage:{day}'):04d}"
    started = iso(now)
    detected_at = iso(now + timedelta(seconds=body.detect_after_seconds))
    eta = iso(now + timedelta(minutes=body.eta_minutes))
    conn.execute(
        "INSERT INTO outages (outage_id, root_node_id, cause, planned, service_types, affected, "
        "started_at, detected_at, detected, eta, eta_source, version, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'noc_estimate', 1, ?)",
        (
            outage_id,
            root["node_id"],
            body.cause,
            int(body.planned),
            json.dumps(service_types),
            json.dumps(affected_ids),
            started,
            detected_at,
            eta,
            request_id,
        ),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO outage_nodes (outage_id, node_id) VALUES (?, ?)",
        [(outage_id, n) for n in {root["node_id"], *affected_ids}],
    )
    alarm_id = None
    if not body.planned:
        alarm_type = "POWER" if body.cause == "POWER_FAILURE" else "LINK_DOWN"
        alarm_id = raise_alarm(conn, root["node_id"], alarm_type, "critical", started, outage_id)
        conn.execute("UPDATE outages SET alarm_id = ? WHERE outage_id = ?", (alarm_id, outage_id))
    detect_due(conn, started)
    return StartedOutage(outage_id, alarm_id, affected_ids)


def _mark_detected(conn: sqlite3.Connection, row: sqlite3.Row, at: str) -> None:
    conn.execute(
        "UPDATE outages SET detected = 1, detected_at = ? WHERE outage_id = ?",
        (at, row["outage_id"]),
    )
    _emit_outage(conn, "OUTAGE_STARTED", row["outage_id"], at)


def detect_due(conn: sqlite3.Connection, now: str) -> int:
    rows = conn.execute(
        "SELECT * FROM outages WHERE detected = 0 AND detected_at <= ? "
        "ORDER BY detected_at, outage_id",
        (now,),
    ).fetchall()
    for row in rows:
        _mark_detected(conn, row, row["detected_at"])
    return len(rows)


def ensure_detected(db: Database, now: str) -> None:
    """Cheap read-only check first; take the write lock only when something is due."""
    with db.read() as conn:
        due = conn.execute(
            "SELECT 1 FROM outages WHERE detected = 0 AND detected_at <= ? LIMIT 1", (now,)
        ).fetchone()
    if due is not None:
        with db.write() as conn:
            detect_due(conn, now)


def _detect_now(conn: sqlite3.Connection, row: sqlite3.Row, now: str) -> None:
    """An update or resolution means the NOC knows: detect first so the feed stays ordered."""
    if not row["detected"]:
        _mark_detected(conn, row, now)


def update_outage(
    conn: sqlite3.Connection, outage_id: str, eta: str, eta_source: str, now: str
) -> dict[str, Any]:
    row = _outage(conn, outage_id)
    if row["resolved_at"] is not None:
        raise ApiError(409, "OUTAGE_RESOLVED", f"outage {outage_id} is already resolved")
    _detect_now(conn, row, now)
    conn.execute(
        "UPDATE outages SET eta = ?, eta_source = ?, version = version + 1 WHERE outage_id = ?",
        (eta, eta_source, outage_id),
    )
    _emit_outage(conn, "OUTAGE_UPDATED", outage_id, now)
    return admin_outage_view(_outage(conn, outage_id))


def resolve_outage(conn: sqlite3.Connection, outage_id: str, now: str) -> dict[str, Any]:
    row = _outage(conn, outage_id)
    if row["resolved_at"] is None:
        _detect_now(conn, row, now)
        conn.execute(
            "UPDATE outages SET resolved_at = ?, version = version + 1 WHERE outage_id = ?",
            (now, outage_id),
        )
        _emit_outage(conn, "OUTAGE_RESOLVED", outage_id, now)
        if row["alarm_id"] is not None:
            clear_alarm(conn, row["alarm_id"], now)
    return admin_outage_view(_outage(conn, outage_id))


def close_open_state(conn: sqlite3.Connection, now: str) -> None:
    """Before a reset: tell the feed that everything the NOC had announced is over.

    A consumer of the feed would otherwise be left believing in outages and alarms that no
    longer exist. Outages the NOC had not detected yet were never on the feed and are simply
    forgotten; their alarms were on the feed, so those are cleared.
    """
    declared = conn.execute(
        "SELECT outage_id FROM outages WHERE resolved_at IS NULL AND detected = 1 "
        "ORDER BY started_at, outage_id"
    ).fetchall()
    for row in declared:
        resolve_outage(conn, row["outage_id"], now)
    alarms = conn.execute(
        "SELECT alarm_id FROM alarms WHERE cleared_at IS NULL ORDER BY raised_at, alarm_id"
    ).fetchall()
    for row in alarms:
        clear_alarm(conn, row["alarm_id"], now)


def detected_outage_for(conn: sqlite3.Connection, node_id: str, now: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT o.* FROM outage_nodes n JOIN outages o ON o.outage_id = n.outage_id "
        "WHERE n.node_id = ? AND o.resolved_at IS NULL AND o.detected_at <= ? "
        "ORDER BY o.started_at DESC, o.outage_id DESC LIMIT 1",
        (node_id, now),
    ).fetchone()
    return row


def node_has_outage(conn: sqlite3.Connection, node_id: str, now: str) -> bool:
    """True model: any unresolved outage that has started, detected or not."""
    row = conn.execute(
        "SELECT 1 FROM outage_nodes n JOIN outages o ON o.outage_id = n.outage_id "
        "WHERE n.node_id = ? AND o.resolved_at IS NULL AND o.started_at <= ? LIMIT 1",
        (node_id, now),
    ).fetchone()
    return row is not None
