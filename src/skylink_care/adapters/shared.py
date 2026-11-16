"""Callers that ask about one node at the same moment share one read of its live status.

In an outage every customer on a node writes in at once, and each diagnosis reads the node's
status. While one such read is on its way, the others in this process wait for its answer
instead of asking again: the answers would be the same answer, and a slow or failing API is
asked once, not once for every contact.

Only a read on its way is shared. An answer that has arrived is not kept for whoever asks
next, not even for a second. What a caller gets can therefore be older than its own question
by at most the time a read takes, which the whole-call limit bounds at a few seconds and the
freshness rule of the network decision allows for.

Two callers must not rely on that and do not use this at all: a turn woken by the event feed
(an outage declared or resolved), which has to see the network as it is after the event and
not as a read sent before it found it; and whoever is about to act on a status (the executor,
before it books a visit). Both read for themselves.

A failed read is likewise shared only by those already waiting for it.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Protocol

from skylink_care.adapters.models import LineTestDto, NodeStatusDto
from skylink_care.adapters.result import Result


class NetworkSource(Protocol):
    """What the network decision reads: a node's live status and a line's remote test."""

    async def node_status(self, node_id: str, *, correlation_id: str) -> Result[NodeStatusDto]: ...

    async def line_test(self, service_id: str, *, correlation_id: str) -> Result[LineTestDto]: ...


class SharedNodeStatus:
    def __init__(self, reader: NetworkSource) -> None:
        self._reader = reader
        self._reads: dict[str, asyncio.Task[Result[NodeStatusDto]]] = {}

    @property
    def in_flight(self) -> int:
        return len(self._reads)

    async def node_status(self, node_id: str, *, correlation_id: str) -> Result[NodeStatusDto]:
        """The node's live status, from the read already on its way if there is one.

        The read is made under the correlation id of whoever asked first.
        """
        read = self._reads.get(node_id)
        if read is None:
            read = asyncio.create_task(self._read(node_id, correlation_id))
            read.add_done_callback(partial(self._finished, node_id))
            self._reads[node_id] = read
        # Shielded: a caller that gives up must not take the read away from the others.
        return await asyncio.shield(read)

    async def line_test(self, service_id: str, *, correlation_id: str) -> Result[LineTestDto]:
        """A line test exercises one customer's line. It is never shared."""
        return await self._reader.line_test(service_id, correlation_id=correlation_id)

    async def _read(self, node_id: str, correlation_id: str) -> Result[NodeStatusDto]:
        try:
            return await self._reader.node_status(node_id, correlation_id=correlation_id)
        finally:
            # Before the answer is handed out: whoever asks from now on gets a read of their own.
            self._forget(node_id, asyncio.current_task())

    def _finished(self, node_id: str, read: asyncio.Task[Result[NodeStatusDto]]) -> None:
        """A read cancelled before it began never reaches its own clean-up: it is forgotten
        here. An error is raised to whoever waits; with nobody left waiting it is taken here,
        so that the event loop does not report it as never retrieved."""
        self._forget(node_id, read)
        if not read.cancelled():
            read.exception()

    def _forget(self, node_id: str, read: asyncio.Task[Result[NodeStatusDto]] | None) -> None:
        if self._reads.get(node_id) is read:
            del self._reads[node_id]
