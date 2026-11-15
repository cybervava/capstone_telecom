"""``care-admin``: operator commands for the care platform."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from skylink_care.config import get_settings
from skylink_care.db.database import Database
from skylink_care.db.migrate import migrate, reset_runtime
from skylink_care.db.read_model import ReadModelRepository
from skylink_care.pipelines.profile import build_profile, render_markdown
from skylink_care.pipelines.reference_loader import DataPackError, load_reference


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="care-admin", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations")
    load = commands.add_parser("load-reference", help="load nodes, mapping and service index")
    load.add_argument("--data", type=Path, required=True, help="data pack directory")
    profile = commands.add_parser("profile", help="write the mapping-quality profile")
    profile.add_argument("--out", type=Path, default=Path("reports/data_profile.md"))
    profile.add_argument("--source", default="data/generated", help="label for the report")
    commands.add_parser(
        "reset-runtime",
        help="development only: forget contacts, actions and network state after a gateway re-seed",
    )
    commands.add_parser("feed-status", help="position and health of the NOC feed consumer")
    commands.add_parser(
        "feed-clear-gap",
        help="trust the outage read model again after a lost feed event was reconciled",
    )
    return parser


async def _migrate() -> int:
    applied = await migrate(get_settings().database_conninfo())
    sys.stdout.write(f"migrations applied: {', '.join(applied) or 'none (already current)'}\n")
    return 0


async def _load_reference(data: Path) -> int:
    database = await Database.open(get_settings().database_conninfo(), max_size=2)
    try:
        stats = await load_reference(database, data)
    except DataPackError as exc:
        sys.stderr.write(f"data pack rejected: {exc}\n")
        return 2
    finally:
        await database.close()
    sys.stdout.write(
        f"loaded {stats.nodes:,} nodes, {stats.mappings:,} mapping rows,"
        f" {stats.services:,} services in {stats.seconds}s\n"
    )
    return 0


async def _profile(source: str) -> str:
    database = await Database.open(get_settings().database_conninfo(), max_size=2)
    try:
        profile = await build_profile(database)
    finally:
        await database.close()
    return render_markdown(profile, generated_at=datetime.now(UTC), source=source)


async def _reset_runtime() -> int:
    settings = get_settings()
    if settings.env == "prod":
        # Fails closed: CARE_ENV is prod unless it was set to dev or test on purpose.
        sys.stderr.write("refused: reset-runtime only runs with CARE_ENV=dev or CARE_ENV=test\n")
        return 2
    await reset_runtime(settings.database_conninfo())
    sys.stdout.write("runtime state cleared (audit log, reference data and sequences kept)\n")
    return 0


async def _feed_status() -> int:
    database = await Database.open(get_settings().database_conninfo(), max_size=2)
    try:
        model = ReadModelRepository(database)
        state, stats = await model.feed_state(), await model.stats()
    finally:
        await database.close()
    lines = [
        f"applied seq      {state.applied_seq} of {state.source_last_seq}"
        f" ({'caught up' if state.caught_up else 'behind'})",
        f"heartbeat        {state.heartbeat_at or 'never'}",
        f"last error       {state.last_error or 'none'}",
        f"gap              {state.gap_detail or 'none'}"
        + (f" (since {state.gap_at})" if state.gap_at else ""),
        f"open outages     {stats.get('open_outages', 0)}",
        f"active alarms    {stats.get('active_alarms', 0)}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    # A gap means the read model is not being used as evidence: worth a non-zero exit in scripts.
    return 0 if state.intact else 1


async def _feed_clear_gap() -> int:
    """Only after the open outages in the read model were compared with the NOC's own list."""
    database = await Database.open(get_settings().database_conninfo(), max_size=2)
    try:
        cleared = await ReadModelRepository(database).clear_gap()
    finally:
        await database.close()
    sys.stdout.write(
        "gap cleared: the read model counts as evidence again\n"
        if cleared
        else "no gap was marked\n"
    )
    return 0


def _write_profile(out: Path, source: str) -> int:
    report = asyncio.run(_profile(source))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    sys.stdout.write(f"wrote {out}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "migrate":
        return asyncio.run(_migrate())
    if args.command == "load-reference":
        return asyncio.run(_load_reference(args.data))
    if args.command == "reset-runtime":
        return asyncio.run(_reset_runtime())
    if args.command == "feed-status":
        return asyncio.run(_feed_status())
    if args.command == "feed-clear-gap":
        return asyncio.run(_feed_clear_gap())
    return _write_profile(args.out, args.source)


if __name__ == "__main__":
    raise SystemExit(main())
