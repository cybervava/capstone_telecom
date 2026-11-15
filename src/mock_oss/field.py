"""Field Service API: technician slots and appointments.

Slots are generated deterministically: AM 09:00-13:00 and PM 14:00-18:00 IST for today and the
next five days, capacity 3 per slot per pincode. A slot is bookable while it has not started.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from mock_oss.auth import require
from mock_oss.clock import IST_OFFSET, iso, ist_date
from mock_oss.context import get_db, now, now_iso, request_id, require_customer, require_service_of
from mock_oss.errors import ApiError
from mock_oss.idempotency import (
    Created,
    WriteContext,
    idempotency_key,
    resource_for_key,
    run_idempotent,
)
from mock_oss.schemas import AppointmentIn

router = APIRouter(prefix="/field/v1")
CREATE_ENDPOINT = "POST /field/v1/appointments"
SLOT_CAPACITY = 3
WINDOW_DAYS = 5
LISTED_DAYS = 5
_SLOT_RE = re.compile(r"^SL-(\d{6})-(\d{8})-(AM|PM)$")
_HOURS = {"AM": (time(9), time(13)), "PM": (time(14), time(18))}


@dataclass(frozen=True)
class Slot:
    pincode: str
    day: date
    half: str

    @property
    def slot_id(self) -> str:
        return f"SL-{self.pincode}-{self.day:%Y%m%d}-{self.half}"

    def _at(self, local: time) -> datetime:
        return datetime.combine(self.day, local, tzinfo=UTC) - IST_OFFSET

    @property
    def start(self) -> datetime:
        return self._at(_HOURS[self.half][0])

    @property
    def end(self) -> datetime:
        return self._at(_HOURS[self.half][1])

    def bookable(self, moment: datetime) -> bool:
        return self.start > moment and self.day <= ist_date(moment) + timedelta(days=WINDOW_DAYS)


def parse_slot(slot_id: str) -> Slot | None:
    match = _SLOT_RE.match(slot_id)
    if match is None:
        return None
    try:
        day = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError:
        return None
    return Slot(match.group(1), day, match.group(3))


def _booked(conn: sqlite3.Connection, slot_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM appointments WHERE slot_id = ? AND status = 'BOOKED'", (slot_id,)
    ).fetchone()
    return int(row[0])


@router.get("/slots", dependencies=[Depends(require("field:read"))])
def slots(
    request: Request,
    pincode: Annotated[str, Query(pattern=r"^\d{6}$")],
    from_date: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    moment = now(request)
    first = from_date or ist_date(moment)
    candidates = [
        Slot(pincode, first + timedelta(days=d), half)
        for d in range(LISTED_DAYS)
        for half in ("AM", "PM")
    ]
    with get_db(request).read() as conn:
        rows = conn.execute(
            "SELECT slot_id, COUNT(*) AS n FROM appointments "
            "WHERE slot_id LIKE ? AND status = 'BOOKED' GROUP BY slot_id",
            (f"SL-{pincode}-%",),
        ).fetchall()
    booked = {r["slot_id"]: int(r["n"]) for r in rows}
    return {
        "slots": [
            {
                "slot_id": s.slot_id,
                "start": iso(s.start),
                "end": iso(s.end),
                "remaining": max(0, SLOT_CAPACITY - booked.get(s.slot_id, 0)),
            }
            for s in candidates
            if s.bookable(moment)
        ]
    }


def appointment_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "appointment_id": row["appointment_id"],
        "customer_id": row["customer_id"],
        "service_id": row["service_id"],
        "slot_id": row["slot_id"],
        "start": row["start_at"],
        "end": row["end_at"],
        "reason": row["reason"],
        "complaint_ref": row["complaint_ref"],
        "status": row["status"],
        "created_at": row["created_at"],
        "idempotency_key": row["idempotency_key"],
    }


def _get(conn: sqlite3.Connection, appointment_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "APPOINTMENT_NOT_FOUND", f"appointment {appointment_id} not found")
    return row


def _create(body: AppointmentIn, ctx: WriteContext, moment: datetime) -> Created:
    require_customer(ctx.conn, body.customer_id)
    require_service_of(ctx.conn, body.customer_id, body.service_id)
    slot = parse_slot(body.slot_id)
    if slot is None or not slot.bookable(moment):
        raise ApiError(404, "SLOT_NOT_FOUND", f"slot {body.slot_id} does not exist or has passed")
    if _booked(ctx.conn, slot.slot_id) >= SLOT_CAPACITY:
        raise ApiError(409, "SLOT_FULL", f"slot {slot.slot_id} is fully booked")
    appointment_id = f"AP-{uuid.uuid4().hex[:12].upper()}"
    ctx.conn.execute(
        "INSERT INTO appointments (appointment_id, customer_id, service_id, slot_id, start_at, "
        "end_at, reason, complaint_ref, status, created_at, idempotency_key, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'BOOKED', ?, ?, ?)",
        (
            appointment_id,
            body.customer_id,
            body.service_id,
            slot.slot_id,
            iso(slot.start),
            iso(slot.end),
            body.reason,
            body.complaint_ref,
            ctx.now,
            ctx.key,
            ctx.request_id,
        ),
    )
    return Created(201, appointment_view(_get(ctx.conn, appointment_id)), appointment_id)


@router.post("/appointments", dependencies=[Depends(require("field:write"))])
def book(
    body: AppointmentIn, request: Request, key: Annotated[str, Depends(idempotency_key)]
) -> JSONResponse:
    moment = now(request)
    return run_idempotent(
        request,
        endpoint=CREATE_ENDPOINT,
        key=key,
        payload=body.model_dump(mode="json"),
        create=lambda ctx: _create(body, ctx, moment),
    )


@router.get("/appointments/by-key/{key}", dependencies=[Depends(require("field:read"))])
def by_key(key: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        appointment_id = resource_for_key(conn, CREATE_ENDPOINT, key)
        if appointment_id is None:
            raise ApiError(404, "NOT_FOUND", "no appointment for this idempotency key")
        return appointment_view(_get(conn, appointment_id))


@router.get("/appointments", dependencies=[Depends(require("field:read"))])
def list_appointments(
    request: Request,
    service_id: Annotated[str, Query(min_length=1)],
    status: Annotated[Literal["BOOKED", "CANCELLED"] | None, Query()] = None,
) -> dict[str, Any]:
    with get_db(request).read() as conn:
        rows = conn.execute(
            "SELECT * FROM appointments WHERE service_id = ? AND (? IS NULL OR status = ?) "
            "ORDER BY created_at, rowid LIMIT 500",
            (service_id, status, status),
        ).fetchall()
    return {"appointments": [appointment_view(r) for r in rows]}


@router.post(
    "/appointments/{appointment_id}/cancel", dependencies=[Depends(require("field:write"))]
)
def cancel(appointment_id: str, request: Request) -> dict[str, Any]:
    """Cancelling an already cancelled appointment is a no-op."""
    with get_db(request).write() as conn:
        row = _get(conn, appointment_id)
        if row["status"] == "BOOKED":
            conn.execute(
                "UPDATE appointments SET status = 'CANCELLED', cancelled_at = ?, request_id = ? "
                "WHERE appointment_id = ?",
                (now_iso(request), request_id(request), appointment_id),
            )
            row = _get(conn, appointment_id)
        return appointment_view(row)
