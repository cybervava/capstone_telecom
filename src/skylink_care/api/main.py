"""Entry point for the API process (``care-api``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.config import get_settings
from skylink_care.db.database import Database
from skylink_care.observability.logging import configure_logging

# Intake keeps its own small pool so that worker load can never starve reference issuance.
INTAKE_POOL_MAX = 8


def build() -> FastAPI:
    """Application factory for uvicorn. The pool is opened and closed with the server."""
    settings = get_settings()
    configure_logging()
    database = Database.create(settings.database_conninfo(), max_size=INTAKE_POOL_MAX)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.start()
        try:
            yield
        finally:
            await database.close()

    return create_app(
        ApiDeps(
            database=database,
            jwt_secret=settings.require_jwt_secret(),
            env=settings.env,
            dev_staff_key=settings.staff_key_for_dev(),
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
    )
