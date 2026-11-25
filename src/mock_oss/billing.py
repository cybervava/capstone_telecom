"""Billing API: service credits."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from mock_oss.auth import require
from mock_oss.clock import IST_OFFSET, iso
from mock_oss.context import get_db, require_customer, require_service_of
from mock_oss.errors import ApiError
from mock_oss.idempotency import (
    Created,
    WriteContext,
    idempotency_key,
    resource_for_key,
    run_idempotent,
)
from mock_oss.schemas import CreditIn

router = APIRouter(prefix="/billing/v1")
CREATE_ENDPOINT = "POST /billing/v1/credits"
MAX_CREDIT = Decimal("5000.00")
CENT = Decimal("0.01")


def validate_amount(amount: Decimal) -> Decimal:
    """``0 < amount <= 5000.00`` with at most two decimal places."""
    if not amount.is_finite() or amount <= 0 or amount > MAX_CREDIT:
        raise ApiError(422, "INVALID_AMOUNT", "amount_usd must satisfy 0 < amount <= 5000.00")
    if amount != amount.quantize(CENT):
        raise ApiError(422, "INVALID_AMOUNT", "amount_usd has more than two decimal places")
    return amount.quantize(CENT)


def credit_view(row: sqlite3.Row) -> dict[str, Any]:
    keys = ["credit_id", "customer_id", "service_id"]
    rest = [
        "reason_code",
        "outage_id",
        "policy_version",
        "complaint_ref",
        "approved_by",
        "status",
        "created_at",
        "idempotency_key",
    ]
    return {**{k: row[k] for k in keys}, "amount_usd": row["amount"], **{k: row[k] for k in rest}}


def _get_credit(conn: sqlite3.Connection, credit_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM credits WHERE credit_id = ?", (credit_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"credit {credit_id} not found")
    return row


def _create_credit(body: CreditIn, ctx: WriteContext) -> Created:
    amount = validate_amount(body.amount_usd)
    customer = require_customer(ctx.conn, body.customer_id)
    require_service_of(ctx.conn, body.customer_id, body.service_id)
    if customer["status"] == "suspended":
        raise ApiError(409, "ACCOUNT_SUSPENDED", f"account {body.customer_id} is suspended")
    credit_id = f"CR-{uuid.uuid4().hex[:12].upper()}"
    ctx.conn.execute(
        "INSERT INTO credits (credit_id, customer_id, service_id, amount, reason_code, outage_id, "
        "policy_version, complaint_ref, approved_by, status, created_at, idempotency_key, "
        "request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'APPLIED', ?, ?, ?)",
        (
            credit_id,
            body.customer_id,
            body.service_id,
            f"{amount:.2f}",
            body.reason_code,
            body.outage_id,
            body.policy_version,
            body.complaint_ref,
            body.approved_by,
            ctx.now,
            ctx.key,
            ctx.request_id,
        ),
    )
    return Created(201, credit_view(_get_credit(ctx.conn, credit_id)), credit_id)


@router.post("/credits", dependencies=[Depends(require("billing:write"))])
def create_credit(
    body: CreditIn, request: Request, key: Annotated[str, Depends(idempotency_key)]
) -> JSONResponse:
    return run_idempotent(
        request,
        endpoint=CREATE_ENDPOINT,
        key=key,
        payload=body.model_dump(mode="json"),
        create=lambda ctx: _create_credit(body, ctx),
    )


@router.get("/credits/by-key/{key}", dependencies=[Depends(require("billing:read"))])
def credit_by_key(key: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        credit_id = resource_for_key(conn, CREATE_ENDPOINT, key)
        if credit_id is None:
            raise ApiError(404, "NOT_FOUND", "no credit for this idempotency key")
        return credit_view(_get_credit(conn, credit_id))


@router.get("/accounts/{customer_id}/credits", dependencies=[Depends(require("billing:read"))])
def list_credits(
    customer_id: str, request: Request, since: Annotated[date | None, Query()] = None
) -> dict[str, Any]:
    # Dates in the contract are IST days: one starts at 18:30 UTC the day before.
    floor = iso(datetime.combine(since, time(), tzinfo=UTC) - IST_OFFSET) if since else ""
    with get_db(request).read() as conn:
        require_customer(conn, customer_id)
        rows = conn.execute(
            "SELECT * FROM credits WHERE customer_id = ? AND created_at >= ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 500",
            (customer_id, floor),
        ).fetchall()
    return {"credits": [credit_view(r) for r in rows]}
