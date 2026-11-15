"""Operator commands for the feed consumer: what they print and how they exit."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from skylink_care import cli
from skylink_care.db.database import Database
from skylink_care.db.read_model import ReadModelRepository

NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)


@pytest.fixture
def cli_on_test_database(database_conninfo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(database_conninfo=lambda: database_conninfo)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


@pytest.mark.usefixtures("cli_on_test_database")
async def test_feed_status_reports_a_healthy_consumer_and_exits_zero(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    async with db.transaction() as conn:
        await ReadModelRepository(db).record_poll(
            conn, applied_seq=12, source_last_seq=12, heartbeat_at=NOW, polled_at=NOW
        )

    code = await cli._feed_status()

    printed = capsys.readouterr().out
    assert code == 0
    assert "applied seq      12 of 12 (caught up)" in printed
    assert "gap              none" in printed
    assert "open outages     0" in printed


@pytest.mark.usefixtures("cli_on_test_database")
async def test_marked_gap_is_shown_fails_the_status_check_and_can_be_cleared(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    model = ReadModelRepository(db)
    async with db.transaction() as conn:
        await model.record_gap(conn, "expected seq 3, received 5", at=NOW)

    with_gap = await cli._feed_status()
    shown = capsys.readouterr().out
    cleared = await cli._feed_clear_gap()
    again = await cli._feed_clear_gap()
    said = capsys.readouterr().out
    after = await cli._feed_status()

    assert with_gap == 1  # usable in a script or a health check
    assert "gap              expected seq 3, received 5 (since 2026-11-15" in shown
    assert (cleared, again) == (0, 0)
    assert "gap cleared" in said
    assert "no gap was marked" in said
    assert after == 0
    assert (await model.feed_state()).intact


def test_parser_knows_the_feed_commands() -> None:
    parser = cli._parser()

    assert parser.parse_args(["feed-status"]).command == "feed-status"
    assert parser.parse_args(["feed-clear-gap"]).command == "feed-clear-gap"


@pytest.mark.usefixtures("cli_on_test_database")
async def test_reset_runtime_is_refused_outside_development(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_conninfo: str
) -> None:
    production = SimpleNamespace(env="prod", database_conninfo=lambda: database_conninfo)
    monkeypatch.setattr(cli, "get_settings", lambda: production)

    code = await cli._reset_runtime()

    assert code == 2
    assert "refused" in capsys.readouterr().err


async def test_reset_runtime_forgets_the_old_gateway_state_and_keeps_what_must_last(
    db: Database,
    database_conninfo: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    development = SimpleNamespace(env="dev", database_conninfo=lambda: database_conninfo)
    monkeypatch.setattr(cli, "get_settings", lambda: development)
    model = ReadModelRepository(db)
    async with db.transaction() as conn:
        await model.record_poll(
            conn, applied_seq=40, source_last_seq=40, heartbeat_at=NOW, polled_at=NOW
        )
        await model.record_gap(conn, "expected seq 3, received 5", at=NOW)
        await conn.execute(
            "INSERT INTO outages (outage_id, version, cause, planned, started_at, last_event_seq)"
            " VALUES ('OUT-1', 1, 'FIBRE_CUT', false, %s, 1)",
            (NOW,),
        )
        await conn.execute(
            "INSERT INTO nodes (node_id, node_type, service_type, city, area, pincode, status)"
            " VALUES ('BLR-OLT-0001', 'OLT', 'broadband', 'BLR', 'A', '560001', 'ACTIVE')"
        )
        cursor = await conn.execute("SELECT nextval('complaint_ref_seq') AS seq")
        before = (await cursor.fetchone() or {})["seq"]

    code = await cli._reset_runtime()

    assert code == 0
    assert "runtime state cleared" in capsys.readouterr().out
    state = await model.feed_state()
    assert (state.applied_seq, state.heartbeat_at, state.intact) == (0, None, True)
    assert await model.get_outage("OUT-1") is None
    async with db.connection() as conn:
        cursor = await conn.execute("SELECT count(*) AS n FROM nodes")
        assert (await cursor.fetchone() or {})["n"] == 1  # reference data has its own loader
        cursor = await conn.execute("SELECT nextval('complaint_ref_seq') AS seq")
        # A complaint reference is never issued twice, reset or not.
        assert (await cursor.fetchone() or {})["seq"] == before + 1
