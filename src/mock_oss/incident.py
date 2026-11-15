"""Incident Management API: incidents, complaint links and complaint registrations.

Deliberately no de-duplication of incidents by node: two keys make two incidents.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from mock_oss.auth import require
from mock_oss.clock import iso
from mock_oss.context import get_db, now_iso, request_id
from mock_oss.errors import ApiError
from mock_oss.idempotency import (
    Created,
    WriteContext,
    idempotency_key,
    resource_for_key,
    run_idempotent,
)
from mock_oss.schemas import ComplaintIn, ComplaintLinkIn, IncidentIn, IncidentPatch, IncidentStatus

router = APIRouter(prefix="/incident/v1")
READ = [Depends(require("incident:read"))]
WRITE = [Depends(require("incident:write"))]
INCIDENT_ENDPOINT = "POST /incident/v1/incidents"
COMPLAINT_ENDPOINT = "POST /incident/v1/complaints"
_INCIDENT_FIELDS = [
    "incident_id",
    "node_id",
    "service_type",
    "kind",
    "outage_id",
    "summary",
    "status",
    "created_at",
    "updated_at",
    "idempotency_key",
]


LINK_ENDPOINT = "POST /incident/v1/incidents/{incident_id}/complaints"


def _link_resource(incident_id: str, ref: str) -> str:
    return f"{incident_id}|{ref}"


def _incident_row(conn: sqlite3.Connection, incident_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "INCIDENT_NOT_FOUND", f"incident {incident_id} not found")
    return row


def _refs(conn: sqlite3.Connection, incident_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT complaint_ref FROM incident_complaints WHERE incident_id = ? "
        "ORDER BY linked_at, rowid",
        (incident_id,),
    ).fetchall()
    return [str(r["complaint_ref"]) for r in rows]


def incident_view(row: sqlite3.Row, refs: list[str]) -> dict[str, Any]:
    return {**{k: row[k] for k in _INCIDENT_FIELDS}, "complaint_refs": refs}


def _load_incident(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    return incident_view(_incident_row(conn, incident_id), _refs(conn, incident_id))


def _create_incident(body: IncidentIn, ctx: WriteContext) -> Created:
    if (
        ctx.conn.execute("SELECT 1 FROM nodes WHERE node_id = ?", (body.node_id,)).fetchone()
        is None
    ):
        raise ApiError(404, "NODE_NOT_FOUND", f"node {body.node_id} not found")
    incident_id = f"INC-{uuid.uuid4().hex[:12].upper()}"
    ctx.conn.execute(
        "INSERT INTO incidents (incident_id, node_id, service_type, kind, outage_id, summary, "
        "status, created_at, updated_at, idempotency_key, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
        (
            incident_id,
            body.node_id,
            body.service_type,
            body.kind,
            body.outage_id,
            body.summary,
            ctx.now,
            ctx.now,
            ctx.key,
            ctx.request_id,
        ),
    )
    return Created(201, _load_incident(ctx.conn, incident_id), incident_id)


@router.post("/incidents", dependencies=WRITE)
def create_incident(
    body: IncidentIn, request: Request, key: Annotated[str, Depends(idempotency_key)]
) -> JSONResponse:
    return run_idempotent(
        request,
        endpoint=INCIDENT_ENDPOINT,
        key=key,
        payload=body.model_dump(mode="json"),
        create=lambda ctx: _create_incident(body, ctx),
    )


@router.get("/incidents", dependencies=READ)
def list_incidents(
    request: Request,
    node_id: Annotated[str | None, Query(min_length=1)] = None,
    status: Annotated[IncidentStatus | None, Query()] = None,
) -> dict[str, Any]:
    args = (node_id, node_id, status, status)
    with get_db(request).read() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE (? IS NULL OR node_id = ?) "
            "AND (? IS NULL OR status = ?) ORDER BY created_at, rowid LIMIT 500",
            args,
        ).fetchall()
        links = conn.execute(
            "SELECT l.incident_id, l.complaint_ref FROM incident_complaints l "
            "JOIN incidents i ON i.incident_id = l.incident_id "
            "WHERE (? IS NULL OR i.node_id = ?) AND (? IS NULL OR i.status = ?) "
            "ORDER BY l.linked_at, l.rowid",
            args,
        ).fetchall()
    refs: dict[str, list[str]] = {}
    for link in links:
        refs.setdefault(link["incident_id"], []).append(link["complaint_ref"])
    return {"incidents": [incident_view(r, refs.get(r["incident_id"], [])) for r in rows]}


@router.get("/incidents/by-key/{key}", dependencies=READ)
def incident_by_key(key: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        incident_id = resource_for_key(conn, INCIDENT_ENDPOINT, key)
        if incident_id is None:
            raise ApiError(404, "NOT_FOUND", "no incident for this idempotency key")
        return _load_incident(conn, incident_id)


@router.get("/incidents/{incident_id}", dependencies=READ)
def get_incident(incident_id: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        return _load_incident(conn, incident_id)


@router.patch("/incidents/{incident_id}", dependencies=WRITE)
def patch_incident(incident_id: str, body: IncidentPatch, request: Request) -> dict[str, Any]:
    """Naturally idempotent partial update; only the fields present in the body change."""
    with get_db(request).write() as conn:
        current = _incident_row(conn, incident_id)
        merged = {
            f: getattr(body, f) if f in body.model_fields_set else current[f]
            for f in ("kind", "outage_id", "status", "summary")
        }
        conn.execute(
            "UPDATE incidents SET kind = ?, outage_id = ?, status = ?, summary = ?, "
            "updated_at = ?, request_id = ? WHERE incident_id = ?",
            (
                merged["kind"],
                merged["outage_id"],
                merged["status"],
                merged["summary"],
                now_iso(request),
                request_id(request),
                incident_id,
            ),
        )
        return _load_incident(conn, incident_id)


# ---------------------------------------------------------------- complaint links


def link_view(row: sqlite3.Row) -> dict[str, Any]:
    keys = ["incident_id", "complaint_ref", "contact_id", "customer_id", "linked_at"]
    return {k: row[k] for k in keys}


def _link_row(conn: sqlite3.Connection, incident_id: str, ref: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM incident_complaints WHERE incident_id = ? AND complaint_ref = ?",
        (incident_id, ref),
    ).fetchone()
    return row


def _create_link(incident_id: str, body: ComplaintLinkIn, ctx: WriteContext) -> Created:
    _incident_row(ctx.conn, incident_id)
    existing = _link_row(ctx.conn, incident_id, body.complaint_ref)
    if existing is not None:
        return Created(200, link_view(existing), _link_resource(incident_id, body.complaint_ref))
    ctx.conn.execute(
        "INSERT INTO incident_complaints (incident_id, complaint_ref, contact_id, customer_id, "
        "linked_at, idempotency_key, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            incident_id,
            body.complaint_ref,
            body.contact_id,
            body.customer_id,
            ctx.now,
            ctx.key,
            ctx.request_id,
        ),
    )
    row = _link_row(ctx.conn, incident_id, body.complaint_ref)
    assert row is not None
    return Created(201, link_view(row), _link_resource(incident_id, body.complaint_ref))


@router.post("/incidents/{incident_id}/complaints", dependencies=WRITE)
def link_complaint(
    incident_id: str,
    body: ComplaintLinkIn,
    request: Request,
    key: Annotated[str, Depends(idempotency_key)],
) -> JSONResponse:
    payload = {"incident_id": incident_id, **body.model_dump(mode="json")}
    return run_idempotent(
        request,
        endpoint=LINK_ENDPOINT,
        key=key,
        payload=payload,
        create=lambda ctx: _create_link(incident_id, body, ctx),
    )


@router.get("/incidents/{incident_id}/complaints/by-key/{key}", dependencies=READ)
def link_by_key(incident_id: str, key: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        resource = resource_for_key(conn, LINK_ENDPOINT, key)
        owner, _, ref = (resource or "").partition("|")
        row = _link_row(conn, incident_id, ref) if owner == incident_id else None
    if row is None:
        raise ApiError(404, "NOT_FOUND", "no complaint link for this idempotency key")
    return link_view(row)


# ---------------------------------------------------------------- complaint registrations


def complaint_view(row: sqlite3.Row) -> dict[str, Any]:
    keys = ["complaint_ref", "received_at", "channel", "category", "customer_id", "registered_at"]
    return {k: row[k] for k in keys}


def _complaint_row(conn: sqlite3.Connection, ref: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM complaints WHERE complaint_ref = ?", (ref,)
    ).fetchone()
    return row


def _register(body: ComplaintIn, ctx: WriteContext) -> Created:
    received = iso(body.received_at)
    existing = _complaint_row(ctx.conn, body.complaint_ref)
    if existing is not None:
        same = (
            existing["received_at"],
            existing["channel"],
            existing["category"],
            existing["customer_id"],
        ) == (received, body.channel, body.category, body.customer_id)
        if not same:
            raise ApiError(
                409,
                "COMPLAINT_REF_CONFLICT",
                f"{body.complaint_ref} is registered with different details",
            )
        reply = {"complaint_ref": body.complaint_ref, "registered_at": existing["registered_at"]}
        return Created(200, reply, body.complaint_ref)
    ctx.conn.execute(
        "INSERT INTO complaints (complaint_ref, received_at, channel, category, customer_id, "
        "registered_at, idempotency_key, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            body.complaint_ref,
            received,
            body.channel,
            body.category,
            body.customer_id,
            ctx.now,
            ctx.key,
            ctx.request_id,
        ),
    )
    return Created(
        201, {"complaint_ref": body.complaint_ref, "registered_at": ctx.now}, body.complaint_ref
    )


@router.post("/complaints", dependencies=WRITE)
def register_complaint(
    body: ComplaintIn, request: Request, key: Annotated[str, Depends(idempotency_key)]
) -> JSONResponse:
    payload = {**body.model_dump(mode="json"), "received_at": iso(body.received_at)}
    return run_idempotent(
        request,
        endpoint=COMPLAINT_ENDPOINT,
        key=key,
        payload=payload,
        create=lambda ctx: _register(body, ctx),
    )


@router.get("/complaints/by-key/{key}", dependencies=READ)
def complaint_by_key(key: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        ref = resource_for_key(conn, COMPLAINT_ENDPOINT, key)
        row = _complaint_row(conn, ref) if ref is not None else None
    if row is None:
        raise ApiError(404, "NOT_FOUND", "no complaint for this idempotency key")
    return complaint_view(row)


@router.get("/complaints/{complaint_ref}", dependencies=READ)
def get_complaint(complaint_ref: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        row = _complaint_row(conn, complaint_ref)
    if row is None:
        raise ApiError(404, "COMPLAINT_NOT_FOUND", f"complaint {complaint_ref} not found")
    return complaint_view(row)
