"""Simulator controls (scope ``admin``): outages, alarms, faults, consent, chaos, state, reset.

Never subject to fault injection.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from mock_oss.auth import require
from mock_oss.chaos import ChaosRules, build_rule
from mock_oss.clock import iso
from mock_oss.context import get_db, now, now_iso, request_id, require_customer
from mock_oss.db import WRITE_TABLES, clear_runtime, count_rows
from mock_oss.errors import ApiError
from mock_oss.middleware import RequestCounters
from mock_oss.noc import (
    admin_outage_view,
    clear_alarm,
    close_open_state,
    ensure_detected,
    raise_alarm,
    require_active_node,
    resolve_outage,
    start_outage,
    update_outage,
)
from mock_oss.schemas import AlarmIn, ChaosIn, ConsentIn, LineFaultIn, OutageIn, OutageUpdateIn

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(require("admin"))])


@router.post("/outages")
def create_outage(body: OutageIn, request: Request) -> JSONResponse:
    with get_db(request).write() as conn:
        started = start_outage(conn, body, now(request), request_id(request))
    return JSONResponse(
        status_code=201,
        content={
            "outage_id": started.outage_id,
            "alarm_id": started.alarm_id,
            "affected_node_ids": started.affected_node_ids,
        },
    )


@router.post("/outages/{outage_id}/update")
def update(outage_id: str, body: OutageUpdateIn, request: Request) -> dict[str, Any]:
    moment = now(request)
    eta = iso(moment + timedelta(minutes=body.eta_minutes))
    with get_db(request).write() as conn:
        return update_outage(conn, outage_id, eta, body.eta_source, iso(moment))


@router.post("/outages/{outage_id}/resolve")
def resolve(outage_id: str, request: Request) -> dict[str, Any]:
    with get_db(request).write() as conn:
        return resolve_outage(conn, outage_id, now_iso(request))


@router.get("/outages")
def list_outages(request: Request, active: bool | None = None) -> dict[str, Any]:
    db = get_db(request)
    ensure_detected(db, now_iso(request))
    with db.read() as conn:
        rows = conn.execute(
            "SELECT * FROM outages WHERE (? IS NULL OR (resolved_at IS NULL) = ?) "
            "ORDER BY started_at, outage_id",
            (active, active),
        ).fetchall()
    return {"outages": [admin_outage_view(r) for r in rows]}


@router.post("/alarms")
def create_alarm(body: AlarmIn, request: Request) -> JSONResponse:
    with get_db(request).write() as conn:
        require_active_node(conn, body.node_id)
        alarm_id = raise_alarm(conn, body.node_id, body.alarm_type, body.severity, now_iso(request))
    return JSONResponse(status_code=201, content={"alarm_id": alarm_id})


@router.post("/alarms/{alarm_id}/clear")
def clear(alarm_id: str, request: Request) -> dict[str, Any]:
    with get_db(request).write() as conn:
        return clear_alarm(conn, alarm_id, now_iso(request))


@router.post("/line-faults")
def line_fault(body: LineFaultIn, request: Request) -> dict[str, Any]:
    with get_db(request).write() as conn:
        exists = conn.execute(
            "SELECT 1 FROM services WHERE service_id = ?", (body.service_id,)
        ).fetchone()
        if exists is None:
            raise ApiError(404, "SERVICE_NOT_FOUND", f"service {body.service_id} not found")
        conn.execute(
            "INSERT INTO line_fault_overrides (service_id, fault, result) VALUES (?, ?, ?) "
            "ON CONFLICT(service_id) DO UPDATE SET fault = excluded.fault, "
            "result = excluded.result",
            (body.service_id, int(body.fault), body.result if body.fault else None),
        )
    return {
        "service_id": body.service_id,
        "fault": body.fault,
        "result": body.result if body.fault else None,
    }


@router.post("/consent")
def set_consent(body: ConsentIn, request: Request) -> dict[str, Any]:
    channel = (body.channel or "sms") if body.outage_notifications else "none"
    updated = now_iso(request)
    with get_db(request).write() as conn:
        require_customer(conn, body.customer_id)
        conn.execute(
            "INSERT INTO consent_overrides (customer_id, outage_notifications, channel, "
            "updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(customer_id) DO UPDATE SET "
            "outage_notifications = excluded.outage_notifications, channel = excluded.channel, "
            "updated_at = excluded.updated_at",
            (body.customer_id, int(body.outage_notifications), channel, updated),
        )
    return {
        "customer_id": body.customer_id,
        "outage_notifications": body.outage_notifications,
        "channel": channel,
        "updated_at": updated,
    }


def _rules(request: Request) -> ChaosRules:
    rules: ChaosRules = request.app.state.chaos
    return rules


@router.post("/chaos")
def set_chaos(body: ChaosIn, request: Request) -> dict[str, Any]:
    rule = build_rule(
        body.target,
        body.mode,
        latency_ms=body.latency_ms,
        error_rate=body.error_rate,
        hold_seconds=body.hold_seconds,
        ttl_seconds=body.ttl_seconds,
        now=now(request),
    )
    _rules(request).apply(rule)
    return rule.describe()


@router.get("/chaos")
def list_chaos(request: Request) -> dict[str, Any]:
    return {"rules": [r.describe() for r in _rules(request).active(now(request))]}


@router.delete("/chaos")
def clear_chaos(request: Request) -> dict[str, int]:
    return {"cleared": _rules(request).clear()}


def _counters(request: Request) -> RequestCounters:
    counters: RequestCounters = request.app.state.counters
    return counters


@router.get("/state")
def state(request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        tables = {name: count_rows(conn, name) for name in WRITE_TABLES}
    return {"tables": tables, "requests": _counters(request).snapshot()}


@router.post("/reset")
def reset(request: Request) -> dict[str, str]:
    with get_db(request).write() as conn:
        close_open_state(conn, now_iso(request))
        clear_runtime(conn)
    _rules(request).clear()
    _counters(request).reset()
    return {"status": "reset"}
