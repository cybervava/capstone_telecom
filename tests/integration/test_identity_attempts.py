"""Tries at the identity check, counted for each claimed customer ID, and what staff hear."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from skylink_care.adapters.clients import CrmReader
from skylink_care.adapters.result import Err, ErrorKind
from skylink_care.agents.identity_guard import (
    ACROSS_CUSTOMERS,
    IDENTITY_ATTEMPTS,
    MANY_CUSTOMERS,
    MAX_CUSTOMER_ITEMS,
    IdentityGuard,
)
from skylink_care.agents.triage import Claim, IdentityStatus, TriageAgent
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactBoundError, ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.identity import (
    CONTACTS_SHOWN,
    DAY_SECONDS,
    SWEEP_ROWS,
    IdentityAttemptRepository,
    Limits,
)
from skylink_care.db.ops import OpsRepository
from skylink_care.domain.models import Channel
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.offline import OfflineProvider
from skylink_care.observability.audit import Auditor

HOUR = 3600
LIMITS = Limits(per_window=5, window_seconds=HOUR, per_day=10)
ROOMY = Limits(per_window=100, window_seconds=HOUR, per_day=100)


async def a_contact(db: Database) -> UUID:
    intake = await ContactRepository(db).accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="internet down",
            correlation_id=uuid4().hex,
            received_at=datetime.now(UTC),
        )
    )
    return intake.contact_id


async def aged(db: Database, seconds: int, customer_id: str | None = None) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE identity_attempts SET attempted_at = attempted_at - make_interval(secs => %s)"
            " WHERE %s::text IS NULL OR customer_id = %s",
            (seconds, customer_id, customer_id),
        )
        await conn.execute(
            "UPDATE ops_items SET last_seen_at = last_seen_at - make_interval(secs => %s)",
            (seconds,),
        )


async def rows(db: Database) -> dict[str, int]:
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT customer_id, count(*) AS rows FROM identity_attempts GROUP BY 1 ORDER BY 1"
        )
        return {row["customer_id"]: row["rows"] for row in await cursor.fetchall()}


async def wrong(
    attempts: IdentityAttemptRepository,
    customer_id: str,
    contact_id: UUID,
    *,
    known: bool = True,
    limits: Limits = LIMITS,
) -> None:
    attempt = await attempts.take(customer_id, contact_id, limits)
    assert attempt is not None
    await attempts.mark_wrong(attempt, account_known=known)


def guard_on(db: Database, *, spread: int = 50) -> IdentityGuard:
    return IdentityGuard(
        IdentityAttemptRepository(db), OpsRepository(db), LIMITS, spread_alert_customers=spread
    )


async def wrong_answer(guard: IdentityGuard, customer_id: str, contact_id: UUID) -> None:
    """One wrong answer for a customer the CRM knows, as the triage agent reports it."""
    attempt = await guard.take(customer_id, contact_id)
    assert attempt is not None
    await guard.wrong(attempt, customer_id, account_known=True)


# --- counting ------------------------------------------------------------------------------------


async def test_tries_are_counted_for_the_id_that_was_claimed_until_none_is_left(
    db: Database,
) -> None:
    attempts = IdentityAttemptRepository(db)
    contact_id = await a_contact(db)
    three = Limits(per_window=3, window_seconds=HOUR, per_day=10)

    taken = [await attempts.take("C0000001", contact_id, three) for _ in range(4)]
    another = await attempts.take("C0000002", contact_id, three)

    assert [attempt is not None for attempt in taken] == [True, True, True, False]
    assert another is not None
    assert await rows(db) == {"C0000001": 3, "C0000002": 1}


async def test_a_try_given_back_is_not_counted(db: Database) -> None:
    """The right answer, and an answer the CRM could not check, cost the customer nothing."""
    attempts = IdentityAttemptRepository(db)
    contact_id = await a_contact(db)

    for _ in range(LIMITS.per_day * 2):
        attempt = await attempts.take("C0000001", contact_id, LIMITS)
        assert attempt is not None
        await attempts.give_back(attempt)

    assert await rows(db) == {}
    assert (await attempts.recent("C0000001", LIMITS)).tries == 0


async def test_answers_given_at_the_same_moment_cannot_each_take_the_last_try(
    db: Database,
) -> None:
    """Counting and taking are one step. Checked first and recorded after, sixteen
    conversations answering at once would each have found a try left."""
    attempts = IdentityAttemptRepository(db)
    contact_id = await a_contact(db)

    taken = await asyncio.gather(
        *(attempts.take("C0000001", contact_id, LIMITS) for _ in range(16))
    )

    assert sum(attempt is not None for attempt in taken) == LIMITS.per_window
    assert await rows(db) == {"C0000001": LIMITS.per_window}


async def test_guessing_that_keeps_under_the_hourly_limit_is_stopped_by_the_daily_one(
    db: Database,
) -> None:
    """Four an hour would never be refused by five an hour. They are ten in under three hours."""
    attempts = IdentityAttemptRepository(db)
    contact_id = await a_contact(db)
    for _ in range(LIMITS.per_day // 4):
        for _ in range(4):
            await wrong(attempts, "C0000001", contact_id)
        await aged(db, HOUR + 1)
    for _ in range(LIMITS.per_day % 4):
        await wrong(attempts, "C0000001", contact_id)

    refused = await attempts.take("C0000001", contact_id, LIMITS)
    seen = await attempts.recent("C0000001", LIMITS)
    await aged(db, DAY_SECONDS)
    a_day_later = await attempts.take("C0000001", contact_id, LIMITS)

    assert refused is None
    assert (seen.tries, seen.in_window) == (LIMITS.per_day, LIMITS.per_day % 4)
    assert LIMITS.reached(seen)
    assert a_day_later is not None


async def test_a_try_older_than_a_day_is_forgotten_whoevers_it_was(db: Database) -> None:
    """A row is of use only while it counts. Whoever takes a try removes those that no longer
    do, so customer IDs tried once and never again do not pile up."""
    attempts = IdentityAttemptRepository(db)
    contact_id = await a_contact(db)
    for customer_id in ("C0000001", "C0000001", "C0000002", "C0000003"):
        await wrong(attempts, customer_id, contact_id)
    await aged(db, DAY_SECONDS + 1, "C0000001")
    await aged(db, DAY_SECONDS + 1, "C0000002")

    counted = (await attempts.recent("C0000001", LIMITS)).tries
    await wrong(attempts, "C0000001", contact_id)

    assert counted == 0
    assert await rows(db) == {"C0000001": 1, "C0000003": 1}


async def test_old_rows_are_removed_a_bounded_number_at_a_time_by_anyone_at_once(
    db: Database,
) -> None:
    attempts = IdentityAttemptRepository(db)
    contact_id = await a_contact(db)
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO identity_attempts (customer_id, contact_id, attempted_at)"
            " SELECT 'C' || lpad(n::text, 7, '0'), %s,"
            "        clock_timestamp() - make_interval(secs => %s)"
            " FROM generate_series(1, %s) AS n",
            (contact_id, DAY_SECONDS * 2, SWEEP_ROWS * 3 + 50),
        )

    # Three customers' tries at the same moment each sweep, and none waits for another.
    await asyncio.gather(
        *(wrong(attempts, f"C900000{n}", contact_id, limits=ROOMY) for n in range(3))
    )
    left = sum((await rows(db)).values())
    await wrong(attempts, "C9000000", contact_id, limits=ROOMY)

    assert 3 <= left <= SWEEP_ROWS * 2 + 50 + 3  # each removed up to its share, none the same
    assert await rows(db) == {"C9000000": 2, "C9000001": 1, "C9000002": 1}


async def test_what_is_known_of_the_tries_for_one_id(db: Database) -> None:
    attempts = IdentityAttemptRepository(db)
    contacts = [await a_contact(db) for _ in range(CONTACTS_SHOWN + 2)]
    for contact_id in contacts:
        await wrong(attempts, "C0000001", contact_id, known=False, limits=ROOMY)
    await aged(db, HOUR + 1)
    await wrong(attempts, "C0000001", contacts[0], limits=ROOMY)
    await wrong(attempts, "C0000002", contacts[0], known=False)

    seen = await attempts.recent("C0000001", ROOMY)
    never_known = await attempts.recent("C0000002", ROOMY)
    nothing = await attempts.recent("C0000003", ROOMY)

    assert (seen.tries, seen.in_window) == (len(contacts) + 1, 1)
    assert seen.conversations == len(contacts)
    assert seen.contacts[0] == contacts[0]  # the latest first
    assert len(seen.contacts) == CONTACTS_SHOWN
    assert seen.account_known  # the CRM knew it once: it is somebody's
    assert seen.first_at is not None
    assert seen.last_at is not None
    assert (seen.last_at - seen.first_at).total_seconds() > HOUR
    assert not never_known.account_known
    assert (nothing.tries, nothing.contacts, nothing.first_at) == (0, (), None)


# --- what staff are told ------------------------------------------------------------------------


async def test_staff_are_told_when_the_last_try_is_used_and_not_before(db: Database) -> None:
    """Told only when a try is refused, guessing that stops at the limit every time would
    never be told at all."""
    guard = guard_on(db)
    first, second = await a_contact(db), await a_contact(db)
    for contact_id in (first, first, first, second):
        await wrong_answer(guard, "C0000001", contact_id)
    before = await OpsRepository(db).list_open()

    await wrong_answer(guard, "C0000001", second)

    (item,) = await OpsRepository(db).list_open()
    assert before == []
    assert (item.kind, item.subject, item.priority) == (IDENTITY_ATTEMPTS, "C0000001", 2)
    assert "answered wrongly 5 times in the last 24 hours, 5 of them within 60 minutes" in (
        item.summary
    )
    assert "in 2 conversation(s)" in item.summary
    assert item.details["wrong_answers_in_the_window"] == LIMITS.per_window
    assert item.details["conversations"] == 2
    assert set(item.details["contacts"].split(", ")) == {str(first), str(second)}


async def test_a_refused_try_is_counted_on_the_item_that_is_open(db: Database) -> None:
    guard = guard_on(db)
    contact_id = await a_contact(db)
    for _ in range(LIMITS.per_window):
        await wrong_answer(guard, "C0000001", contact_id)

    assert await guard.take("C0000001", contact_id) is None
    await guard.refused("C0000001")

    (item,) = await OpsRepository(db).list_open()
    assert (item.subject, item.occurrences) == ("C0000001", 2)


async def test_staff_are_not_told_of_an_id_that_is_nobodys(db: Database) -> None:
    """There is no customer to look after. The refusal is on the conversation's audit trail."""
    guard = guard_on(db)
    contact_id = await a_contact(db)
    for _ in range(LIMITS.per_window):
        attempt = await guard.take("C9999999", contact_id)
        assert attempt is not None
        await guard.wrong(attempt, "C9999999", account_known=False)

    assert await guard.take("C9999999", contact_id) is None
    await guard.refused("C9999999")

    assert await OpsRepository(db).list_open() == []


