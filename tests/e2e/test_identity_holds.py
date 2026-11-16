"""An identity, once verified, holds, and the check behind it cannot be worn down.

A customer who is not signed in proves who they are with two things they know: the customer ID
and the last four digits of the registered mobile. Four digits are ten thousand guesses. A
conversation allows three, but nothing counted them for one customer ID across conversations,
and a caller who had verified as themselves could go on guessing for someone else without end.
"""

from __future__ import annotations

from typing import Any

from skylink_care.agents.identity_guard import IDENTITY_ATTEMPTS
from skylink_care.db.identity import IdentityAttemptRepository, Limits
from skylink_care.db.ops import OpsRepository
from tests.e2e.test_edges_and_degraded_mode import customer_with
from tests.e2e.test_loadtest_facts import customer_on_another_node
from tests.support.platform import Platform
from tests.support.stack import Simulator

WRONG_ANSWER = "don't match our records"


async def tool_calls(platform: Platform, contact_id: Any, tool: str) -> list[dict[str, Any]]:
    trail = await platform.runtime.audit.for_contact(contact_id)
    return [dict(record.inputs) for record in trail if record.step == f"tool:{tool}"]


def wrong_digits_for(customer: dict[str, Any]) -> str:
    return "0000" if customer["phone"][-4:] != "0000" else "1111"


async def identity_items(platform: Platform) -> list[Any]:
    items = await OpsRepository(platform.runtime.db).list_open()
    return [item for item in items if item.kind == IDENTITY_ATTEMPTS]


async def test_a_verified_contact_cannot_be_moved_to_another_customer(
    platform: Platform, simulator: Simulator
) -> None:
    """Verified as themselves, a caller offers another customer's ID with that customer's
    number, in the form that verified them a moment ago. The conversation stays theirs: the
    second claim is not checked, and nothing of the other customer is attached or told.
    Another account is another conversation."""
    mine = await customer_with(simulator)
    other = await customer_on_another_node(platform, simulator, mine["true_node_id"])
    theirs = await simulator.start_outage(other["true_node_id"])
    intake = await platform.contact(
        f"internet down. customer id {mine['customer_id']}, phone {mine['phone']}"
    )
    await platform.settle()
    checked_before = await tool_calls(platform, intake.contact_id, "crm.verify")
    async with platform.runtime.db.connection() as conn:
        cursor = await conn.execute("SELECT count(*) AS tries FROM identity_attempts")
        counted = await cursor.fetchone()
    assert counted is not None
    assert counted["tries"] == 0  # the right answer was given back: it costs nothing

    await platform.say(
        intake.contact_id,
        f"sorry, my id is {other['customer_id']}, phone {other['phone']}. is there an outage?",
    )
    await platform.settle()

    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.customer_id == mine["customer_id"]
    assert await tool_calls(platform, intake.contact_id, "crm.verify") == checked_before
    looked_up = await tool_calls(platform, intake.contact_id, "crm.customer")
    assert {call["customer_id"] for call in looked_up} == {mine["customer_id"]}
    assert not any(
        theirs["outage_id"] in reply for reply in await platform.replies(intake.contact_id)
    )
    trail = await platform.runtime.audit.for_contact(intake.contact_id)
    ignored = [record for record in trail if record.step == "identity.claim_ignored"]
    assert len(ignored) == 1
    assert ignored[0].outputs["kept"] == mine["customer_id"]


async def test_a_signed_in_customer_stays_themselves_whatever_id_they_type(
    platform: Platform, simulator: Simulator
) -> None:
    """The sign-in says who this is. An ID typed into the message is not even a claim."""
    mine = await customer_with(simulator)
    other = await customer_on_another_node(platform, simulator, mine["true_node_id"])
    theirs = await simulator.start_outage(other["true_node_id"])

    intake = await platform.contact(
        f"my id is {other['customer_id']}, phone {other['phone']}. is there an outage?",
        authenticated_customer_id=mine["customer_id"],
    )
    await platform.settle()

    contact = await platform.runtime.contacts.get(intake.contact_id)
    assert contact is not None
    assert contact.customer_id == mine["customer_id"]
    assert await tool_calls(platform, intake.contact_id, "crm.verify") == []
    assert not any(
        theirs["outage_id"] in reply for reply in await platform.replies(intake.contact_id)
    )


