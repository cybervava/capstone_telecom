"""Fixtures for tests that need real PostgreSQL (registered as a plugin in tests/conftest.py).

A throwaway database is created once per test session from the compose instance, migrated, and
dropped afterwards. Start the instance with ``make up``. Set ``CARE_REQUIRE_DB=1`` (as CI does)
to turn "database not reachable" from a skip into a failure.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, OperationalError, sql

from skylink_care.config import Settings
from skylink_care.db.actions import ActionRepository
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.episodes import EpisodeRepository
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.messages import MessageRepository
from skylink_care.db.migrate import migrate
from skylink_care.db.reference import ReferenceRepository

_MUTABLE_TABLES = (
    "approvals, actions, handoffs, inbox, contact_messages, contacts, node_episodes,"
    " address_node_map, service_index, nodes"
)


@pytest.fixture(scope="session")
async def database_conninfo() -> AsyncIterator[str]:
    settings = Settings(_env_file=".env")  # type: ignore[call-arg]
    admin = settings.database_conninfo(dbname="postgres")
    name = f"care_test_{uuid4().hex[:12]}"
    try:
        async with await AsyncConnection.connect(admin, autocommit=True, connect_timeout=3) as conn:
            await conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except OperationalError as exc:
        if os.environ.get("CARE_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"PostgreSQL not reachable ({exc}); run `make up`")
    conninfo = settings.database_conninfo(dbname=name)
    await migrate(conninfo)
    yield conninfo
    async with await AsyncConnection.connect(admin, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
async def db(database_conninfo: str) -> AsyncIterator[Database]:
    database = await Database.open(database_conninfo, min_size=2, max_size=20)
    async with database.connection() as conn:
        await conn.execute(f"TRUNCATE {_MUTABLE_TABLES} CASCADE")
    yield database
    await database.close()


@pytest.fixture
def contacts(db: Database) -> ContactRepository:
    return ContactRepository(db)


@pytest.fixture
def inbox(db: Database) -> InboxRepository:
    return InboxRepository(db)


@pytest.fixture
def messages(db: Database) -> MessageRepository:
    return MessageRepository(db)


@pytest.fixture
def episodes(db: Database) -> EpisodeRepository:
    return EpisodeRepository(db)


@pytest.fixture
def actions(db: Database) -> ActionRepository:
    return ActionRepository(db)


@pytest.fixture
def audit(db: Database) -> AuditRepository:
    return AuditRepository(db)


@pytest.fixture
def handoffs(db: Database) -> HandoffRepository:
    return HandoffRepository(db)


@pytest.fixture
def reference(db: Database) -> ReferenceRepository:
    return ReferenceRepository(db)
