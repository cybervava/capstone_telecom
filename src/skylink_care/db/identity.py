"""Tries at the identity check, counted for each customer ID that was claimed.

A customer who is not signed in proves who they are with a customer ID and the last four digits
of the registered mobile. One conversation allows three tries. This is what counts them across
conversations, so that four digits cannot be guessed by opening enough of them.

A try is taken before the answer is checked and given back when it was right, so that the count
and the check cannot be raced: of any number of conversations answering at once, only as many
as there are tries left reach the CRM.

Two numbers limit the wrong answers for one customer ID: a few within a short window, which
stops guessing in a hurry, and a few more within a day, which stops guessing that keeps just
under the first.

Times are the database's own, written and compared there: what is counted is how recent a row
is, and one clock must say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection

DAY_SECONDS = 86_400
# Rows too old to count, anyone's, removed whenever a try is taken. The table then holds what
# a day holds and no more, however many customer IDs were tried once and never again.
SWEEP_ROWS = 200
# How many of the conversations a locked ID was tried in are named to staff.
CONTACTS_SHOWN = 5
_SINCE = "attempted_at >= statement_timestamp() - make_interval(secs => %s)"


@dataclass(frozen=True)
class Limits:
    """How many wrong answers one customer ID may have before no further one is checked."""

    per_window: int
    window_seconds: int
    per_day: int

    def reached(self, seen: RecentTries) -> bool:
        return seen.in_window >= self.per_window or seen.tries >= self.per_day


@dataclass(frozen=True)
class RecentTries:
    """The tries counted for one customer ID within the last day."""

    tries: int
    # Of those, the ones within the short window.
    in_window: int
    conversations: int
    # The conversations they were made in, the latest first, at most ``CONTACTS_SHOWN``.
    contacts: tuple[UUID, ...]
    first_at: datetime | None
    last_at: datetime | None
    # The CRM knew the ID at least once: it is somebody's account.
    account_known: bool


class IdentityAttemptRepository(Repository):
    async def take(self, customer_id: str, contact_id: UUID, limits: Limits) -> int | None:
        """Take one of the tries this ID has left. None when none is left.

        Counting and taking are one step under a lock on the ID, held until the transaction
        ends: two conversations cannot both take the last try.
        """
        async with self._db.transaction() as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"identity-check:{customer_id}",),
            )
            await conn.execute(
                "DELETE FROM identity_attempts WHERE attempt_id IN ("
                " SELECT attempt_id FROM identity_attempts"
                " WHERE attempted_at < statement_timestamp() - make_interval(secs => %s)"
                " ORDER BY attempted_at LIMIT %s FOR UPDATE SKIP LOCKED)",
                (DAY_SECONDS, SWEEP_ROWS),
            )
            seen = await self._counts(conn, customer_id, limits)
            if seen["in_window"] >= limits.per_window or seen["tries"] >= limits.per_day:
                return None
            cursor = await conn.execute(
                "INSERT INTO identity_attempts (customer_id, contact_id) VALUES (%s, %s)"
                " RETURNING attempt_id",
                (customer_id, contact_id),
            )
            taken = await cursor.fetchone()
        if taken is None:  # an INSERT that returns nothing has not happened
            raise RuntimeError("a try at the identity check could not be recorded")
        return int(taken["attempt_id"])

    async def give_back(self, attempt_id: int) -> None:
        """The answer was right, or the CRM could not check it: the try is not counted."""
        async with self._db.connection() as conn:
            await conn.execute("DELETE FROM identity_attempts WHERE attempt_id = %s", (attempt_id,))

    async def mark_wrong(self, attempt_id: int, *, account_known: bool) -> None:
        """The answer was wrong: the try stays counted, with what the CRM said of the ID."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE identity_attempts SET account_known = %s WHERE attempt_id = %s",
                (account_known, attempt_id),
            )

    async def recent(self, customer_id: str, limits: Limits) -> RecentTries:
        """What is counted for this ID within the last day, by whoever tried."""
        async with self._db.connection() as conn:
            totals = await self._counts(conn, customer_id, limits)
            cursor = await conn.execute(
                "SELECT contact_id FROM identity_attempts"  # noqa: S608
                f" WHERE customer_id = %s AND {_SINCE}"
                " GROUP BY contact_id ORDER BY max(attempted_at) DESC LIMIT %s",
                (customer_id, DAY_SECONDS, CONTACTS_SHOWN),
            )
            contacts = tuple(row["contact_id"] for row in await cursor.fetchall())
        return RecentTries(
            tries=int(totals["tries"]),
            in_window=int(totals["in_window"]),
            conversations=int(totals["conversations"]),
            contacts=contacts,
            first_at=totals["first_at"],
            last_at=totals["last_at"],
            account_known=bool(totals["account_known"]),
        )

    async def customers_answered_wrongly(self, *, window_seconds: int) -> int:
        """For how many different customers a wrong answer was given within the window.

        One wrong answer each is under every limit for one customer ID. Many customers at
        once is what the same digits tried on one customer ID after another looks like.
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(DISTINCT customer_id) AS customers FROM identity_attempts"  # noqa: S608
                f" WHERE account_known AND {_SINCE}",
                (window_seconds,),
            )
            row = await cursor.fetchone()
        return int(row["customers"]) if row else 0

    @staticmethod
    async def _counts(conn: Connection, customer_id: str, limits: Limits) -> dict[str, Any]:
        cursor = await conn.execute(
            "SELECT count(*) AS tries,"  # noqa: S608
            " count(*) FILTER (WHERE attempted_at >= statement_timestamp()"
            "   - make_interval(secs => %s)) AS in_window,"
            " count(DISTINCT contact_id) AS conversations,"
            " min(attempted_at) AS first_at, max(attempted_at) AS last_at,"
            " coalesce(bool_or(account_known), false) AS account_known"
            f" FROM identity_attempts WHERE customer_id = %s AND {_SINCE}",
            (limits.window_seconds, customer_id, DAY_SECONDS),
        )
        row = await cursor.fetchone()
        if row is None:  # an aggregate without GROUP BY always returns its one row
            raise RuntimeError("the tries at the identity check could not be counted")
        return dict(row)
