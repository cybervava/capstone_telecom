"""Intake ledger: accepting a contact and issuing its complaint reference.

``accept`` is the reference-first transaction. It touches only this database, so it keeps
working when every other dependency is down, and it is idempotent on the channel's request id.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from skylink_care.db.actions import COMPLAINT_REGISTER, REGULATORY_LANE
from skylink_care.db.base import Repository
from skylink_care.db.database import Connection
from skylink_care.domain.models import Channel
from skylink_care.domain.reference import format_reference
from skylink_care.hashing import canonical_hash

FIRST_MESSAGE_KEY = "inbound:first"
RECEIVED_EVENT = "contact.received"


class ContactBoundError(Exception):
    """A contact that belongs to one customer was to be given to another."""


class RequestIdReusedError(Exception):
    """The channel reused a request id for a different request."""

    def __init__(self, complaint_ref: str) -> None:
        super().__init__("client_request_id was already used for a different request")
        self.complaint_ref = complaint_ref


@dataclass(frozen=True)
class NewContact:
    channel: Channel
    client_request_id: str
    text: str
    correlation_id: str
    received_at: datetime
    session_id: str | None = None
    authenticated_customer_id: str | None = None
    caller_phone: str | None = None
    # The API caller that opened the contact (token subject). Not part of the request identity.
    created_by: str | None = None

    def request_hash(self) -> str:
        return canonical_hash(
            {
                "channel": self.channel.value,
                "text": self.text,
                "session_id": self.session_id,
                "authenticated_customer_id": self.authenticated_customer_id,
                "caller_phone": self.caller_phone,
            }
        )


@dataclass(frozen=True)
class IntakeResult:
    contact_id: UUID
    complaint_ref: str
    status: str
    received_at: datetime
    reference_issued_at: datetime
    replayed: bool


@dataclass(frozen=True)
class Contact:
    contact_id: UUID
    channel: Channel
    complaint_ref: str
    status: str
    correlation_id: str
    received_at: datetime
    reference_issued_at: datetime
    reference_latency_ms: int
    session_id: str | None
    authenticated_customer_id: str | None
    caller_phone: str | None
    customer_id: str | None
    service_id: str | None
    service_type: str | None
    node_id: str | None
    intent: str | None
    network_state: str | None
    outage_id: str | None
    episode_id: UUID | None
    closed_at: datetime | None
    created_by: str | None
    # When the network state above was decided. Without it the state is of no known age.
    network_checked_at: datetime | None = None


_CONTACT_COLUMNS = (
    "contact_id, channel, complaint_ref, status, correlation_id, received_at,"
    " reference_issued_at, reference_latency_ms, session_id, authenticated_customer_id,"
    " caller_phone, customer_id, service_id, service_type, node_id, intent, network_state,"
    " outage_id, episode_id, closed_at, created_by, network_checked_at"
)

# Workflow-owned columns that ``update`` may set. Anything else is rejected.
_UPDATABLE = frozenset(
    {
        "status",
        "customer_id",
        "service_id",
        "service_type",
        "node_id",
        "intent",
        "network_state",
        "network_checked_at",
        "outage_id",
        "episode_id",
        "closed_at",
    }
)


class ContactRepository(Repository):
    async def accept(self, new: NewContact, *, started: float | None = None) -> IntakeResult:
        """Store the contact and issue its reference, or return the one issued earlier.

        ``started`` is a ``time.monotonic()`` reading taken when the request arrived. The stored
        latency runs from there to the moment the reference row is written; the commit that
        follows is not included, so the end-to-end figure is measured by the caller.
        """
        began = started if started is not None else time.monotonic()
        request_hash = new.request_hash()
        async with self._db.transaction() as conn:
            existing = await _find_by_request(conn, new)
            if existing is None:
                inserted = await _insert_contact(conn, new, request_hash, began)
                if inserted is not None:
                    await _insert_first_message_and_wakeup(conn, inserted["contact_id"], new)
                    await _propose_registration(conn, inserted, new)
                    return _intake_result(inserted, replayed=False)
                # Lost a race with an identical request: fall through to the stored row.
                existing = await _find_by_request(conn, new)
            if existing is None:  # pragma: no cover - only if the row vanished mid-transaction
                raise RuntimeError("contact disappeared during intake")
            if existing["request_hash"] != request_hash:
                raise RequestIdReusedError(existing["complaint_ref"])
            return _intake_result(existing, replayed=True)

    async def get(self, contact_id: UUID, *, conn: Connection | None = None) -> Contact | None:
        async with self._conn(conn) as active:
            cursor = await active.execute(
                f"SELECT {_CONTACT_COLUMNS} FROM contacts WHERE contact_id = %s",  # noqa: S608
                (contact_id,),
            )
            row = await cursor.fetchone()
        return _to_contact(row) if row else None

    async def get_by_reference(self, complaint_ref: str) -> Contact | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_CONTACT_COLUMNS} FROM contacts WHERE complaint_ref = %s",  # noqa: S608
                (complaint_ref,),
            )
            row = await cursor.fetchone()
        return _to_contact(row) if row else None

    async def update(
        self, contact_id: UUID, *, conn: Connection | None = None, **changes: Any
    ) -> None:
        """Set workflow-owned columns. Column names are checked against a fixed allow-list.

        The customer of a contact is set once. Setting the same one again changes nothing;
        another one is refused with ``ContactBoundError`` and nothing is written; no
        customer (``None`` or an empty ID) attaches nobody and detaches nobody.
        """
        unknown = set(changes) - _UPDATABLE
        if unknown:
            raise ValueError(f"columns not updatable: {sorted(unknown)}")
        customer_id = changes.get("customer_id")
        if not customer_id:
            changes.pop("customer_id", None)
        if not changes:
            return
        assignments = ", ".join(f"{column} = %s" for column in changes)
        unbound_or_theirs = " AND (customer_id IS NULL OR customer_id = %s)" if customer_id else ""
        values = (*changes.values(), contact_id, *([customer_id] if customer_id else []))
        async with self._conn(conn) as active:
            cursor = await active.execute(
                f"UPDATE contacts SET {assignments}, updated_at = now()"  # noqa: S608
                f" WHERE contact_id = %s{unbound_or_theirs}",
                values,
            )
        if customer_id and cursor.rowcount == 0:
            raise ContactBoundError(
                f"contact {contact_id} belongs to another customer, or does not exist"
            )

    async def waiting_on_nodes(
        self,
        conn: Connection,
        node_ids: Sequence[str],
        *,
        statuses: Sequence[str],
        not_told_about: str | None = None,
    ) -> list[UUID]:
        """Open contacts on any of these nodes that are in one of the given statuses.

        ``not_told_about`` leaves out contacts already attached to that outage: they heard about
        it from the live status before the feed event arrived.
        """
        cursor = await conn.execute(
            "SELECT contact_id FROM contacts WHERE node_id = ANY(%s) AND status = ANY(%s)"
            " AND closed_at IS NULL AND (%s::text IS NULL OR outage_id IS DISTINCT FROM %s)"
            # Id order, the same order in which the timer pump locks contacts.
            " ORDER BY contact_id",
            (list(node_ids), list(statuses), not_told_about, not_told_about),
        )
        return [row["contact_id"] for row in await cursor.fetchall()]

    async def count_recent_for_customer(self, customer_id: str, *, since: datetime) -> int:
        """How many contacts this customer has made since ``since`` (the repeat-contact signal)."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS n FROM contacts WHERE customer_id = %s AND received_at >= %s",
                (customer_id, since),
            )
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0


