"""``care-admin``: operator commands for the care platform."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from skylink_care.config import get_settings
from skylink_care.db.database import Database
from skylink_care.db.migrate import migrate
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
    return _write_profile(args.out, args.source)


if __name__ == "__main__":
    raise SystemExit(main())
