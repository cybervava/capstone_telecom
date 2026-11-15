"""Idempotent creates: the key record is written in the same transaction as the resource."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from mock_oss.clock import iso
from mock_oss.db import Database
from mock_oss.errors import ApiError

MAX_KEY_LENGTH = 200


@dataclass(frozen=True)
class Created:
    """What a create function returns: HTTP status, response body, resource id."""

    status: int
    body: dict[str, Any]
    resource_id: str


@dataclass(frozen=True)
class WriteContext:
    conn: sqlite3.Connection
    key: str
    request_id: str
    now: str


def idempotency_key(request: Request) -> str:
    """FastAPI dependency: the ``Idempotency-Key`` header, required on creating POSTs."""
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if len(key) > MAX_KEY_LENGTH:
        raise ApiError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is too long")
    return key


def body_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(row: sqlite3.Row, digest: str) -> JSONResponse:
    if row["body_hash"] != digest:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "key already used with a different body")
    return JSONResponse(
        status_code=int(row["status"]),
        content=json.loads(row["response"]),
        headers={"Idempotent-Replay": "true"},
    )


def run_idempotent(
    request: Request,
    *,
    endpoint: str,
    key: str,
    payload: Any,
    create: Callable[[WriteContext], Created],
) -> JSONResponse:
    """Replay, reject (409) or create-and-record, atomically. Failed creates are not stored."""
    db: Database = request.app.state.db
    digest = body_hash(payload)
    now = iso(request.app.state.clock.now())
    with db.write() as conn:
        row = conn.execute(
            "SELECT body_hash, status, response FROM idempotency WHERE endpoint = ? AND key = ?",
            (endpoint, key),
        ).fetchone()
        if row is not None:
            return _replay(row, digest)
        request_id: str = request.state.request_id
        created = create(WriteContext(conn, key, request_id, now))
        conn.execute(
            "INSERT INTO idempotency (endpoint, key, body_hash, status, response, resource_id, "
            "created_at, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                endpoint,
                key,
                digest,
                created.status,
                json.dumps(created.body),
                created.resource_id,
                now,
                request_id,
            ),
        )
    return JSONResponse(status_code=created.status, content=created.body)


def resource_for_key(conn: sqlite3.Connection, endpoint: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT resource_id FROM idempotency WHERE endpoint = ? AND key = ?", (endpoint, key)
    ).fetchone()
    return str(row["resource_id"]) if row is not None else None
