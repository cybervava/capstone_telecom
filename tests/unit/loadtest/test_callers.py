"""The callers of a load run: what they send, when they give up, and what they report."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
from loadtest.callers import Callers, Pace
from loadtest.outcome import Offered, Targets
from loadtest.population import CHANNEL, WEB, Caller

FAST = Pace(retry_first=0.01, retry_longest=0.02, poll=0.01, poll_when_delayed=0.03, stay=0.3)
CENTRE = {"Authorization": "Bearer channel-token"}
CONTACT = {"contact_id": "0e0a4d1c-9d4e-4a57-8f3e-2f1f6c3e7a10", "complaint_ref": "SKL-1"}


def caller(route: str = WEB, **more: Any) -> Caller:
    fields: dict[str, Any] = {
        "customer_id": "C0000042",
        "phone": "+919800000042",
        "node_id": "BLR-OLT-0001",
        "affected": True,
        "route": route,
        "text": "wifi is not working at all",
    }
    return Caller(**{**fields, **more})


def envelope(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


class Api:
    """A care API that answers as told and remembers what it was sent."""

    def __init__(self, intake: Callable[[int], httpx.Response]) -> None:
        self._intake = intake
        self.intakes: list[dict[str, Any]] = []
        self.intake_headers: list[str] = []
        self.sign_ins: list[dict[str, Any]] = []
        self.polls = 0
        self.replies_after = 1
        self.delayed_on_page = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/dev/token":
            self.sign_ins.append(json.loads(request.content))
            return httpx.Response(200, json=envelope({"token": "customer-token"}))
        if request.url.path == "/v1/contacts":
            self.intakes.append(json.loads(request.content))
            self.intake_headers.append(request.headers["authorization"])
            return self._intake(len(self.intakes))
        self.polls += 1
        answered = self.polls >= self.replies_after
        messages = [{"direction": "inbound", "template_id": None}]
        if answered:
            messages.append({"direction": "outbound", "template_id": "outage_active.v1"})
        return httpx.Response(
            200, json=envelope({"messages": messages, "reply_delayed": self.delayed_on_page})
        )


def created(delayed: bool = False) -> httpx.Response:
    return httpx.Response(201, json=envelope({**CONTACT, "reply_delayed": delayed}))


async def offer(api: Api, who: Caller, *, within: float = 120.0) -> Offered:
    transport = httpx.MockTransport(api)
    async with httpx.AsyncClient(transport=transport, base_url="http://care.test") as http:
        callers = Callers(
            http, channel_headers=CENTRE, targets=Targets(reference_seconds=within), pace=FAST
        )
        (outcome,) = await callers.offer_all([0.0], [who])
    return outcome


async def test_a_web_caller_signs_in_opens_the_contact_and_stays_for_the_reply() -> None:
    api = Api(lambda _: created())

    outcome = await offer(api, caller())

    assert api.sign_ins == [{"role": "customer", "customer_id": "C0000042"}]
    assert api.intake_headers == ["Bearer customer-token"]
    assert api.intakes[0]["channel"] == "chat"
    assert api.intakes[0]["text"] == "wifi is not working at all"
    assert "caller_phone" not in api.intakes[0]
    assert outcome.accepted
    assert outcome.attempts == 1
    assert outcome.reference_seconds is not None
    assert 0 <= outcome.reference_seconds < 1
    assert outcome.reply_template == "outage_active.v1"
    assert outcome.reply_seconds is not None
    assert outcome.reply_seconds >= outcome.reference_seconds
    assert not outcome.told_delayed


async def test_a_caller_through_a_contact_centre_is_relayed_with_the_line_and_rings_off() -> None:
    api = Api(lambda _: created())

    outcome = await offer(api, caller(CHANNEL))

    assert api.sign_ins == []
    assert api.intake_headers == ["Bearer channel-token"]
    assert api.intakes[0]["channel"] == "call_transcript"
    assert api.intakes[0]["caller_phone"] == "+919800000042"
    assert outcome.accepted
    assert api.polls == 0
    assert outcome.reply_seconds is None


async def test_a_request_that_fails_is_sent_again_with_the_same_request_id() -> None:
    api = Api(lambda n: httpx.Response(503) if n < 3 else created())

    outcome = await offer(api, caller(CHANNEL))

    assert outcome.accepted
    assert outcome.attempts == 3
    assert len({sent["client_request_id"] for sent in api.intakes}) == 1
    assert outcome.reference_seconds is not None
    assert outcome.reference_seconds >= 0.025  # the two waits are in the measured time


async def test_a_connection_that_fails_is_tried_again_too() -> None:
    def intake(n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ConnectError("refused")
        return created()

    outcome = await offer(Api(intake), caller(CHANNEL))

    assert (outcome.accepted, outcome.attempts) == (True, 2)


async def test_a_first_contact_turned_away_by_a_rate_limit_is_counted_and_not_sent_again() -> None:
    api = Api(lambda _: httpx.Response(429))

    outcome = await offer(api, caller(CHANNEL))

    assert not outcome.accepted
    assert outcome.rate_limited
    assert (outcome.attempts, outcome.error) == (1, "HTTP 429")
    assert len(api.intakes) == 1


async def test_a_request_the_api_refuses_as_wrong_is_not_sent_again() -> None:
    api = Api(lambda _: httpx.Response(422))

    outcome = await offer(api, caller(CHANNEL))

    assert (outcome.accepted, outcome.rate_limited, outcome.error) == (False, False, "HTTP 422")
    assert len(api.intakes) == 1


async def test_a_caller_gives_up_when_the_reference_is_not_there_in_time() -> None:
    api = Api(lambda _: httpx.Response(503))

    outcome = await offer(api, caller(CHANNEL), within=0.1)

    assert not outcome.accepted
    assert outcome.reference_seconds is None
    assert outcome.error == "HTTP 503"
    assert 1 <= outcome.attempts <= 12


async def test_a_reference_that_comes_after_the_time_allowed_is_not_an_accepted_contact() -> None:
    """The API took the contact, and its caller still stood without a reference when the two
    minutes were up. That is what the rule is about, so it is counted as it was for the caller,
    and the time is kept."""

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.15)
        return created()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(slow), base_url="http://care.test"
    ) as http:
        callers = Callers(
            http, channel_headers=CENTRE, targets=Targets(reference_seconds=0.05), pace=FAST
        )
        (outcome,) = await callers.offer_all([0.0], [caller(CHANNEL)])

    assert not outcome.accepted
    assert outcome.reference_seconds is not None
    assert outcome.reference_seconds >= 0.15
    assert outcome.error == "reference after the time allowed"


async def test_the_generator_says_how_late_it_was_itself() -> None:
    api = Api(lambda _: created())
    transport = httpx.MockTransport(api)
    async with httpx.AsyncClient(transport=transport, base_url="http://care.test") as http:
        callers = Callers(http, channel_headers=CENTRE, targets=Targets(), pace=FAST)
        await callers.offer_all([0.0, 0.01, 0.02], [caller(CHANNEL)] * 3)

    # Offered when due, give or take the event loop: nowhere near the times being measured.
    assert 0 <= callers.worst_lag_seconds < 0.5


async def test_a_caller_told_of_a_delay_asks_less_often_and_reports_having_been_told() -> None:
    api = Api(lambda _: created(delayed=True))
    api.replies_after = 3
    api.delayed_on_page = True

    outcome = await offer(api, caller())

    assert outcome.told_delayed
    assert outcome.reply_seconds is not None
    assert outcome.reply_seconds >= 0.09  # three asks, 0.03 s apart: the slower pace
    assert api.polls == 3


async def test_a_delay_first_heard_of_on_the_page_also_counts_as_told() -> None:
    api = Api(lambda _: created(delayed=False))
    api.delayed_on_page = True

    outcome = await offer(api, caller())

    assert outcome.told_delayed


async def test_a_caller_who_gets_no_reply_leaves_after_a_while_with_the_reference() -> None:
    api = Api(lambda _: created())
    api.replies_after = 10_000

    outcome = await offer(api, caller())

    assert outcome.accepted
    assert outcome.reply_seconds is None
    assert api.polls >= 5


async def test_every_contact_is_offered_when_it_is_due_whatever_became_of_the_others() -> None:
    api = Api(lambda n: httpx.Response(503) if n == 1 else created())
    transport = httpx.MockTransport(api)
    people = [caller(CHANNEL, customer_id=f"C00000{n:02d}") for n in range(5)]
    held_up = Pace(retry_first=0.6, retry_longest=0.6, poll=0.01, poll_when_delayed=0.03, stay=0.3)
    async with httpx.AsyncClient(transport=transport, base_url="http://care.test") as http:
        callers = Callers(http, channel_headers=CENTRE, targets=Targets(), pace=held_up)
        outcomes = await callers.offer_all([0.0, 0.02, 0.04, 0.06, 0.08], people)

    assert [outcome.due for outcome in outcomes] == [0.0, 0.02, 0.04, 0.06, 0.08]
    assert all(outcome.accepted for outcome in outcomes)
    # The first was held up for over half a second by a failure; the others did not wait.
    first, *others = outcomes
    assert first.attempts == 2
    assert first.reference_seconds is not None
    assert first.reference_seconds >= 0.6
    assert all(o.reference_seconds is not None and o.reference_seconds < 0.3 for o in others)
