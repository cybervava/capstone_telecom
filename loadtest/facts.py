"""What the platform itself recorded about the contacts of a run.

The callers' figures say what a client saw. These say what happened behind the API: whether
every contact was answered, how long the first reply took for all of them (not only for those
who stayed on the page), where contacts ended, and what was said about the network. And when
the outage ends: who was told, where those not told stand, and what the platform made of the
lines it tested meanwhile.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from skylink_care.db.database import Database
from skylink_care.guardrails.claims import CLAIMS, Claim

# Templates that say the network is clear, or arrange a visit, which needs it to be.
CLAIMS_CLEAR = sorted(
    template for template, claim in CLAIMS.items() if claim in {Claim.CLEAR, Claim.VISIT}
)
RESTORED_TEMPLATE = "outage_restored.v1"
UNVERIFIED_TEMPLATE = "network_unknown.v1"
_IN_WINDOW = "c.received_at >= %(start)s AND c.received_at < %(end)s"
_FIRST_REPLY = f"""
    SELECT c.contact_id, c.received_at, first.created_at AS replied_at,
           first.meta ->> 'template_id' AS template_id
      FROM contacts c
      LEFT JOIN LATERAL (
            SELECT m.created_at, m.meta FROM contact_messages m
             WHERE m.contact_id = c.contact_id AND m.direction = 'outbound'
             ORDER BY m.message_id LIMIT 1) first ON true
     WHERE {_IN_WINDOW}
"""
_REPLY_TIMES = f"""
    SELECT count(*) AS contacts, count(replied_at) AS replied,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY waited) AS p50,
           percentile_disc(0.95) WITHIN GROUP (ORDER BY waited) AS p95,
           percentile_disc(0.99) WITHIN GROUP (ORDER BY waited) AS p99,
           max(waited) AS max
      FROM (SELECT replied_at, extract(epoch FROM replied_at - received_at) AS waited
              FROM ({_FIRST_REPLY}) answered) timed
"""
_REFERENCE_TIMES = f"""
    SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY reference_latency_ms) AS p50,
           percentile_disc(0.99) WITHIN GROUP (ORDER BY reference_latency_ms) AS p99,
           max(reference_latency_ms) AS max
      FROM contacts c WHERE {_IN_WINDOW}
