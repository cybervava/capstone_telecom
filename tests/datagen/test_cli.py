"""CLI exit codes, determinism and manifest content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skylink_datagen.cli import main
from tests.datagen.conftest import SEED, SMALL_CUSTOMERS
from tests.datagen.helpers import DATA_FILES, read_csv, read_jsonl, sha256

DETERMINISM_CUSTOMERS = 3000


def _generate(out: Path, seed: int, *extra: str) -> int:
    argv = ["generate", "--out", str(out), "--customers", str(DETERMINISM_CUSTOMERS)]
    return main([*argv, "--seed", str(seed), *extra])


@pytest.fixture(scope="module")
def twin_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    first, second, other = (tmp_path_factory.mktemp(name) for name in ("a", "b", "c"))
    assert _generate(first, 7) == 0
    assert _generate(second, 7) == 0
    assert _generate(other, 8) == 0
    return first, second, other


def test_generate_writes_every_contract_file(dataset_dir: Path) -> None:
    for name in (*DATA_FILES, "manifest.json"):
        assert (dataset_dir / name).is_file(), name


def test_same_seed_produces_byte_identical_files(twin_runs: tuple[Path, Path, Path]) -> None:
    first, second, _ = twin_runs
    for name in (*DATA_FILES, "manifest.json"):
        assert sha256(first / name) == sha256(second / name), name


def test_different_seed_changes_every_file(twin_runs: tuple[Path, Path, Path]) -> None:
    first, _, other = twin_runs
    for name in DATA_FILES:
        assert sha256(first / name) != sha256(other / name), name


def test_knowledge_is_deterministic(tmp_path: Path, knowledge_dir: Path) -> None:
    assert main(["knowledge", "--out", str(tmp_path), "--seed", str(SEED)]) == 0
    produced = sorted(p.relative_to(knowledge_dir) for p in knowledge_dir.rglob("*") if p.is_file())
    assert produced == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    for rel in produced:
        assert sha256(knowledge_dir / rel) == sha256(tmp_path / rel), rel


def test_manifest_records_rows_hashes_rates_and_window(dataset_dir: Path) -> None:
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == SEED
    assert manifest["customers"] == SMALL_CUSTOMERS
    assert isinstance(manifest["generator_version"], str)
    assert manifest["generator_version"]
    assert manifest["defect_rates"] == {"missing": 0.015, "ambiguous": 0.01, "stale": 0.007}
    assert manifest["history_window"] == {
        "start": "2026-05-01T00:00:00+05:30",
        "end": "2026-10-31T23:59:59+05:30",
    }
    assert sorted(manifest["files"]) == sorted(DATA_FILES)
    for name, entry in manifest["files"].items():
        path = dataset_dir / name
        rows = len(read_jsonl(path)) if name.endswith(".jsonl") else len(read_csv(path)[1])
        assert entry == {"rows": rows, "sha256": sha256(path)}, name


def test_manifest_records_custom_rates(tmp_path: Path) -> None:
    rates = ["--missing-rate", "0.05", "--ambiguous-rate", "0.02", "--stale-rate", "0"]
    assert _generate(tmp_path, 1, *rates) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["defect_rates"] == {"missing": 0.05, "ambiguous": 0.02, "stale": 0.0}


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["explode"],
        ["generate", "--customers", "0"],
        ["generate", "--customers", "abc"],
        ["generate", "--customers", "10"],
        ["generate", "--missing-rate", "1.5"],
        ["generate", "--stale-rate", "-0.1"],
        ["generate", "--ambiguous-rate", "nan"],
        ["generate", "--seed", "x"],
        ["knowledge", "--seed", "x"],
    ],
)
def test_invalid_arguments_exit_non_zero_with_message(
    argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    full = (
        [*argv, "--out", str(tmp_path)] if argv and argv[0] in {"generate", "knowledge"} else argv
    )
    assert main(full) == 2
    assert capsys.readouterr().err.strip()
    assert not any(tmp_path.iterdir())


def test_unwritable_output_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    assert main(["generate", "--out", str(blocker), "--customers", "1000"]) == 1
    assert "error" in capsys.readouterr().err.lower()


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    assert "generate" in capsys.readouterr().out
