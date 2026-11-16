"""The callers of a load run: each offers one contact to the care API, as a client would.

A web caller signs in, opens the contact and stays on the page for the reply as the page does:
it asks again every 1.2 seconds, or every 5 once told that replies are delayed. A caller who
comes through a contact centre is relayed by the centre's integration, which states the
caller's line; that caller gets the reference and rings off.

A request that fails for a reason that may pass (no connection, a timeout, a 5xx) is sent again
with the same request id, as a client must, until two minutes after the contact was due. A 429
is not sent again: a first contact turned away is the failure this test looks for.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

import httpx

from loadtest.outcome import Offered, Targets
from loadtest.population import CHANNEL, WEB, Caller

ACCEPTED = (httpx.codes.OK, httpx.codes.CREATED)
LATE = "reference after the time allowed"


def _request(caller: Caller) -> dict[str, Any]:
    """What is sent for this caller. The request id is made once and sent again unchanged."""
    body: dict[str, Any] = {"client_request_id": uuid4().hex, "text": caller.text}
    if caller.route == CHANNEL:
        return body | {"channel": "call_transcript", "caller_phone": caller.phone}
    return body | {"channel": "chat"}


@dataclass(frozen=True)
class Pace:
    """The client's timings in seconds. The polling is the customer page's own."""

    retry_first: float = 0.5
    retry_longest: float = 5.0
    poll: float = 1.2
    poll_when_delayed: float = 5.0
    # How long a web caller waits for the reply before closing the page.
    stay: float = 180.0


class Callers:
    def __init__(
        self,
        api: httpx.AsyncClient,
        *,
        channel_headers: Mapping[str, str],
        targets: Targets,
        pace: Pace | None = None,
    ) -> None:
        self._api = api
        self._channel = dict(channel_headers)
        self._targets = targets
        self._pace = pace or Pace()
        # The latest a contact was offered after it was due: the generator's own lag. Times
        # are measured from when a contact was due, so this is inside every one of them.
        self.worst_lag_seconds = 0.0

    async def offer_all(self, due: Sequence[float], callers: Sequence[Caller]) -> list[Offered]:
        """Offer every contact when it is due, whatever became of the ones before it."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        offers = []
        for at, caller in zip(due, callers, strict=True):
            wait = started + at - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self.worst_lag_seconds = max(self.worst_lag_seconds, loop.time() - started - at)
            offers.append(loop.create_task(self._offer(caller, at, started)))
        return list(await asyncio.gather(*offers))

    async def _offer(self, caller: Caller, due: float, started: float) -> Offered:
        loop = asyncio.get_running_loop()

        def since_due() -> float:
            return loop.time() - started - due

        body = _request(caller)
        offered = Offered(due=due, route=caller.route, affected=caller.affected)
        headers: dict[str, str] | None = self._channel if caller.route == CHANNEL else None
        attempts, wait = 0, self._pace.retry_first
        while True:
            attempts += 1
            try:
                if headers is None:
                    headers = await self._sign_in(caller.customer_id)
                response = await self._api.post("/v1/contacts", json=body, headers=headers)
            except httpx.HTTPError as exc:
                error = type(exc).__name__
            else:
                if response.status_code in ACCEPTED:
                    data = response.json()["data"]
                    took = since_due()
                    with_reference = replace(
                        offered,
                        reference_seconds=took,
                        attempts=attempts,
                        told_delayed=bool(data.get("reply_delayed")),
                    )
                    if took > self._targets.reference_seconds:
                        # The API took the contact, and its caller still had no reference
                        # when the time was up. It is counted as the caller found it.
                        return replace(with_reference, error=LATE)
                    accepted = replace(with_reference, accepted=True)
                    if caller.route != WEB:
                        return accepted
                    return await self._stay(accepted, str(data["contact_id"]), headers, since_due)
                error = f"HTTP {response.status_code}"
                limited = response.status_code == httpx.codes.TOO_MANY_REQUESTS
                if limited or not response.is_server_error:
                    # Turned away, or refused for what was sent: asking again changes nothing.
                    return replace(offered, rate_limited=limited, attempts=attempts, error=error)
            if since_due() + wait > self._targets.reference_seconds:
                return replace(offered, attempts=attempts, error=error)
            await asyncio.sleep(wait)
            wait = min(wait * 2, self._pace.retry_longest)

    async def _sign_in(self, customer_id: str) -> dict[str, str]:
        response = await self._api.post(
            "/v1/dev/token", json={"role": "customer", "customer_id": customer_id}
        )
        response.raise_for_status()
        return {"Authorization": f"Bearer {response.json()['data']['token']}"}

    async def _stay(
        self,
        accepted: Offered,
        contact_id: str,
        headers: Mapping[str, str],
        since_due: Callable[[], float],
    ) -> Offered:
        """Wait on the page for the first reply, asking as often as the page does."""
        told = accepted.told_delayed
        delayed = told
        leave_at = since_due() + self._pace.stay
        while since_due() < leave_at:
            await asyncio.sleep(self._pace.poll_when_delayed if delayed else self._pace.poll)
            try:
                response = await self._api.get(f"/v1/contacts/{contact_id}", headers=headers)
                response.raise_for_status()
            except httpx.HTTPError:
                continue  # the page says it could not refresh, and asks again
            data = response.json()["data"]
            delayed = bool(data.get("reply_delayed"))
            told = told or delayed
            reply = next((m for m in data["messages"] if m["direction"] == "outbound"), None)
            if reply is not None:
                return replace(
                    accepted,
                    told_delayed=told,
                    reply_seconds=since_due(),
                    reply_template=reply.get("template_id"),
                )
        return replace(accepted, told_delayed=told)