"""
# Each gives rows of (name, n); the grouping and the order are added where they are run.
_COUNTS = {
    "by_status": f"SELECT c.status AS name, count(*) AS n FROM contacts c WHERE {_IN_WINDOW}",
    "first_reply": (
        "SELECT coalesce(template_id, '(none yet)') AS name, count(*) AS n"
        f"  FROM ({_FIRST_REPLY}) answered"
    ),
    "handoffs": (
        "SELECT h.reason AS name, count(*) AS n FROM handoffs h"
        f"  JOIN contacts c USING (contact_id) WHERE {_IN_WINDOW}"
    ),
    "read_by": (
        "SELECT coalesce(a.model, '?') || CASE WHEN a.outcome = 'DEGRADED' THEN"
        "         ' (without the model: ' || coalesce(a.inputs ->> 'provider_error', '?')"
        "         || ')' ELSE '' END AS name, count(*) AS n"
        "  FROM audit_log a JOIN contacts c USING (contact_id)"
        f" WHERE a.step = 'triage.read_contact' AND {_IN_WINDOW}"
    ),
    "writes": (
        "SELECT a.action_type || ' ' || a.status AS name, count(*) AS n FROM actions a"
        f"  JOIN contacts c USING (contact_id) WHERE {_IN_WINDOW}"
    ),
    # Proactive notices belong to no contact; they are found by the outage they are about.
    "notifications": (
        "SELECT status AS name, count(*) AS n FROM actions"
        " WHERE action_type = 'NOTIFY_SEND' AND request_body ->> 'outage_id' = ANY(%(outages)s)"
    ),
    "ops_items": (
        "SELECT kind AS name, count(*) AS n FROM ops_items"
        " WHERE last_seen_at >= %(start)s AND created_at < %(end)s"
    ),
}
_DEAD_LETTERED = (
    "SELECT count(DISTINCT i.contact_id) AS n FROM inbox i"
    f"  JOIN contacts c USING (contact_id) WHERE i.dead AND {_IN_WINDOW}"
)
_SAID_CLEAR = (
    "SELECT count(*) AS n FROM contact_messages m JOIN contacts c USING (contact_id)"
    " WHERE m.direction = 'outbound' AND m.meta ->> 'template_id' = ANY(%(clear)s)"
    f"  AND m.created_at < %(end)s AND {_IN_WINDOW}"
)
_WAITING_ON_AN_OUTAGE = (
    "SELECT c.contact_id FROM contacts c"
    f" WHERE c.outage_id IS NOT NULL AND c.status = 'AWAITING_NETWORK' AND {_IN_WINDOW}"
)
_TOLD_RESTORED = (
    "SELECT count(DISTINCT m.contact_id) AS n FROM contact_messages m"
    " WHERE m.contact_id = ANY(%(waiting)s) AND m.meta ->> 'template_id' = %(restored)s"
)
# Those who waited and were not told that it is over, by the last thing they were told.
_NOT_TOLD_STAND_AS = """
    SELECT coalesce(last.template_id, '(nothing)') AS name, count(*) AS n
      FROM unnest(%(waiting)s::uuid[]) AS w(contact_id)
      LEFT JOIN LATERAL (
            SELECT m.meta ->> 'template_id' AS template_id FROM contact_messages m
             WHERE m.contact_id = w.contact_id AND m.direction = 'outbound'
             ORDER BY m.message_id DESC LIMIT 1) last ON true
     WHERE NOT EXISTS (
            SELECT 1 FROM contact_messages m WHERE m.contact_id = w.contact_id
               AND m.meta ->> 'template_id' = %(restored)s)
"""
# Those of them told since then that the status cannot be verified, whatever they heard later:
# the platform doubted a node that had just been restored, or could not read it.
_TOLD_UNVERIFIED = """
    SELECT count(DISTINCT m.contact_id) AS n FROM contact_messages m
     WHERE m.contact_id = ANY(%(waiting)s) AND m.meta ->> 'template_id' = %(unverified)s
       AND m.created_at >= %(since)s
"""
# Their lines by the latest test the platform ran for them since then.
_LINES_TESTED = """
    SELECT count(*) AS tested, count(*) FILTER (WHERE result = 'FAIL') AS failing
      FROM (SELECT DISTINCT ON (t.service_id) t.result FROM line_tests t
             WHERE t.contact_id = ANY(%(waiting)s) AND t.tested_at >= %(since)s
             ORDER BY t.service_id, t.tested_at DESC, t.test_id DESC) latest
"""
# Tickets the platform raised since then for a fault it suspects on a node, on the strength of
# these contacts. The nodes had just been restored: in the simulator nothing is wrong with them.
_SUSPECTED_TICKETS = """
    SELECT count(*) AS n FROM actions opened
     WHERE opened.action_type = 'INCIDENT_OPEN'
       AND opened.request_body ->> 'kind' = 'SUSPECTED_NODE_FAULT'
       AND opened.created_at >= %(since)s
       AND opened.episode_id IN (
            SELECT linked.episode_id FROM actions linked
             WHERE linked.action_type = 'INCIDENT_LINK_COMPLAINT'
               AND linked.contact_id = ANY(%(waiting)s))
