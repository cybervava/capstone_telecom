"""What the platform has done in the enterprise systems, for the demo console:
``GET /admin/v1/activity``.

One read per refresh of the console's evidence panel: the notices sent for an outage (and
whether any reached a customer who said no to them), a customer's notices and credits, a line's
technician visits, and a node's incidents with the complaints linked to each.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from mock_oss.auth import require
from mock_oss.billing import credit_view
from mock_oss.context import get_db, require_customer
from mock_oss.errors import ApiError
from mock_oss.field import appointment_view
from mock_oss.incident import incident_view
from mock_oss.notify import message_view

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(require("admin"))])

Id = Annotated[str | None, Query(min_length=1, max_length=64)]


def _outage(conn: sqlite3.Connection, outage_id: str, limit: int) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM outages WHERE outage_id = ?", (outage_id,)).fetchone() is None:
        raise ApiError(404, "OUTAGE_NOT_FOUND", f"outage {outage_id} not found")
    notices = conn.execute(
        "SELECT template_id, COUNT(*) AS sent, COUNT(DISTINCT customer_id) AS customers "
        "FROM messages WHERE outage_id = ? GROUP BY template_id ORDER BY template_id",
        (outage_id,),
    ).fetchall()
    without_consent = conn.execute(
        "SELECT COUNT(*) FROM messages m JOIN customers c ON c.customer_id = m.customer_id "
        "LEFT JOIN consent_overrides o ON o.customer_id = m.customer_id "
        # Consent is the runtime override where there is one, else the CRM record.
        "WHERE m.outage_id = ? AND COALESCE(o.outage_notifications, c.consent) = 0",
        (outage_id,),
    ).fetchone()[0]
    declined = conn.execute(
        "SELECT COUNT(DISTINCT s.customer_id) FROM services s "
        "JOIN truth t ON t.service_id = s.service_id "
        "JOIN customers c ON c.customer_id = s.customer_id "
        "LEFT JOIN consent_overrides o ON o.customer_id = s.customer_id "
        "WHERE t.true_node_id IN (SELECT node_id FROM outage_nodes WHERE outage_id = ?) "
        "AND COALESCE(o.outage_notifications, c.consent) = 0",
        (outage_id,),
    ).fetchone()[0]
    latest = conn.execute(
        "SELECT * FROM messages WHERE outage_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (outage_id, limit),
    ).fetchall()
    return {
        "outage_id": outage_id,
        "notices": [dict(row) for row in notices],
        "sent_without_consent": int(without_consent),
        "declined_on_affected_nodes": int(declined),
        "latest": [message_view(row) for row in latest],
    }


def _customer(conn: sqlite3.Connection, customer_id: str, limit: int) -> dict[str, Any]:
    require_customer(conn, customer_id)
    messages = conn.execute(
        "SELECT * FROM messages WHERE customer_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (customer_id, limit),
    ).fetchall()
    credits = conn.execute(
        "SELECT * FROM credits WHERE customer_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (customer_id, limit),
    ).fetchall()
    return {
        "customer_id": customer_id,
        "messages": [message_view(row) for row in messages],
        "credits": [credit_view(row) for row in credits],
    }


def _service(conn: sqlite3.Connection, service_id: str, limit: int) -> dict[str, Any]:
    if (
        conn.execute("SELECT 1 FROM services WHERE service_id = ?", (service_id,)).fetchone()
        is None
    ):
        raise ApiError(404, "SERVICE_NOT_FOUND", f"service {service_id} not found")
    rows = conn.execute(
        "SELECT * FROM appointments WHERE service_id = ? ORDER BY created_at DESC, rowid DESC "
        "LIMIT ?",
        (service_id, limit),
    ).fetchall()
    return {"service_id": service_id, "appointments": [appointment_view(row) for row in rows]}


def _node(conn: sqlite3.Connection, node_id: str, limit: int) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone() is None:
        raise ApiError(404, "NODE_NOT_FOUND", f"node {node_id} not found")
    rows = conn.execute(
        "SELECT * FROM incidents WHERE node_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (node_id, limit),
    ).fetchall()
    incidents = []
    for row in rows:
        refs = conn.execute(
            "SELECT complaint_ref FROM incident_complaints WHERE incident_id = ? "
            "ORDER BY linked_at, rowid",
            (row["incident_id"],),
        ).fetchall()
        incidents.append(incident_view(row, [str(ref["complaint_ref"]) for ref in refs]))
    return {"node_id": node_id, "incidents": incidents}


@router.get("/activity")
def activity(
    request: Request,
    outage_id: Id = None,
    customer_id: Id = None,
    service_id: Id = None,
    node_id: Id = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    if not any((outage_id, customer_id, service_id, node_id)):
        raise ApiError(
            400,
            "FILTER_REQUIRED",
            "give at least one of outage_id, customer_id, service_id, node_id",
        )
    with get_db(request).read() as conn:
        return {
            "outage": _outage(conn, outage_id, limit) if outage_id else None,
            "customer": _customer(conn, customer_id, limit) if customer_id else None,
            "service": _service(conn, service_id, limit) if service_id else None,
            "node": _node(conn, node_id, limit) if node_id else None,
        }
