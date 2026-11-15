"""Network Monitoring API: node status, line tests and the NOC event feed."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from mock_oss.auth import require
from mock_oss.clock import iso
from mock_oss.config import Settings
from mock_oss.context import get_clock, get_db, now_iso
from mock_oss.db import read_counter
from mock_oss.errors import ApiError
from mock_oss.noc import (
    EVENT_SEQ,
    active_alarms,
    detected_outage_for,
    ensure_detected,
    node_has_outage,
    outage_view,
    require_active_node,
)
from mock_oss.schemas import LineTestIn

router = APIRouter(prefix="/network/v1")
POLL_INTERVAL = 0.1

_FAIL_CHECKS = {"link": "DOWN", "optical_rx_dbm": None, "sync": False, "packet_loss_pct": 100.0}
_INCONCLUSIVE_CHECKS = {
    "link": "UNKNOWN",
    "optical_rx_dbm": None,
    "sync": None,
    "packet_loss_pct": None,
}


def _status_body(request: Request, node_id: str) -> dict[str, Any]:
    db = get_db(request)
    observed = now_iso(request)
    ensure_detected(db, observed)
    with db.read() as conn:
        node = require_active_node(conn, node_id)
        outage = detected_outage_for(conn, node_id, observed)
        return {
            "node_id": node_id,
            "status": "OUTAGE" if outage is not None else "OPERATIONAL",
            "outage": outage_view(outage) if outage is not None else None,
            "active_alarms": active_alarms(conn, node),
            "observed_at": observed,
            "feed_seq": read_counter(conn, EVENT_SEQ),
        }


@router.get("/nodes/{node_id}/status", dependencies=[Depends(require("network:read"))])
def node_status(node_id: str, request: Request) -> dict[str, Any]:
    return _status_body(request, node_id)


def _service_truth(conn: sqlite3.Connection, service_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT s.service_id, t.true_node_id, o.fault, o.result AS override_result, "
        "f.result AS seed_result FROM services s "
        "LEFT JOIN truth t ON t.service_id = s.service_id "
        "LEFT JOIN line_fault_overrides o ON o.service_id = s.service_id "
        "LEFT JOIN seed_line_faults f ON f.service_id = s.service_id WHERE s.service_id = ?",
        (service_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", f"service {service_id} not found")
    return row


def _line_fault(row: sqlite3.Row) -> str | None:
    """Runtime overrides win over seeded faults; returns the forced result or None."""
    if row["fault"] is not None:
        return str(row["override_result"]) if row["fault"] else None
    return str(row["seed_result"]) if row["seed_result"] is not None else None


def _pass_checks(test_id: str) -> dict[str, Any]:
    rng = random.Random(test_id)
    return {
        "link": "UP",
        "optical_rx_dbm": round(rng.uniform(-24.0, -14.0), 1),
        "sync": True,
        "packet_loss_pct": round(rng.uniform(0.0, 0.9), 2),
    }


def _evaluate(request: Request, service_id: str, test_id: str) -> dict[str, Any]:
    tested_at = now_iso(request)
    with get_db(request).read() as conn:
        row = _service_truth(conn, service_id)
        node_id = row["true_node_id"]
        forced = _line_fault(row)
        checks: dict[str, Any]
        in_outage = node_id is not None and node_has_outage(conn, node_id, tested_at)
        if forced == "INCONCLUSIVE":
            result, checks = "INCONCLUSIVE", _INCONCLUSIVE_CHECKS
        elif in_outage or forced == "FAIL":
            result, checks = "FAIL", _FAIL_CHECKS
        else:
            result, checks = "PASS", _pass_checks(test_id)
    return {
        "test_id": test_id,
        "service_id": service_id,
        "node_id": node_id,
        "result": result,
        "checks": checks,
        "tested_at": tested_at,
    }


def _exists(request: Request, service_id: str) -> None:
    with get_db(request).read() as conn:
        _service_truth(conn, service_id)


@router.post("/line-tests", dependencies=[Depends(require("network:test"))])
async def line_test(body: LineTestIn, request: Request) -> dict[str, Any]:
    await run_in_threadpool(_exists, request, body.service_id)
    settings: Settings = request.app.state.settings
    low, high = settings.line_test_delay_ms
    if high > 0:
        rng: random.Random = request.app.state.rng
        await get_clock(request).sleep(rng.randint(low, high) / 1000)
    test_id = f"LT-{uuid.uuid4().hex[:10]}"
    return await run_in_threadpool(_evaluate, request, body.service_id, test_id)


def _page(request: Request, after_seq: int, limit: int) -> dict[str, Any]:
    db = get_db(request)
    ensure_detected(db, now_iso(request))
    with db.read() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (after_seq, limit)
        ).fetchall()
        last_seq = read_counter(conn, EVENT_SEQ)
    return {"events": [json.loads(r["payload"]) for r in rows], "last_seq": last_seq}


@router.get("/events", dependencies=[Depends(require("network:feed"))])
async def events(
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    wait_seconds: Annotated[float, Query(ge=0, le=25)] = 0,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while True:
        page = await run_in_threadpool(_page, request, after_seq, limit)
        remaining = deadline - loop.time()
        if page["events"] or remaining <= 0:
            return {**page, "generated_at": iso(get_clock(request).now())}
        await asyncio.sleep(min(POLL_INTERVAL, remaining))