"""
# The steps of a turn that took the most time in all: where the time of a reply goes.
_TURN_STEPS = (
    "SELECT a.step, count(*) AS calls,"
    "       percentile_disc(0.5) WITHIN GROUP (ORDER BY a.duration_ms) AS p50_ms,"
    "       percentile_disc(0.95) WITHIN GROUP (ORDER BY a.duration_ms) AS p95_ms,"
    "       max(a.duration_ms) AS max_ms"
    "  FROM audit_log a JOIN contacts c USING (contact_id)"
    f" WHERE a.duration_ms IS NOT NULL AND {_IN_WINDOW}"
    " GROUP BY a.step ORDER BY sum(a.duration_ms) DESC LIMIT 8"
)


async def read_facts(
    db: Database, start: datetime, end: datetime, outages: Sequence[str] = ()
) -> dict[str, Any]:
    """The platform's own record of the contacts received from ``start`` up to ``end``, and of
    the notices about these outages."""
    window: dict[str, Any] = {"start": start, "end": end, "outages": list(outages)}
    replies = await _one(db, _REPLY_TIMES, window)
    reference = await _one(db, _REFERENCE_TIMES, window)
    facts: dict[str, Any] = {
        "contacts": int(replies["contacts"]),
        "replied": int(replies["replied"]),
        "first_reply_seconds": {k: _number(replies[k]) for k in ("p50", "p95", "p99", "max")},
        "reference_ms_at_the_server": {k: _number(reference[k]) for k in ("p50", "p99", "max")},
    }
    for name, sql in _COUNTS.items():
        facts[name] = await _counts(db, sql, window)
    facts["dead_lettered"] = await _count(db, _DEAD_LETTERED, window)
    facts["said_clear"] = await _count(db, _SAID_CLEAR, {**window, "clear": CLAIMS_CLEAR})
    async with db.connection() as conn:
        cursor = await conn.execute(_TURN_STEPS, window)
        facts["turn_steps"] = [dict(row) for row in await cursor.fetchall()]
    return facts


async def waiting_on_an_outage(db: Database, start: datetime, end: datetime) -> list[UUID]:
    """Contacts of the window that were told of an outage and wait to hear of its end."""
    async with db.connection() as conn:
        cursor = await conn.execute(_WAITING_ON_AN_OUTAGE, {"start": start, "end": end})
        return [row["contact_id"] for row in await cursor.fetchall()]


async def database_time(db: Database) -> datetime:
    """The database's own clock: what the timestamps it sets are compared with."""
    stamped: datetime = (await _one(db, "SELECT clock_timestamp() AS now", {}))["now"]
    return stamped


async def told_of_the_restoration(db: Database, waiting: Sequence[UUID]) -> int:
    """How many of those who waited have been told that the outage is over."""
    params = {"waiting": list(waiting), "restored": RESTORED_TEMPLATE}
    return await _count(db, _TOLD_RESTORED, params)


async def after_the_wave(db: Database, waiting: Sequence[UUID], since: datetime) -> dict[str, Any]:
    """What the end of the outage left for those who waited on it: where the ones not told
    stand, what the tests of their lines found, and whether the platform came to suspect a
    node again and raised a ticket for it (``since``: the database's time at the outage's end).
    """
    params = {
        "waiting": list(waiting),
        "restored": RESTORED_TEMPLATE,
        "unverified": UNVERIFIED_TEMPLATE,
        "since": since,
    }
    lines = await _one(db, _LINES_TESTED, params)
    return {
        "not_told_stand_as": await _counts(db, _NOT_TOLD_STAND_AS, params),
        "told_unverified_meanwhile": await _count(db, _TOLD_UNVERIFIED, params),
        "lines_tested": int(lines.get("tested") or 0),
        "lines_failing": int(lines.get("failing") or 0),
        "suspected_tickets": await _count(db, _SUSPECTED_TICKETS, params),
    }


async def _one(db: Database, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    async with db.connection() as conn:
        row = await (await conn.execute(sql, params)).fetchone()
    return dict(row) if row else {}


async def _count(db: Database, sql: str, params: dict[str, Any]) -> int:
    return int((await _one(db, sql, params)).get("n") or 0)


async def _counts(db: Database, sql: str, params: dict[str, Any]) -> dict[str, int]:
    async with db.connection() as conn:
        cursor = await conn.execute(f"{sql} GROUP BY 1 ORDER BY 2 DESC, 1", params)
        return {str(row["name"]): int(row["n"]) for row in await cursor.fetchall()}


def _number(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)
