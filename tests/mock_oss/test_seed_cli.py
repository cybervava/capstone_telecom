"""Seeding from CSV files and the command line."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mock_oss import main as main_module
from mock_oss.config import Settings
from mock_oss.main import create_app, run
from mock_oss.seed import SeedError, is_line_faulted, seed_database
from tests.mock_oss.conftest import auth, write_data_pack


def test_seed_cli_and_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = write_data_pack(tmp_path / "pack")
    db = tmp_path / "cli.sqlite"
    assert run(["seed", "--data", str(data), "--db", str(db), "--seed", "7"]) == 0
    assert run(["seed", "--data", str(data), "--db", str(db)]) == 0
    monkeypatch.setenv("MOCK_OSS_DB", str(db))
    settings = Settings.from_env()
    with TestClient(create_app(settings)) as c:
        assert c.get("/health").json()["customers"] == 12


def test_seed_uses_env_db_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = write_data_pack(tmp_path / "pack")
    db = tmp_path / "nested" / "env.sqlite"
    monkeypatch.setenv("MOCK_OSS_DB", str(db))
    assert run(["seed", "--data", str(data)]) == 0
    assert db.exists()


def test_reseed_clears_runtime_state(settings: Settings, data_dir: Path) -> None:
    seed_database(data_dir, settings.db_path, seed=42, line_fault_rate=0.0)
    with TestClient(create_app(settings)) as c:
        c.post(
            "/admin/v1/alarms",
            json={"node_id": "BLR-OLT-0001", "alarm_type": "LOS", "severity": "major"},
            headers=auth("admin"),
        )
        assert c.get("/health").json()["last_event_seq"] == 1
    seed_database(data_dir, settings.db_path, seed=42, line_fault_rate=0.0)
    with TestClient(create_app(settings)) as c:
        status = c.get("/network/v1/nodes/BLR-OLT-0001/status", headers=auth("admin")).json()
        assert status["active_alarms"] == []


def test_seed_missing_file_fails(tmp_path: Path) -> None:
    data = write_data_pack(tmp_path / "pack")
    (data / "services.csv").unlink()
    with pytest.raises(SeedError, match=r"services\.csv"):
        seed_database(data, tmp_path / "x.sqlite", seed=1)
    assert run(["seed", "--data", str(data), "--db", str(tmp_path / "y.sqlite")]) == 2


def test_seed_bad_column_fails(tmp_path: Path) -> None:
    data = write_data_pack(tmp_path / "pack")
    (data / "nodes.csv").write_text("node_id,oops\nX,Y\n", encoding="utf-8")
    with pytest.raises(SeedError, match=r"nodes\.csv"):
        seed_database(data, tmp_path / "x.sqlite", seed=1)


def test_seed_without_address_map(tmp_path: Path, settings: Settings) -> None:
    data = write_data_pack(tmp_path / "pack")
    (data / "address_node_map.csv").unlink()
    seed_database(data, settings.db_path, seed=1, line_fault_rate=0.0)
    with TestClient(create_app(settings)) as c:
        rows = c.get("/admin/v1/customers/search?limit=1", headers=auth("admin")).json()
        assert rows["customers"][0]["mapped_node_ids"] == []


def test_line_fault_selection_is_deterministic_and_rate_bound() -> None:
    ids = [f"S{i:07d}" for i in range(1, 50001)]
    a = {s for s in ids if is_line_faulted(s, seed=42, rate=0.012)}
    b = {s for s in ids if is_line_faulted(s, seed=42, rate=0.012)}
    c = {s for s in ids if is_line_faulted(s, seed=43, rate=0.012)}
    assert a == b
    assert a != c
    assert 0.010 < len(a) / len(ids) < 0.014
    assert not any(is_line_faulted(s, seed=42, rate=0.0) for s in ids[:1000])


def test_seeded_line_faults_only_broadband(tmp_path: Path, settings: Settings) -> None:
    data = write_data_pack(tmp_path / "pack")
    seed_database(data, settings.db_path, seed=42, line_fault_rate=1.0)
    with TestClient(create_app(settings)) as c:

        def result(service_id: str) -> Any:
            return c.post(
                "/network/v1/line-tests", json={"service_id": service_id}, headers=auth("admin")
            ).json()["result"]

        assert result("S0000001") == "FAIL"
        assert result("S0000008") == "PASS"
        c.post(
            "/admin/v1/line-faults",
            json={"service_id": "S0000001", "fault": False},
            headers=auth("admin"),
        )
        assert result("S0000001") == "PASS"
        c.post("/admin/v1/reset", json={}, headers=auth("admin"))
        assert result("S0000001") == "FAIL"


def _write_bulk(directory: Path, n: int) -> Path:
    write_data_pack(directory)
    with (directory / "customers.csv").open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        for i in range(100, n):
            w.writerow(
                [
                    f"C{i:07d}",
                    "A B",
                    f"+917{i:09d}",
                    f"a.b{i}@example.com",
                    "consumer",
                    "active",
                    "2024-01-01",
                    "true",
                    "sms",
                    "2026-08-02T15:30:00+05:30",
                ]
            )
    with (directory / "services.csv").open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        for i in range(100, n):
            w.writerow(
                [
                    f"S{i:07d}",
                    f"C{i:07d}",
                    "broadband",
                    "Fibre 100",
                    "599.00",
                    "active",
                    "A0000001",
                    "AL-AX18",
                    "SN",
                    "2024-01-01",
                    "3",
                ]
            )
    with (directory / "truth_service_node.csv").open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        for i in range(100, n):
            w.writerow([f"S{i:07d}", "BLR-OLT-0001", "none"])
    return directory


def test_seed_throughput(tmp_path: Path) -> None:
    data = _write_bulk(tmp_path / "bulk", 40_000)
    started = time.monotonic()
    seed_database(data, tmp_path / "bulk.sqlite", seed=42)
    assert time.monotonic() - started < 6  # 400k must be well under 60 s


def test_serve_is_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(app: object, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)
    monkeypatch.setenv("MOCK_OSS_DB", str(tmp_path / "s.sqlite"))
    monkeypatch.setenv("MOCK_OSS_PORT", "9123")
    assert run([]) == 0
    assert run(["serve"]) == 0
    assert [c["port"] for c in calls] == [9123, 9123]
    assert calls[0]["host"] == "127.0.0.1"


def test_serve_bad_env_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_OSS_PORT", "nope")
    assert run(["serve"]) == 2
