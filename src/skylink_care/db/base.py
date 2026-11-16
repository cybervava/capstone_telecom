"""Base class for repositories: a method can join the caller's transaction or use its own."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from skylink_care.db.database import Connection, Database


class Repository:
    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def database(self) -> Database:
        """The database this repository reads and writes."""
        return self._db

    @asynccontextmanager
    async def _conn(self, conn: Connection | None) -> AsyncIterator[Connection]:
        """Yield ``conn`` when the caller is inside a transaction, otherwise a pooled connection."""
        if conn is not None:
            yield conn
            return
        async with self._db.connection() as own:
            yield own