async def locked_out(guard: IdentityGuard, customer_id: str, contact_id: UUID) -> None:
    for _ in range(LIMITS.per_window):
        await wrong_answer(guard, customer_id, contact_id)


async def test_customer_ids_tried_in_turn_do_not_fill_the_staff_queue(db: Database) -> None:
    """Each customer has an item of their own up to a number; one item stands for the rest."""
    guard = guard_on(db)
    contact_id = await a_contact(db)
    customers = [f"C{number:07d}" for number in range(1, MAX_CUSTOMER_ITEMS + 4)]

    for customer_id in customers:
        await locked_out(guard, customer_id, contact_id)
    await guard.refused(customers[0])  # one of the first is tried again

    items = await OpsRepository(db).list_open()
    own = {item.subject: item for item in items if item.subject != MANY_CUSTOMERS}
    (many,) = [item for item in items if item.subject == MANY_CUSTOMERS]
    assert set(own) == set(customers[:MAX_CUSTOMER_ITEMS])
    assert own[customers[0]].occurrences == 2
    assert (many.kind, many.priority, many.occurrences) == (IDENTITY_ATTEMPTS, 1, 3)
    assert many.details["latest"]["customer_id"] == customers[-1]
    assert items[0] is many  # read first


async def test_items_nobody_closed_do_not_hide_the_next_customer(db: Database) -> None:
    """Ten items left open from last week would otherwise send every customer locked since
    into the one item that names only the latest."""
    guard = guard_on(db)
    contact_id = await a_contact(db)
    for number in range(1, MAX_CUSTOMER_ITEMS + 1):
        await locked_out(guard, f"C{number:07d}", contact_id)
    await aged(db, DAY_SECONDS + 1)

    await locked_out(guard, "C0000099", contact_id)

    subjects = {item.subject for item in await OpsRepository(db).list_open()}
    assert "C0000099" in subjects
    assert MANY_CUSTOMERS not in subjects


