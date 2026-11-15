"""Reference load and the mapping-quality profile, on a generated data pack."""

from __future__ import annotations

import csv
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from skylink_care.db.database import Database
from skylink_care.pipelines.profile import build_profile, render_markdown
from skylink_care.pipelines.reference_loader import DataPackError, check_data_pack, load_reference


def truth_counts(datapack: Path) -> dict[tuple[str, str], int]:
    """Ground truth from the simulator's file. Used by the test only, never by the platform."""
    service_types = {}
    with (datapack / "services.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            service_types[row["service_id"]] = row["service_type"]
    counts: dict[tuple[str, str], int] = {}
    with (datapack / "truth_service_node.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (service_types[row["service_id"]], row["mapping_defect"])
            counts[key] = counts.get(key, 0) + 1
    return counts


async def test_load_brings_in_every_node_mapping_row_and_service(
    db: Database, datapack: Path
) -> None:
    stats = await load_reference(db, datapack)

    def rows(name: str) -> int:
        with (datapack / name).open(encoding="utf-8") as handle:
            return sum(1 for _ in handle) - 1

    assert stats.nodes == rows("nodes.csv")
    assert stats.mappings == rows("address_node_map.csv")
    assert stats.services == rows("services.csv")


async def test_load_keeps_identifiers_only(db: Database, datapack: Path) -> None:
    await load_reference(db, datapack)

    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'service_index' ORDER BY ordinal_position"
        )
        columns = [row["column_name"] for row in await cursor.fetchall()]

    assert columns == ["service_id", "customer_id", "service_type", "address_id", "status"]


async def test_reloading_replaces_rather_than_duplicates(db: Database, datapack: Path) -> None:
    first = await load_reference(db, datapack)
    second = await load_reference(db, datapack)

    async with db.connection() as conn:
        cursor = await conn.execute("SELECT count(*) AS n FROM service_index")
        row = await cursor.fetchone()
    assert row is not None
    assert row["n"] == first.services == second.services


async def test_profile_finds_exactly_the_defects_the_generator_injected(
    db: Database, datapack: Path
) -> None:
    await load_reference(db, datapack)
    truth = truth_counts(datapack)

    profile = await build_profile(db)

    for quality in profile.quality:
        kind = quality.service_type
        assert quality.missing == truth.get((kind, "missing"), 0)
        assert quality.ambiguous == truth.get((kind, "ambiguous"), 0)
        assert quality.stale == truth.get((kind, "stale"), 0)
        assert quality.resolved == truth.get((kind, "none"), 0)
        assert quality.services == sum(n for (k, _), n in truth.items() if k == kind)
        assert 0.9 < quality.coverage < 1.0


async def test_profile_report_states_coverage_per_service_type(
    db: Database, datapack: Path
) -> None:
    await load_reference(db, datapack)

    report = render_markdown(
        await build_profile(db),
        generated_at=datetime(2026, 11, 15, 12, 0, tzinfo=UTC),
        source="test pack",
    )

    assert "| broadband |" in report
    assert "| mobile |" in report
    assert "Generated 2026-11-15 12:00 UTC from `test pack`" in report
    assert "%" in report


def test_missing_file_is_rejected_before_the_database_is_touched(tmp_path: Path) -> None:
    with pytest.raises(DataPackError, match=r"missing file: .*nodes\.csv"):
        check_data_pack(tmp_path)


def test_unexpected_columns_are_rejected_with_the_expected_list(
    datapack: Path, tmp_path: Path
) -> None:
    for name in ("nodes.csv", "address_node_map.csv", "services.csv"):
        shutil.copy(datapack / name, tmp_path / name)
    text = (tmp_path / "nodes.csv").read_text(encoding="utf-8")
    (tmp_path / "nodes.csv").write_text(text.replace("node_id,", "id,", 1), encoding="utf-8")

    with pytest.raises(DataPackError, match=r"nodes\.csv: expected columns node_id"):
        check_data_pack(tmp_path)


def test_the_platform_never_reads_the_simulators_ground_truth() -> None:
    """The mapping the platform works from is imperfect on purpose; the truth file is off limits."""
    source_root = Path(__file__).resolve().parents[2] / "src" / "skylink_care"
    offenders = [
        str(path)
        for path in source_root.rglob("*.py")
        if "truth_service_node" in path.read_text(encoding="utf-8")
        or "customers.csv" in path.read_text(encoding="utf-8").replace("``customers.csv``", "")
    ]

    assert offenders == []
