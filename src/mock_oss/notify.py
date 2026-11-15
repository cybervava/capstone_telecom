"""Notification gateway (simulated SMS / push). Messages are recorded, never sent."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from mock_oss.auth import require
from mock_oss.context import get_db, require_customer
from mock_oss.errors import ApiError
from mock_oss.idempotency import (
    Created,
    WriteContext,
    idempotency_key,
    resource_for_key,
    run_idempotent,
)
from mock_oss.schemas import MessageIn

router = APIRouter(prefix="/notify/v1", dependencies=[Depends(require("notify:write"))])
CREATE_ENDPOINT = "POST /notify/v1/messages"


def message_view(row: sqlite3.Row) -> dict[str, Any]:
    keys = [
        "message_id",
        "customer_id",
        "channel",
        "template_id",
        "body",
        "outage_id",
        "status",
        "created_at",
        "idempotency_key",
    ]
    return {k: row[k] for k in keys}


def _create(body: MessageIn, ctx: WriteContext) -> Created:
    require_customer(ctx.conn, body.customer_id)
    message_id = f"MSG-{uuid.uuid4().hex[:12].upper()}"
    ctx.conn.execute(
        "INSERT INTO messages (message_id, customer_id, channel, template_id, body, outage_id, "
        "status, created_at, idempotency_key, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 'ACCEPTED', ?, ?, ?)",
        (
            message_id,
            body.customer_id,
            body.channel,
            body.template_id,
            body.body,
            body.outage_id,
            ctx.now,
            ctx.key,
            ctx.request_id,
        ),
    )
    return Created(
        202, {"message_id": message_id, "status": "ACCEPTED", "created_at": ctx.now}, message_id
    )


@router.post("/messages")
def send(
    body: MessageIn, request: Request, key: Annotated[str, Depends(idempotency_key)]
) -> JSONResponse:
    return run_idempotent(
        request,
        endpoint=CREATE_ENDPOINT,
        key=key,
        payload=body.model_dump(mode="json"),
        create=lambda ctx: _create(body, ctx),
    )


@router.get("/messages/by-key/{key}")
def by_key(key: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        message_id = resource_for_key(conn, CREATE_ENDPOINT, key)
        row = None
        if message_id is not None:
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "no message for this idempotency key")
    return message_view(row)


@router.get("/messages")
def list_messages(
    request: Request,
    customer_id: Annotated[str | None, Query(min_length=1)] = None,
    outage_id: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    if customer_id is None and outage_id is None:
        raise ApiError(400, "FILTER_REQUIRED", "give at least one of customer_id, outage_id")
    with get_db(request).read() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE (? IS NULL OR customer_id = ?) "
            "AND (? IS NULL OR outage_id = ?) ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (customer_id, customer_id, outage_id, outage_id, limit),
        ).fetchall()
    return {"messages": [message_view(r) for r in rows]}