async def test_wrong_answers_for_many_different_customers_are_told_and_nothing_is_refused(
    db: Database,
) -> None:
    """One guess at each of many customer IDs stays under every limit for one of them."""
    guard = guard_on(db, spread=4)
    contact_id = await a_contact(db)

    for number in range(1, 4):
        await wrong_answer(guard, f"C{number:07d}", contact_id)
    attempt = await guard.take("C9999999", contact_id)  # nobody's: not a customer
    assert attempt is not None
    await guard.wrong(attempt, "C9999999", account_known=False)
    before = await OpsRepository(db).list_open()
    await wrong_answer(guard, "C0000004", contact_id)
    await wrong_answer(guard, "C0000005", contact_id)

    (item,) = await OpsRepository(db).list_open()
    assert before == []
    assert (item.subject, item.priority, item.occurrences) == (ACROSS_CUSTOMERS, 1, 2)
    assert item.details["customers"] == 4
    assert item.details["latest"]["customers"] == 5
    assert await guard.take("C0000006", contact_id) is not None


# --- the contact ---------------------------------------------------------------------------------


async def test_a_contact_is_bound_to_a_customer_once(db: Database) -> None:
    contacts = ContactRepository(db)
    contact_id = await a_contact(db)
    await contacts.update(contact_id, customer_id="C0000001", status="IN_PROGRESS")

    await contacts.update(contact_id, customer_id="C0000001", intent="connectivity")
    await contacts.update(contact_id, customer_id=None, status="AWAITING_NETWORK")
    await contacts.update(contact_id, customer_id="", status="AWAITING_CUSTOMER")
    with pytest.raises(ContactBoundError):
        await contacts.update(contact_id, customer_id="C0000002", status="CLOSED")

    contact = await contacts.get(contact_id)
    assert contact is not None
    assert (contact.customer_id, contact.status) == ("C0000001", "AWAITING_CUSTOMER")
    with pytest.raises(ContactBoundError):
        await contacts.update(uuid4(), customer_id="C0000001")


