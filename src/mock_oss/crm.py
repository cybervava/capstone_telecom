"""CRM API: customer lookup, profile, consent and identity verification."""

from __future__ import annotations

import hmac
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from mock_oss.auth import require
from mock_oss.context import get_db, require_customer
from mock_oss.errors import ApiError
from mock_oss.schemas import VerifyIn

# A scope for each thing that is read: finding and reading a profile, checking what a caller
# knows, and the one fact the executor needs before it sends a notice.
router = APIRouter(prefix="/crm/v1")
PROFILE = [Depends(require("crm:profile"))]
VERIFY = [Depends(require("crm:verify"))]
CONSENT = [Depends(require("crm:consent"))]
_PHONE = re.compile(r"^(?:\+91)?([6-9]\d{9})$")

CONSENT_SQL = (
    "SELECT COALESCE(o.outage_notifications, c.consent) AS consent, "
    "COALESCE(o.channel, c.consent_channel) AS channel, "
    "COALESCE(o.updated_at, c.consent_updated_at) AS updated_at "
    "FROM customers c LEFT JOIN consent_overrides o ON o.customer_id = c.customer_id "
    "WHERE c.customer_id = ?"
)


def mask_name(full_name: str) -> str:
    return " ".join(word[0] + "*" * (len(word) - 1) for word in full_name.split())


def mask_phone(phone: str) -> str:
    return phone[:3] + "*" * (len(phone) - 7) + phone[-4:]


def consent_of(conn: sqlite3.Connection, customer_id: str) -> dict[str, Any]:
    row = conn.execute(CONSENT_SQL, (customer_id,)).fetchone()
    if row is None:
        raise ApiError(404, "CUSTOMER_NOT_FOUND", f"customer {customer_id} not found")
    return {
        "outage_notifications": bool(row["consent"]),
        "channel": row["channel"],
        "updated_at": row["updated_at"],
    }


def _lookup_rows(conn: sqlite3.Connection, field: str, value: str) -> list[sqlite3.Row]:
    if field == "phone":
        match = _PHONE.match(value)
        if match is None:
            raise ApiError(400, "INVALID_LOOKUP", "phone must be +91XXXXXXXXXX or 10 digits")
        sql, arg = "SELECT * FROM customers WHERE phone = ?", f"+91{match.group(1)}"
    elif field == "email":
        sql, arg = "SELECT * FROM customers WHERE email_lower = ?", value.lower()
    else:
        sql, arg = "SELECT * FROM customers WHERE customer_id = ?", value
    return list(conn.execute(sql + " ORDER BY customer_id", (arg,)).fetchall())


@router.get("/customers/lookup", dependencies=PROFILE)
def lookup(
    request: Request, phone: str = "", customer_id: str = "", email: str = ""
) -> dict[str, Any]:
    given = [
        (f, v.strip())
        for f, v in (("phone", phone), ("customer_id", customer_id), ("email", email))
        if v.strip()
    ]
    if len(given) != 1:
        raise ApiError(400, "INVALID_LOOKUP", "exactly one of phone, customer_id, email")
    with get_db(request).read() as conn:
        rows = _lookup_rows(conn, *given[0])
    return {
        "matches": [
            {
                "customer_id": r["customer_id"],
                "name_masked": mask_name(r["full_name"]),
                "phone_masked": mask_phone(r["phone"]),
                "status": r["status"],
            }
            for r in rows
        ]
    }


def _service_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "service_id": row["service_id"],
        "service_type": row["service_type"],
        "plan_name": row["plan_name"],
        "monthly_fee_inr": row["monthly_fee"],
        "status": row["status"],
        "address": {
            "address_id": row["address_id"],
            "area": row["area"],
            "city": row["city"],
            "pincode": row["pincode"],
        },
        "router_model": row["router_model"],
        "activated_on": row["activated_on"],
        "billing_cycle_day": row["billing_cycle_day"],
    }


@router.get("/customers/{customer_id}", dependencies=PROFILE)
def profile(customer_id: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        customer = require_customer(conn, customer_id)
        services = conn.execute(
            "SELECT s.*, a.area, a.city, a.pincode FROM services s "
            "LEFT JOIN addresses a ON a.address_id = s.address_id "
            "WHERE s.customer_id = ? ORDER BY s.service_id",
            (customer_id,),
        ).fetchall()
        consent = consent_of(conn, customer_id)
    return {
        "customer_id": customer_id,
        "full_name": customer["full_name"],
        "phone": customer["phone"],
        "email": customer["email"],
        "segment": customer["segment"],
        "status": customer["status"],
        "consent": consent,
        "services": [_service_view(s) for s in services],
    }


@router.get("/customers/{customer_id}/consent", dependencies=CONSENT)
def consent(customer_id: str, request: Request) -> dict[str, Any]:
    with get_db(request).read() as conn:
        return consent_of(conn, customer_id)


@router.post("/customers/{customer_id}/verify", dependencies=VERIFY)
def verify(customer_id: str, body: VerifyIn, request: Request) -> dict[str, bool]:
    with get_db(request).read() as conn:
        customer = require_customer(conn, customer_id)
    expected = str(customer["phone"])[-4:].encode()
    return {"verified": hmac.compare_digest(expected, body.phone_last4.encode())}