async def _find_by_request(conn: Connection, new: NewContact) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "SELECT contact_id, complaint_ref, status, received_at, reference_issued_at, request_hash"
        " FROM contacts WHERE channel = %s AND client_request_id = %s",
        (new.channel.value, new.client_request_id),
    )
    return await cursor.fetchone()


async def _insert_contact(
    conn: Connection, new: NewContact, request_hash: str, began: float
) -> dict[str, Any] | None:
    cursor = await conn.execute("SELECT nextval('complaint_ref_seq') AS seq")
    row = await cursor.fetchone()
    if row is None:  # pragma: no cover - nextval always returns a row
        raise RuntimeError("complaint_ref_seq returned nothing")
    sequence = int(row["seq"])
    latency_ms = max(int((time.monotonic() - began) * 1000), 0)
    cursor = await conn.execute(
        "INSERT INTO contacts (contact_id, channel, client_request_id, request_hash,"
        " complaint_ref, ref_seq, session_id, authenticated_customer_id, caller_phone,"
        " correlation_id, received_at, reference_latency_ms, created_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT ON CONSTRAINT contacts_request_unique DO NOTHING"
        " RETURNING contact_id, complaint_ref, status, received_at, reference_issued_at,"
        " request_hash",
        (
            uuid4(),
            new.channel.value,
            new.client_request_id,
            request_hash,
            format_reference(sequence, new.received_at),
            sequence,
            new.session_id,
            new.authenticated_customer_id,
            new.caller_phone,
            new.correlation_id,
            new.received_at,
            latency_ms,
            new.created_by,
        ),
    )
    return await cursor.fetchone()


async def _insert_first_message_and_wakeup(
    conn: Connection, contact_id: UUID, new: NewContact
) -> None:
    await conn.execute(
        "INSERT INTO contact_messages (contact_id, direction, author, body, dedupe_key)"
        " VALUES (%s, 'inbound', 'customer', %s, %s)",
        (contact_id, new.text, FIRST_MESSAGE_KEY),
    )
    await conn.execute(
        "INSERT INTO inbox (contact_id, event_type, dedupe_key) VALUES (%s, %s, %s)",
        (contact_id, RECEIVED_EVENT, RECEIVED_EVENT),
    )


async def _propose_registration(conn: Connection, contact: dict[str, Any], new: NewContact) -> None:
    """Queue registration of the reference with Incident Management.

    The body is frozen with what is known at intake. It is never rebuilt once triage learns more,
    because a changed body under the same key would be rejected as a conflict.
    """
    body = {
        "complaint_ref": contact["complaint_ref"],
        "received_at": new.received_at.isoformat(),
        "channel": new.channel.value,
        "category": "UNCLASSIFIED",
        "customer_id": None,
    }
    await conn.execute(
        "INSERT INTO actions (action_id, action_key, action_type, lane, contact_id, requested_by,"
        " request_body, request_hash, status, correlation_id)"
        " VALUES (%s, %s, %s, %s, %s, 'system:intake', %s, %s, 'READY', %s)",
        (
            uuid4(),
            f"complaint:{contact['complaint_ref']}",
            COMPLAINT_REGISTER,
            REGULATORY_LANE,
            contact["contact_id"],
            Jsonb(body),
            canonical_hash(body),
            new.correlation_id,
        ),
    )


def _intake_result(row: dict[str, Any], *, replayed: bool) -> IntakeResult:
    return IntakeResult(
        contact_id=row["contact_id"],
        complaint_ref=row["complaint_ref"],
        status=row["status"],
        received_at=row["received_at"],
        reference_issued_at=row["reference_issued_at"],
        replayed=replayed,
    )


def _to_contact(row: dict[str, Any]) -> Contact:
    return Contact(**{**row, "channel": Channel(row["channel"])})
