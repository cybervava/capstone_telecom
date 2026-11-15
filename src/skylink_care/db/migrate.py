"""Apply the plain-SQL migrations in ``db/migrations`` in file-name order, exactly once each.

Each file runs in its own transaction together with its ``schema_migrations`` row. An advisory
lock makes concurrent starts safe. The LangGraph checkpointer manages its own tables through
``setup()``, which is idempotent.
"""

from __future__ import annotations

from importlib import resources

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

_MIGRATION_LOCK = 7_402_115  # arbitrary constant, unique to this application


def migration_files() -> list[tuple[str, str]]:
    """Return ``(version, sql)`` for every migration, ordered by file name."""
    folder = resources.files("skylink_care.db") / "migrations"
    files = sorted(
        (entry for entry in folder.iterdir() if entry.name.endswith(".sql")),
        key=lambda entry: entry.name,
    )
    return [(entry.name.removesuffix(".sql"), entry.read_text(encoding="utf-8")) for entry in files]


async def migrate(conninfo: str) -> list[str]:
    """Bring the database to the latest schema. Returns the versions applied by this call."""
    applied_now: list[str] = []
    async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cursor = await conn.execute("SELECT version FROM schema_migrations")
            done = {row[0] for row in await cursor.fetchall()}
            for version, sql in migration_files():
                if version in done:
                    continue
                async with conn.transaction():
                    await conn.execute(sql.encode("utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                    )
                applied_now.append(version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    await _setup_checkpointer(conninfo)
    return applied_now


# Everything the platform has learnt from, or sent to, one particular gateway: conversations,
# proposed and executed writes, the outage read model and workflow checkpoints. The audit log is
# append-only and is kept; reference data is reloaded by its own loader; the complaint reference
# sequence is kept, so a reference is never issued twice.
_RUNTIME_TABLES = (
    "approvals, actions, handoffs, inbox, timers, line_tests, contact_messages, contacts,"
    " node_episodes, outage_nodes, outages, node_alarms, checkpoint_blobs, checkpoint_writes,"
    " checkpoints"
)


async def reset_runtime(conninfo: str) -> None:
    """Forget everything tied to the gateway's previous state. For development and demos only.

    Re-seeding the mock gateway restarts its event sequence and empties its tickets. A care
    database that kept its feed cursor and its ticket references would then refuse the feed
    (the sequence ran backwards) and write to tickets that no longer exist.
    """
    async with (
        await AsyncConnection.connect(conninfo, autocommit=True) as conn,
        conn.transaction(),
    ):
        await conn.execute(f"TRUNCATE {_RUNTIME_TABLES} CASCADE")
        await conn.execute(
            "UPDATE feed_state SET applied_seq = 0, source_last_seq = 0, heartbeat_at = NULL,"
            " polled_at = NULL, last_error = NULL, last_error_at = NULL, gap_at = NULL,"
            " gap_detail = NULL"
        )


async def _setup_checkpointer(conninfo: str) -> None:
    async with await AsyncConnection.connect(
        conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        await AsyncPostgresSaver(conn).setup()