async def one_guess(
    platform: Platform, customer_id: str, digits: str
) -> tuple[list[str], list[dict[str, Any]], Any]:
    """A fresh conversation that claims the ID and offers the digits. Returns what was said
    (with the reference taken out), the checks put to the CRM, and the contact."""
    intake = await platform.contact("my internet is not working")
    await platform.settle()
    await platform.say(intake.contact_id, f"my id is {customer_id}")
    await platform.settle()
    await platform.say(intake.contact_id, digits)
    await platform.settle()
    said = [
        reply.replace(intake.complaint_ref, "<REF>")
        for reply in await platform.replies(intake.contact_id)
    ]
    return said, await tool_calls(platform, intake.contact_id, "crm.verify"), intake.contact_id


async def test_guessing_the_digits_of_one_customer_is_stopped_across_conversations(
    platform: Platform, simulator: Simulator
) -> None:
    """After a handful of wrong guesses for one customer ID, wherever they were made, a
    further guess is answered exactly like the others and is not put to the CRM at all. Not
    even the right digits get through until the hour is over. Staff are told once."""
    settings = platform.runtime.settings
    victim = await customer_with(simulator)
    wrong = wrong_digits_for(victim)

    guesses = [
        await one_guess(platform, victim["customer_id"], wrong)
        for _ in range(settings.identity_fail_limit)
    ]
    locked = await one_guess(platform, victim["customer_id"], wrong)
    with_the_right_digits = await one_guess(platform, victim["customer_id"], victim["phone"][-4:])

    assert all(len(checks) == 1 for _, checks, _ in guesses)
    assert WRONG_ANSWER in guesses[0][0][-1]
    assert (locked[0], locked[1]) == (guesses[0][0], [])
    assert (with_the_right_digits[0], with_the_right_digits[1]) == (guesses[0][0], [])
    contact = await platform.runtime.contacts.get(with_the_right_digits[2])
    assert contact is not None
    assert contact.customer_id is None
    (item,) = await identity_items(platform)
    assert item.subject == victim["customer_id"]
    # Raised when the last try was used, and seen again with each guess that was stopped.
    assert item.occurrences == 3
    assert item.details["conversations"] == settings.identity_fail_limit
    trail = await platform.runtime.audit.for_contact(locked[2])
    assert [record.outcome for record in trail if record.step == "identity.locked"] == ["REFUSED"]


async def test_a_customer_id_that_does_not_exist_is_locked_like_one_that_does(
    platform: Platform, simulator: Simulator
) -> None:
    """Otherwise the lock itself would say which accounts exist."""
    settings = platform.runtime.settings

    guesses = [
        await one_guess(platform, "C9999999", "1234") for _ in range(settings.identity_fail_limit)
    ]
    locked = await one_guess(platform, "C9999999", "1234")

    assert all(len(checks) == 1 for _, checks, _ in guesses)
    assert (locked[0], locked[1]) == (guesses[0][0], [])
    # Staff are told of a locked customer. This ID is nobody's: there is no one to look after.
    assert await identity_items(platform) == []


async def test_conversations_answering_at_once_get_only_the_tries_that_are_left(
    platform: Platform, simulator: Simulator
) -> None:
    """However the turns of many conversations fall, no more answers reach the CRM than the
    limit. (That answers given at the very same moment cannot each take the last try is
    shown where it can be forced: ``tests/integration/test_identity_attempts.py``.)"""
    settings = platform.runtime.settings
    victim = await customer_with(simulator)
    wrong = wrong_digits_for(victim)
    conversations = []
    for _ in range(settings.identity_fail_limit + 3):
        intake = await platform.contact(f"my internet is not working, id {victim['customer_id']}")
        conversations.append(intake.contact_id)
    await platform.settle()

    for contact_id in conversations:
        await platform.say(contact_id, wrong)
    await platform.settle(rounds=40)

    checks = [await tool_calls(platform, contact_id, "crm.verify") for contact_id in conversations]
    assert sum(len(made) for made in checks) == settings.identity_fail_limit
    for contact_id in conversations:
        *_, last = await platform.replies(contact_id)
        assert WRONG_ANSWER in last


