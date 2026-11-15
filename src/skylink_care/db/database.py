"""Connection pools and the ways the platform talks to PostgreSQL.

Connections are autocommit with dict rows, which is also what the LangGraph checkpointer needs,
so one query pool per process serves both. Multi-statement work uses ``transaction()``.

Per-contact locks are held on connections from a second, separate pool. A workflow run keeps its
lock connection for the whole run while it also needs query connections; if both came from one
pool, enough concurrent runs would hold every connection as a lock and starve their own queries.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

type Connection = AsyncConnection[DictRow]
type Pool = AsyncConnectionPool[Connection]

log = structlog.get_logger(__name__)

_CONNECTION_KWARGS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}
_OPEN_TIMEOUT_SECONDS = 10.0


def _pool(conninfo: str, *, min_size: int, max_size: int) -> Pool:
    return AsyncConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        kwargs=_CONNECTION_KWARGS,
        connection_class=AsyncConnection[DictRow],
        open=False,
    )


class Database:
    """Owns the pools. Create with ``Database.open`` and close with ``close``."""

    def __init__(self, pool: Pool, lock_pool: Pool | None = None) -> None:
        self._pool = pool
        self._lock_pool = lock_pool

    @classmethod
    def create(
        cls, conninfo: str, *, min_size: int = 1, max_size: int = 10, lock_connections: int = 0
    ) -> Database:
        """Build the pools without connecting. Call ``start`` from inside the event loop.

        ``lock_connections`` is how many contacts this process may hold locked at once. Leave it
        at 0 in processes that never take contact locks (the API, the executor).
        """
        locks = _pool(conninfo, min_size=0, max_size=lock_connections) if lock_connections else None
        return cls(_pool(conninfo, min_size=min_size, max_size=max_size), locks)

    @classmethod
    async def open(
        cls, conninfo: str, *, min_size: int = 1, max_size: int = 10, lock_connections: int = 0
    ) -> Database:
        database = cls.create(
            conninfo, min_size=min_size, max_size=max_size, lock_connections=lock_connections
        )
        await database.start()
        return database

    async def start(self) -> None:
        """Open the pools and wait until they can serve connections."""
        await self._pool.open(wait=True, timeout=_OPEN_TIMEOUT_SECONDS)
        if self._lock_pool is not None:
            await self._lock_pool.open(wait=True, timeout=_OPEN_TIMEOUT_SECONDS)

    @property
    def pool(self) -> Pool:
        return self._pool

    async def close(self) -> None:
        if self._lock_pool is not None:
            await self._lock_pool.close()
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Connection]:
        """An autocommit connection for single statements."""
        async with self._pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Connection]:
        """A connection inside one transaction: commits on success, rolls back on any exception."""
        async with self._pool.connection() as conn, conn.transaction():
            yield conn

    @asynccontextmanager
    async def contact_lock(self, contact_id: UUID) -> AsyncIterator[bool]:
        """Try to take the per-contact lock for the duration of the block.

        Yields whether the lock was obtained. It is a session-level advisory lock held on its own
        connection, so PostgreSQL releases it by itself if this process dies: no visibility
        timeout is needed, and two workers can never run the same contact at once.

        If the lock cannot be released cleanly the connection is closed rather than returned to
        the pool, because a pooled connection that still holds the lock would block that contact
        for as long as the connection lives.
        """
        pool = self._lock_pool or self._pool
        name = f"contact:{contact_id}"
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (name,)
            )
            row = await cursor.fetchone()
            locked = bool(row and row["locked"])
            try:
                yield locked
            finally:
                if locked:
                    await _release(conn, name)


async def _release(conn: Connection, name: str) -> None:
    try:
        cursor = await conn.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s, 0)) AS released", (name,)
        )
        row = await cursor.fetchone()
        released = bool(row and row["released"])
    except BaseException:
        # Cancelled or broken mid-release: drop the session so the server frees the lock.
        await conn.close()
        raise
    if not released:
        # The session did not hold the lock any more: the connection was lost and re-made while
        # the block ran, so another worker may have run this contact at the same time.
        log.error("contact_lock_was_lost", lock=name)
        await conn.close()
