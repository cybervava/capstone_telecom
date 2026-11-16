"""Handles on the running stack, apart from its callers.

Staff sign-in, for the contact centre's integration and for the watch on the backlog; the
simulator's admin API, which stands in for the NOC (it declares and resolves outages) and for a
dependency that fails; and the watch itself, which reads what staff see on their desk.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import httpx

# The assistant counts as keeping up again when nobody has waited this long for a turn, in
# this many readings in a row.
KEEPING_UP_SECONDS = 5
KEEPING_UP_READINGS = 3


class StackNotReadyError(RuntimeError):
    """The stack is not running, or not as this test needs it."""


async def staff_headers(
    api: httpx.AsyncClient, role: str, name: str, staff_key: str
) -> dict[str, str]:
    response = await api.post(
        "/v1/dev/token", json={"role": role, "name": name, "staff_key": staff_key}
    )
    if response.status_code != httpx.codes.OK:
        raise StackNotReadyError(
            f"could not sign in as {role} (HTTP {response.status_code}). Is CARE_DEV_STAFF_KEY"
            " the key the API was started with, and is the API running with CARE_ENV=dev?"
        )
    return {"Authorization": f"Bearer {response.json()['data']['token']}"}


@dataclass(frozen=True)
class Outage:
    outage_id: str
    root: str
    nodes: tuple[str, ...]


class Noc:
    """The simulator's admin API: the NOC's part, and the failing of a dependency."""

    def __init__(self, sim: httpx.AsyncClient) -> None:
        self._sim = sim

    async def reset(self) -> None:
        (await self._sim.post("/admin/v1/reset")).raise_for_status()

    async def declare(self, root: str) -> Outage:
        response = await self._sim.post(
            "/admin/v1/outages", json={"node_id": root, "cause": "FIBRE_CUT", "eta_minutes": 180}
        )
        response.raise_for_status()
        made = response.json()
        return Outage(str(made["outage_id"]), root, tuple(made["affected_node_ids"]))

    async def resolve(self, outage: Outage) -> None:
        (await self._sim.post(f"/admin/v1/outages/{outage.outage_id}/resolve")).raise_for_status()

    async def fail(self, target: str, mode: str) -> None:
        response = await self._sim.post("/admin/v1/chaos", json={"target": target, "mode": mode})
        response.raise_for_status()

    async def restore(self) -> None:
        (await self._sim.delete("/admin/v1/chaos")).raise_for_status()


@dataclass(frozen=True)
class LoadSample:
    at: float  # seconds since the watch began
    waiting: int
    oldest_seconds: float
    behind: bool


@dataclass
class Watch:
    """Reads the assistant's backlog every few seconds for as long as it is open."""

    api: httpx.AsyncClient
    headers: dict[str, str]
    # Signs in again when the token has run out: a run with its aftermath outlasts one.
    sign_in: Callable[[], Awaitable[dict[str, str]]] | None = None
    every: float = 5.0
    samples: list[LoadSample] = field(default_factory=list)
    missed: int = 0

    async def __aenter__(self) -> Self:
        self._began = asyncio.get_running_loop().time()
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._run())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        await self._task

    async def read(self) -> LoadSample | None:
        """One reading now. None when the API did not give one.

        Whatever goes wrong with a reading, it is a reading missed and counted: the watch must
        never be what ends a run.
        """
        try:
            response = await self.api.get("/v1/ops/load", headers=self.headers)
            if response.status_code == httpx.codes.UNAUTHORIZED and self.sign_in is not None:
                self.headers = await self.sign_in()
                response = await self.api.get("/v1/ops/load", headers=self.headers)
            response.raise_for_status()
            load = response.json()["data"]
            sample = LoadSample(
                at=round(asyncio.get_running_loop().time() - self._began, 1),
                waiting=int(load["waiting_contacts"]),
                oldest_seconds=float(load["oldest_seconds"]),
                behind=bool(load["behind"]),
            )
        except (httpx.HTTPError, StackNotReadyError, ValueError, KeyError, TypeError):
            self.missed += 1
            return None
        self.samples.append(sample)
        return sample

    async def until_keeping_up(self, within: float, every: float = 2.0) -> float | None:
        """Wait until nobody has waited long for a turn, several readings in a row.

        Returns how long that took in seconds, or None when it did not happen in time.
        """
        loop = asyncio.get_running_loop()
        began, calm = loop.time(), 0
        while loop.time() - began < within:
            sample = await self.read()
            calm = calm + 1 if sample and sample.oldest_seconds < KEEPING_UP_SECONDS else 0
            if calm >= KEEPING_UP_READINGS:
                return round(loop.time() - began - (KEEPING_UP_READINGS - 1) * every, 1)
            await asyncio.sleep(every)
        return None

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.read()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.every)
            except TimeoutError:
                continue
