"""The guard on the identity check: how often it may be got wrong for one customer ID.

A customer who is not signed in proves who they are with a customer ID and the last four digits
of the registered mobile. Four digits are ten thousand guesses, and a conversation that allows
three tries is no limit to someone who opens conversations. Tries are therefore counted for the
ID that was claimed, wherever they were made. Each is taken before the answer is put to the
CRM and given back if the answer was right. While none is left, a further try for that ID is
refused before it reaches the CRM, in the words of any other wrong answer, and staff are told
when the last one has been used.

The customer themselves is locked out with the guesser until the window has passed. They are
not left there: signing in and calling from the registered line do not pass this check, and a
conversation that cannot verify an identity ends with a person.

Whoever knows a customer ID can keep it locked by spending the allowed wrong answers in every
window. That is the price of any lock; what it costs the customer is the unsigned-in path, for
as long as somebody keeps paying it.

What no count for one ID sees is the same digits tried once on each of many customer IDs: one
guess in ten thousand is right, whoever's it is. Wrong answers for many different customers
within the window are therefore told to staff as well. Nothing is refused on that count: a
block that anyone can trigger for everyone would be a way to shut the check down at will.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from skylink_care.db.identity import IdentityAttemptRepository, Limits, RecentTries
from skylink_care.db.ops import OpsRepository

IDENTITY_ATTEMPTS = "IDENTITY_ATTEMPTS"
# The one item that stands for every locked customer beyond those with an item of their own.
MANY_CUSTOMERS = "many customer IDs"
# The item for wrong answers spread over many customers, none of them locked.
ACROSS_CUSTOMERS = "wrong answers across many customer IDs"
# At most this many customers have an item of their own at a time. Someone trying customer IDs
# in turn would otherwise write the staff queue full, one line for each.
MAX_CUSTOMER_ITEMS = 10


@dataclass(frozen=True)
class IdentityGuard:
    attempts: IdentityAttemptRepository
    ops: OpsRepository
    limits: Limits
    # Wrong answers for this many different customers within the window are told to staff.
    spread_alert_customers: int

    async def take(self, customer_id: str, contact_id: UUID) -> int | None:
        """A try for this ID, to be given back or marked wrong. None: the ID is locked."""
        return await self.attempts.take(customer_id, contact_id, self.limits)

    async def give_back(self, attempt: int) -> None:
        """The answer was right, or could not be checked. Neither counts against the ID."""
        await self.attempts.give_back(attempt)

    async def wrong(self, attempt: int, customer_id: str, *, account_known: bool) -> None:
        """The answer was wrong, for a customer that exists or for an ID that is nobody's."""
        await self.attempts.mark_wrong(attempt, account_known=account_known)
        seen = await self.attempts.recent(customer_id, self.limits)
        if seen.account_known and self.limits.reached(seen):
            # The last try has been used. Staff hear of it now, not only if someone tries
            # again: guessing that stops at the limit every time would never be refused.
            await self._tell(customer_id, seen)
        customers = await self.attempts.customers_answered_wrongly(
            window_seconds=self.limits.window_seconds
        )
        if customers >= self.spread_alert_customers:
            await self._tell_of_a_spread(customers)

    async def refused(self, customer_id: str) -> None:
        """A try was turned away unchecked."""
        seen = await self.attempts.recent(customer_id, self.limits)
        if seen.account_known:
            await self._tell(customer_id, seen)

    async def _tell(self, customer_id: str, seen: RecentTries) -> None:
        """Tell staff of a locked customer, once for each. For an ID the CRM never knew there
        is no one to look after: that refusal is on the audit trail of its conversation."""
        open_for = await self.ops.open_subjects(
            IDENTITY_ATTEMPTS, seen_within_seconds=self.limits.window_seconds
        )
        own_items = [item for item in open_for if item not in {MANY_CUSTOMERS, ACROSS_CUSTOMERS}]
        if customer_id in own_items or len(own_items) < MAX_CUSTOMER_ITEMS:
            await self._tell_of_one(customer_id, seen)
        else:
            await self._tell_of_many(customer_id, seen)

    async def _tell_of_one(self, customer_id: str, seen: RecentTries) -> None:
        minutes = self.limits.window_seconds // 60
        await self.ops.raise_item(
            kind=IDENTITY_ATTEMPTS,
            subject=customer_id,
            summary=(
                f"The identity check for customer ID {customer_id} was answered wrongly"
                f" {seen.tries} times in the last 24 hours, {seen.in_window} of them within"
                f" {minutes} minutes, in {seen.conversations} conversation(s). No further"
                f" answer is checked while {self.limits.per_window} of them are less than"
                f" {minutes} minutes old or {self.limits.per_day} less than a day. It may be"
                " the customer mistyping, or someone guessing the digits: nothing was"
                " disclosed and nothing on the account has changed. The customer can still"
                " sign in or call from the registered line; in a conversation like these,"
                " verify them another way."
            ),
            details=self._facts(seen),
            priority=2,
        )

    async def _tell_of_many(self, customer_id: str, seen: RecentTries) -> None:
        await self.ops.raise_item(
            kind=IDENTITY_ATTEMPTS,
            subject=MANY_CUSTOMERS,
            summary=(
                f"More than {MAX_CUSTOMER_ITEMS} customers have their identity check locked"
                " at once after repeated wrong answers. Each lock holds, and nothing was"
                " disclosed. This many at a time looks like someone trying customer IDs in"
                " turn. The first of them have an item of their own in this queue; the"
                " details of this one name the latest of the others."
            ),
            details={"customer_id": customer_id, **self._facts(seen)},
            priority=1,
        )

    async def _tell_of_a_spread(self, customers: int) -> None:
        minutes = self.limits.window_seconds // 60
        await self.ops.raise_item(
            kind=IDENTITY_ATTEMPTS,
            subject=ACROSS_CUSTOMERS,
            summary=(
                f"The identity check was answered wrongly for {customers} different customers"
                f" within {minutes} minutes. One wrong answer for each is under every limit"
                " for one customer ID, and one guess in ten thousand is right: this is what"
                " the same digits tried on one customer ID after another looks like. It is"
                " also what many customers mistyping during an outage looks like. Nothing is"
                " refused on this count. Whether verification by customer ID and digits"
                " should be suspended on the channel is a decision for a person."
            ),
            details={"customers": customers, "window_seconds": self.limits.window_seconds},
            priority=1,
        )

    def _facts(self, seen: RecentTries) -> dict[str, object]:
        return {
            "wrong_answers_in_a_day": seen.tries,
            "wrong_answers_in_the_window": seen.in_window,
            "conversations": seen.conversations,
            "contacts": ", ".join(str(contact) for contact in seen.contacts),
            "first_at": seen.first_at.isoformat() if seen.first_at else None,
            "last_at": seen.last_at.isoformat() if seen.last_at else None,
            "limit_in_the_window": self.limits.per_window,
            "window_seconds": self.limits.window_seconds,
            "limit_in_a_day": self.limits.per_day,
        }