async def test_whoever_takes_a_locked_conversation_over_is_told_that_it_is_locked(
    platform: Platform, simulator: Simulator
) -> None:
    """The customer hears what any wrong answer hears. The person who takes over must know
    that the right answer would have been refused too, and for which customer ID."""
    settings = platform.runtime.settings
    victim = await customer_with(simulator)
    intake = await platform.contact("my internet is not working")
    await platform.settle()
    attempts = IdentityAttemptRepository(platform.runtime.db)
    limits = Limits(
        settings.identity_fail_limit,
        settings.identity_fail_window_seconds,
        settings.identity_fail_daily_limit,
    )
    for _ in range(settings.identity_fail_limit):  # guessed elsewhere, by someone else
        attempt = await attempts.take(victim["customer_id"], intake.contact_id, limits)
        assert attempt is not None
        await attempts.mark_wrong(attempt, account_known=True)

    for text in (f"my id is {victim['customer_id']}", victim["phone"][-4:], victim["phone"][-4:]):
        await platform.say(intake.contact_id, text)
        await platform.settle()

    (handoff,) = await platform.runtime.handoffs.list_open()
    assert handoff.reason == "IDENTITY_UNRESOLVED"
    assert f"customer ID {victim['customer_id']} is locked" in handoff.summary
    assert "Verify the customer another way" in handoff.summary
    assert await tool_calls(platform, intake.contact_id, "crm.verify") == []
    assert not any("locked" in reply for reply in await platform.replies(intake.contact_id))


async def test_a_contact_stays_its_customers_when_the_workflow_has_forgotten_everything(
    platform: Platform, simulator: Simulator
) -> None:
    """A run that died before its state was saved is repeated from nothing. Whose contact it
    is must not depend on that state: not when the CRM cannot be read on the next turn, and
    not when the next message offers somebody else's ID with the right digits."""
    mine = await customer_with(simulator)
    other = await customer_on_another_node(platform, simulator, mine["true_node_id"])
    intake = await platform.contact(
        f"internet down. customer id {mine['customer_id']}, phone {mine['phone']}"
    )
    await platform.settle()

    await platform.checkpointer.adelete_thread(str(intake.contact_id))
    await simulator.fault("crm", "down")
    await platform.say(intake.contact_id, "any news?")
    await platform.settle()
    await simulator.fault("crm", "ok")
    while_the_crm_was_down = await platform.runtime.contacts.get(intake.contact_id)

    again = await platform.contact(
        f"internet down. customer id {mine['customer_id']}, phone {mine['phone']}"
    )
    await platform.settle()
    await platform.checkpointer.adelete_thread(str(again.contact_id))
    await platform.say(again.contact_id, f"my id is {other['customer_id']}, phone {other['phone']}")
    await platform.settle()

    assert while_the_crm_was_down is not None
    assert while_the_crm_was_down.customer_id == mine["customer_id"]
    contact = await platform.runtime.contacts.get(again.contact_id)
    assert contact is not None
    assert contact.customer_id == mine["customer_id"]
    looked_up = await tool_calls(platform, again.contact_id, "crm.customer")
    assert {call["customer_id"] for call in looked_up} == {mine["customer_id"]}


async def test_the_lock_ends_with_its_hour_and_one_customers_lock_is_not_anothers(
    platform: Platform, simulator: Simulator
) -> None:
    settings = platform.runtime.settings
    victim = await customer_with(simulator)
    other = await customer_on_another_node(platform, simulator, victim["true_node_id"])
    wrong = wrong_digits_for(victim)
    for _ in range(settings.identity_fail_limit):
        await one_guess(platform, victim["customer_id"], wrong)

    someone_else = await one_guess(platform, other["customer_id"], other["phone"][-4:])
    async with platform.runtime.db.transaction() as conn:
        await conn.execute(
            "UPDATE identity_attempts SET attempted_at = attempted_at - make_interval(secs => %s)",
            (settings.identity_fail_window_seconds + 1,),
        )
    an_hour_later = await one_guess(platform, victim["customer_id"], victim["phone"][-4:])

    for said, checks, contact_id in (someone_else, an_hour_later):
        assert len(checks) == 1
        assert not any(WRONG_ANSWER in reply for reply in said)
        contact = await platform.runtime.contacts.get(contact_id)
        assert contact is not None
        assert contact.customer_id is not None


async def test_a_crm_that_cannot_answer_is_not_counted_as_a_wrong_guess(
    platform: Platform, simulator: Simulator
) -> None:
    """A customer must not be locked out of their own account by our outage."""
    settings = platform.runtime.settings
    customer = await customer_with(simulator)
    await simulator.fault("crm", "down")
    for _ in range(settings.identity_fail_limit + 1):
        await one_guess(platform, customer["customer_id"], customer["phone"][-4:])
    await simulator.fault("crm", "ok")

    said, checks, contact_id = await one_guess(
        platform, customer["customer_id"], customer["phone"][-4:]
    )

    assert len(checks) == 1
    assert not any(WRONG_ANSWER in reply for reply in said)
    contact = await platform.runtime.contacts.get(contact_id)
    assert contact is not None
    assert contact.customer_id == customer["customer_id"]