# --- the agent -----------------------------------------------------------------------------------


class BrokenCrm:
    """A CRM client that fails as code fails, not as a gateway answers."""

    async def verify(self, customer_id: str, **_: Any) -> Any:
        raise ConnectionError("the connection was reset")


class AnsweringCrm:
    """A CRM client whose verify ends in one kind of error, as the gateway would report it."""

    def __init__(self, kind: ErrorKind) -> None:
        self._kind = kind

    async def verify(self, customer_id: str, **_: Any) -> Any:
        return Err(self._kind, "as the test says", status_code=None)


async def answered_by(db: Database, crm: object) -> tuple[Any, UUID, TriageAgent, Auditor]:
    contact_id = await a_contact(db)
    contact = await ContactRepository(db).get(contact_id)
    assert contact is not None
    agent = TriageAgent(cast(CrmReader, crm), LLMGateway(OfflineProvider()), guard=guard_on(db))
    audit = Auditor(AuditRepository(db), contact_id=contact_id, correlation_id=uuid4().hex)
    return contact, contact_id, agent, audit


async def test_a_failure_of_ours_while_checking_does_not_cost_the_customer_a_try(
    db: Database,
) -> None:
    """The turn is tried again after such a failure. Each repeat would otherwise use a try
    up, and the customer would be locked out of their own account by our fault."""
    contact, _, agent, audit = await answered_by(db, BrokenCrm())

    for _ in range(LIMITS.per_day + 1):
        with pytest.raises(ConnectionError):
            await agent.run(
                contact,
                "internet down",
                audit,
                claim=Claim("C0000001", "1234"),
                awaiting_digits=False,
            )

    assert await rows(db) == {}


# An answer about the ID is counted as a wrong one; a call that failed is not.
ABOUT_THE_ID = (ErrorKind.NOT_FOUND, ErrorKind.INVALID, ErrorKind.GONE, ErrorKind.CONFLICT)
NO_ANSWER = (
    ErrorKind.TIMEOUT,
    ErrorKind.UNAVAILABLE,
    ErrorKind.CIRCUIT_OPEN,
    ErrorKind.RATE_LIMITED,
    ErrorKind.UNAUTHORIZED,
    ErrorKind.FORBIDDEN,
    ErrorKind.PROTOCOL,
)


def test_every_kind_of_error_the_gateway_reports_is_one_or_the_other() -> None:
    assert {*ABOUT_THE_ID, *NO_ANSWER} == set(ErrorKind)


@pytest.mark.parametrize("kind", [*ABOUT_THE_ID, *NO_ANSWER])
async def test_an_answer_about_the_id_is_counted_and_a_call_that_failed_is_not(
    db: Database, kind: ErrorKind
) -> None:
    """Counting a failure of ours would lock customers out by our fault. Not counting an
    answer (the account is retired) would be a way to ask about an ID for nothing."""
    contact, _, agent, audit = await answered_by(db, AnsweringCrm(kind))

    outcome = await agent.run(
        contact, "internet down", audit, claim=Claim("C0000001", "1234"), awaiting_digits=False
    )

    if kind in ABOUT_THE_ID:
        assert outcome.identity.status is IdentityStatus.VERIFICATION_FAILED
        assert await rows(db) == {"C0000001": 1}
    else:
        assert outcome.identity.status is IdentityStatus.CRM_UNAVAILABLE
        assert await rows(db) == {}
