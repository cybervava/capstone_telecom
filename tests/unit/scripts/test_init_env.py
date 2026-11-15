"""`make init`: a fresh .env gets generated secrets; an existing one is only ever added to."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "init_env.py"
TEMPLATE = """# comment with =change-me in it
CARE_ENV=dev
CARE_JWT_SECRET=change-me
CARE_DEV_STAFF_KEY=change-me
CARE_DB_PORT=55432
"""


@pytest.fixture
def init_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("init_env_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / ".env.example").write_text(TEMPLATE, encoding="utf-8")
    return module


def values(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def test_fresh_file_gets_a_different_generated_value_for_each_secret(
    init_env: ModuleType, tmp_path: Path
) -> None:
    assert init_env.main() == 0

    made = values(tmp_path / ".env")
    assert made["CARE_ENV"] == "dev"
    assert "change-me" not in (made["CARE_JWT_SECRET"], made["CARE_DEV_STAFF_KEY"])
    assert made["CARE_JWT_SECRET"] != made["CARE_DEV_STAFF_KEY"]
    assert len(made["CARE_DEV_STAFF_KEY"]) >= 24
    assert (tmp_path / ".env").stat().st_mode & 0o077 == 0  # owner only


def test_existing_file_keeps_every_value_and_gains_only_the_secret_it_lacked(
    init_env: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = "CARE_ENV=dev\nCARE_JWT_SECRET=mine-and-not-to-be-touched\nEXTRA=kept\n"
    (tmp_path / ".env").write_text(before, encoding="utf-8")

    assert init_env.main() == 0

    after = (tmp_path / ".env").read_text(encoding="utf-8")
    assert after.startswith(before)
    made = values(tmp_path / ".env")
    assert made["CARE_JWT_SECRET"] == "mine-and-not-to-be-touched"
    assert made["EXTRA"] == "kept"
    assert len(made["CARE_DEV_STAFF_KEY"]) >= 24
    printed = capsys.readouterr().out
    assert "CARE_DEV_STAFF_KEY" in printed
    assert made["CARE_DEV_STAFF_KEY"] not in printed  # the name is said, never the value


def test_complete_file_is_left_exactly_as_it_is(init_env: ModuleType, tmp_path: Path) -> None:
    complete = "CARE_JWT_SECRET=a\nCARE_DEV_STAFF_KEY=\n"  # set, even if set to nothing
    (tmp_path / ".env").write_text(complete, encoding="utf-8")

    assert init_env.main() == 0

    assert (tmp_path / ".env").read_text(encoding="utf-8") == complete


def test_value_set_with_export_counts_as_set(init_env: ModuleType, tmp_path: Path) -> None:
    mine = "export CARE_JWT_SECRET=a\nexport CARE_DEV_STAFF_KEY=chosen-by-hand-on-purpose\n"
    (tmp_path / ".env").write_text(mine, encoding="utf-8")

    assert init_env.main() == 0

    assert (tmp_path / ".env").read_text(encoding="utf-8") == mine
