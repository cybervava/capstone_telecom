"""Entry point for the API process (``care-api``)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.ratelimit import RateLimiter, per_minute
from skylink_care.config import get_settings
from skylink_care.db.database import Database
from skylink_care.observability.logging import configure_logging
from skylink_care.surge import build_gauge

# Intake keeps its own small pool so that worker load can never starve reference issuance.
INTAKE_POOL_MAX = 8
# A takeover holds a conversation's lock while it writes. Those locks have connections of
# their own: taken from the pool that serves requests, a few takeovers at once would each
# hold one connection and wait for a second that the others hold.
TAKEOVER_LOCKS = 2


def build() -> FastAPI:
    """Application factory for uvicorn. The pool is opened and closed with the server."""
    settings = get_settings()
    configure_logging()
    database = Database.create(
        settings.database_conninfo(), max_size=INTAKE_POOL_MAX, lock_connections=TAKEOVER_LOCKS
    )
    surge = build_gauge(settings, database)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.start()
        # Whether the orchestrator is behind is known before a caller asks, however far apart
        # callers come.
        keeping = asyncio.create_task(surge.keep_fresh())
        try:
            yield
        finally:
            keeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeping
            await surge.aclose()
            await database.close()

    return create_app(
        ApiDeps(
            database=database,
            jwt_secret=settings.require_jwt_secret(),
            env=settings.env,
            dev_staff_key=settings.staff_key_for_dev(),
            status_max_age_seconds=settings.status_max_age_seconds,
            surge=surge,
            contact_limit=per_minute(settings.api_contacts_per_minute),
            message_limit=per_minute(settings.api_messages_per_minute),
            channel_limit=RateLimiter(
                capacity=settings.api_channel_burst,
                refill_per_second=settings.api_channel_contacts_per_second,
            ),
        ),
        lifespan=lifespan,
    )


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "skylink_care.api.main:build",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        # The server's own lines go the way of every other library's: through the one handler
        # that redacts (``observability.logging``), not through handlers of the server's own.
        log_config=None,
    )
