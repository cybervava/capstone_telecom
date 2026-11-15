"""Typed accessors for per-app state used by the routers."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import Request

from mock_oss.clock import Clock, iso
from mock_oss.db import Database
from mock_oss.errors import ApiError


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def now(request: Request) -> datetime:
    return get_clock(request).now()


def now_iso(request: Request) -> str:
    return iso(now(request))


def request_id(request: Request) -> str:
    value: str = request.state.request_id
    return value


def require_customer(conn: sqlite3.Connection, customer_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "CUSTOMER_NOT_FOUND", f"customer {customer_id} not found")
    return row


def require_service_of(conn: sqlite3.Connection, customer_id: str, service_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM services WHERE service_id = ? AND customer_id = ?",
        (service_id, customer_id),
    ).fetchone()
    if row is None:
        raise ApiError(
            404, "SERVICE_NOT_FOUND", f"service {service_id} not found for customer {customer_id}"
        )
    return row
