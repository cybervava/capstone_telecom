"""Fixture finder over ground truth: ``GET /admin/v1/customers/search``."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from mock_oss.auth import require
from mock_oss.context import get_db
from mock_oss.schemas import ServiceType

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(require("admin"))])

# Every filter is a fixed "(:x IS NULL OR column = :x)" clause; values are bound parameters.
SEARCH_SQL = """
SELECT s.service_id, s.customer_id, s.address_id, s.service_type, s.router_model,
       s.monthly_fee, s.status AS service_status, t.true_node_id, t.mapping_defect, c.phone,
       COALESCE(o.outage_notifications, c.consent) AS consent
FROM services s
JOIN customers c ON c.customer_id = s.customer_id
LEFT JOIN truth t ON t.service_id = s.service_id
LEFT JOIN consent_overrides o ON o.customer_id = s.customer_id
WHERE (:customer_id IS NULL OR s.customer_id = :customer_id)
  AND (:node_id IS NULL OR t.true_node_id = :node_id)
  AND (:service_type IS NULL OR s.service_type = :service_type)
  AND (:router_model IS NULL OR s.router_model = :router_model)
  AND (:segment IS NULL OR c.segment = :segment)
  AND (:consent IS NULL OR COALESCE(o.outage_notifications, c.consent) = :consent)
  AND (:mapping_defect IS NULL OR t.mapping_defect = :mapping_defect)
  AND (:status IS NULL OR c.status = :status)
  AND (:service_status IS NULL OR s.status = :service_status)
  AND (:min_fee IS NULL OR s.monthly_fee_cents >= :min_fee)
  AND (:untouched = 0 OR (
        NOT EXISTS (SELECT 1 FROM appointments a WHERE a.service_id = s.service_id)
    AND NOT EXISTS (SELECT 1 FROM credits cr WHERE cr.customer_id = s.customer_id)
    AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.customer_id = s.customer_id)))
ORDER BY s.service_id
LIMIT :limit
"""


def _mapped(conn: sqlite3.Connection, address_id: str, service_type: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT node_id FROM address_node_map WHERE address_id = ? AND service_type = ? "
        "ORDER BY node_id",
        (address_id, service_type),
    ).fetchall()
    return [str(r["node_id"]) for r in rows]


def _row_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "customer_id": row["customer_id"],
        "service_id": row["service_id"],
        "true_node_id": row["true_node_id"],
        "mapped_node_ids": _mapped(conn, row["address_id"], row["service_type"]),
        "router_model": row["router_model"],
        "monthly_fee_usd": row["monthly_fee"],
        "service_status": row["service_status"],
        "consent": bool(row["consent"]),
        "mapping_defect": row["mapping_defect"],
        "phone": row["phone"],
    }


@router.get("/customers/search")
def search(
    request: Request,
    customer_id: Annotated[str | None, Query(min_length=1)] = None,
    node_id: Annotated[str | None, Query(min_length=1)] = None,
    service_type: Annotated[ServiceType | None, Query()] = None,
    router_model: Annotated[str | None, Query(min_length=1)] = None,
    segment: Annotated[Literal["consumer", "sme"] | None, Query()] = None,
    consent: Annotated[Literal["true", "false"] | None, Query()] = None,
    mapping_defect: Annotated[
        Literal["none", "missing", "ambiguous", "stale"] | None, Query()
    ] = None,
    status: Annotated[Literal["active", "suspended"] | None, Query()] = None,
    # The line's own status: an active customer can have a suspended line.
    service_status: Annotated[Literal["active", "suspended"] | None, Query()] = None,
    min_monthly_fee: Annotated[Decimal | None, Query(ge=0)] = None,
    # Only customers the platform has written nothing for yet: no notice, credit or visit. A
    # scene played twice then gets a fresh customer, whatever was rehearsed before.
    untouched: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    params = {
        "customer_id": customer_id,
        "node_id": node_id,
        "service_type": service_type,
        "router_model": router_model,
        "segment": segment,
        "consent": None if consent is None else int(consent == "true"),
        "mapping_defect": mapping_defect,
        "status": status,
        "service_status": service_status,
        "min_fee": None if min_monthly_fee is None else int(min_monthly_fee * 100),
        "untouched": int(untouched),
        "limit": limit,
    }
    with get_db(request).read() as conn:
        rows = conn.execute(SEARCH_SQL, params).fetchall()
        return {"customers": [_row_view(conn, r) for r in rows]}
