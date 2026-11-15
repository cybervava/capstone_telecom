"""address_node_map.csv defects must match truth_service_node.csv and the configured rates."""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from skylink_datagen.cli import main
from tests.datagen.helpers import Dataset, read_csv


def _current(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r["valid_to"] == ""]


def test_mapping_rows_are_well_formed(data: Dataset) -> None:
    service_keys = {(s["address_id"], s["service_type"]) for s in data.services.values()}
    for key, rows in data.mapping_by_key.items():
        assert key in service_keys
        for row in rows:
            assert row["node_id"] in data.nodes
            assert data.nodes[row["node_id"]]["service_type"] == row["service_type"]
            assert row["source"] in {"inventory", "gis", "manual"}
            assert row["map_version"] == "2026-11-01"
            valid_from = date.fromisoformat(row["valid_from"])
            if row["valid_to"]:
                assert date.fromisoformat(row["valid_to"]) >= valid_from


def test_defect_shapes_agree_with_truth(data: Dataset) -> None:
    for service_id, truth in data.truth.items():
        svc = data.services[service_id]
        rows = data.mapping_by_key.get((svc["address_id"], svc["service_type"]), [])
        current = _current(rows)
        true_node = truth["true_node_id"]
        defect = truth["mapping_defect"]
        if defect == "missing":
            assert rows == []
        elif defect == "none":
            assert [r["node_id"] for r in current] == [true_node]
        elif defect == "ambiguous":
            nodes = [r["node_id"] for r in current]
            assert len(nodes) == 2
            assert len(set(nodes)) == 2
            assert true_node in nodes
            other = next(n for n in nodes if n != true_node)
            assert data.nodes[other]["status"] == "ACTIVE"
        else:
            assert defect == "stale"
            assert len(current) == 1
            mapped = current[0]["node_id"]
            assert data.nodes[mapped]["status"] == "DECOMMISSIONED"
            assert mapped != true_node


def test_historical_rows_only_on_correct_mappings(data: Dataset) -> None:
    closed = 0
    for service_id, truth in data.truth.items():
        svc = data.services[service_id]
        rows = data.mapping_by_key.get((svc["address_id"], svc["service_type"]), [])
        history = [r for r in rows if r["valid_to"]]
        closed += len(history)
        if history:
            assert truth["mapping_defect"] == "none"
    assert closed > 0


def test_default_defect_rates(data: Dataset) -> None:
    counts = Counter(t["mapping_defect"] for t in data.truth.values())
    n = len(data.truth)
    assert counts["missing"] / n == pytest.approx(0.015, abs=0.005)
    assert counts["ambiguous"] / n == pytest.approx(0.010, abs=0.004)
    assert counts["stale"] / n == pytest.approx(0.007, abs=0.0035)


def test_custom_defect_rates_are_applied(tmp_path: Path) -> None:
    argv = ["generate", "--out", str(tmp_path), "--customers", "3000", "--seed", "5"]
    rates = ["--missing-rate", "0.08", "--ambiguous-rate", "0", "--stale-rate", "0.04"]
    assert main([*argv, *rates]) == 0
    _, truth = read_csv(tmp_path / "truth_service_node.csv")
    counts = Counter(t["mapping_defect"] for t in truth)
    assert counts["ambiguous"] == 0
    assert counts["missing"] / len(truth) == pytest.approx(0.08, abs=0.015)
    assert counts["stale"] / len(truth) == pytest.approx(0.04, abs=0.012)
