"""outages.csv and network_events.jsonl rules."""

from __future__ import annotations

import itertools
import re
import statistics
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any

import pytest

from skylink_datagen.config import derive_rng
from skylink_datagen.network import outage_count, sample_unplanned_minutes
from tests.datagen.helpers import Dataset, ts

WINDOW = (ts("2026-05-01T00:00:00+05:30"), ts("2026-10-31T23:59:59+05:30"))
ROOT_TYPE = {"FIBRE_CUT": {"AGG"}, "POWER_FAILURE": {"AGG"}, "EQUIPMENT_FAULT": {"OLT"},
             "TOWER_FAULT": {"TOWER"}, "PLANNED_MAINTENANCE": {"AGG", "OLT", "TOWER"}}  # fmt: skip
OUTAGE_EVENTS = {"OUTAGE_STARTED", "OUTAGE_UPDATED", "OUTAGE_RESOLVED"}
ALARM_EVENTS = {"ALARM_RAISED", "ALARM_CLEARED"}


def _active_children(data: Dataset, agg: str) -> set[str]:
    return {
        node_id
        for node_id, node in data.nodes.items()
        if node["parent_node_id"] == agg and node["status"] == "ACTIVE"
    }


def test_outage_ids_and_roots(data: Dataset) -> None:
    for outage_id, outage in data.outages.items():
        started = ts(outage["started_at"])
        assert re.fullmatch(rf"OUT-{started:%Y%m%d}-\d{{4}}", outage_id)
        root = data.nodes[outage["root_node_id"]]
        assert root["status"] == "ACTIVE"
        assert root["node_type"] in ROOT_TYPE[outage["cause"]]
        affected = outage["affected_node_ids"].split("|")
        if root["node_type"] == "AGG":
            assert set(affected) == _active_children(data, outage["root_node_id"])
        else:
            assert affected == [outage["root_node_id"]]
        types = {data.nodes[n]["service_type"] for n in affected}
        assert set(outage["service_types"].split("|")) == types


def test_outage_timing_rules(data: Dataset) -> None:
    for outage in data.outages.values():
        started, detected = ts(outage["started_at"]), ts(outage["detected_at"])
        resolved, eta = ts(outage["resolved_at"]), ts(outage["initial_eta"])
        assert WINDOW[0] <= started
        assert resolved <= WINDOW[1]
        assert int(outage["duration_minutes"]) * 60 == (resolved - started).total_seconds()
        assert eta > detected
        assert resolved > detected
        planned = outage["planned"] == "true"
        assert outage["planned"] in {"true", "false"}
        assert planned == (outage["cause"] == "PLANNED_MAINTENANCE")
        if planned:
            assert detected == started
            assert 60 <= int(outage["duration_minutes"]) <= 240
            assert started.time() >= time(1, 0)
            assert resolved.date() == started.date()
            assert resolved.time() <= time(5, 0)
        else:
            assert timedelta(minutes=1) <= detected - started <= timedelta(minutes=10)


def test_planned_share(data: Dataset) -> None:
    planned = sum(o["planned"] == "true" for o in data.outages.values())
    assert planned / len(data.outages) == pytest.approx(0.10, abs=0.03)


def test_full_scale_outage_volume_and_durations() -> None:
    assert outage_count(400_000) / 184 == pytest.approx(10, rel=0.05)
    rng = derive_rng(1, "durations")
    samples = [sample_unplanned_minutes(rng) for _ in range(20_000)]
    assert statistics.median(samples) == pytest.approx(120, rel=0.08)
    assert any(s > 48 * 60 for s in samples)


def test_event_stream_ordering_and_ids(data: Dataset) -> None:
    events = data.events
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    emitted = [ts(e["emitted_at"]) for e in events]
    assert emitted == sorted(emitted)
    ids = [e["event_id"] for e in events]
    assert all(re.fullmatch(r"evt_[0-9a-f]{16}", i) for i in ids)
    assert len(set(ids)) == len(ids)
    for event in events:
        assert event["type"] in OUTAGE_EVENTS | ALARM_EVENTS
        assert isinstance(event["node_ids"], list)
        assert event["node_ids"]
        assert all(n in data.nodes for n in event["node_ids"])


def _by_outage(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["type"] in OUTAGE_EVENTS:
            grouped[event["outage_id"]].append(event)
    return grouped


def test_outage_lifecycle_events(data: Dataset) -> None:
    grouped = _by_outage(data.events)
    assert set(grouped) == set(data.outages)
    for outage_id, events in grouped.items():
        outage = data.outages[outage_id]
        types = [e["type"] for e in events]
        assert types[0] == "OUTAGE_STARTED"
        assert types[-1] == "OUTAGE_RESOLVED"
        assert set(types[1:-1]) <= {"OUTAGE_UPDATED"}
        assert len(types) - 2 <= 3
        assert [e["version"] for e in events] == list(range(1, len(events) + 1))
        assert ts(events[0]["emitted_at"]) == ts(outage["detected_at"])
        assert ts(events[0]["eta"]) == ts(outage["initial_eta"])
        assert ts(events[-1]["emitted_at"]) == ts(outage["resolved_at"])
        assert ts(events[-1]["resolved_at"]) == ts(outage["resolved_at"])
        for event in events:
            assert event["node_ids"] == outage["affected_node_ids"].split("|")
            assert event["service_types"] == outage["service_types"].split("|")
            assert event["cause"] == outage["cause"]
            assert event["planned"] == (outage["planned"] == "true")
            assert ts(event["started_at"]) == ts(outage["started_at"])
            assert event["eta_source"] in {"noc_estimate", "field_update"}
            if event["type"] != "OUTAGE_RESOLVED":
                assert event["resolved_at"] is None


def test_updates_change_eta(data: Dataset) -> None:
    updates = 0
    for events in _by_outage(data.events).values():
        for before, after in itertools.pairwise(events):
            if after["type"] == "OUTAGE_UPDATED":
                updates += 1
                assert after["eta"] != before["eta"]
    assert updates > 0


def test_every_outage_preceded_by_critical_root_alarm(data: Dataset) -> None:
    started_seq = {e["outage_id"]: e["seq"] for e in data.events if e["type"] == "OUTAGE_STARTED"}
    raised = [e for e in data.events if e["type"] == "ALARM_RAISED"]
    for outage_id, outage in data.outages.items():
        start: datetime = ts(outage["started_at"])
        assert any(
            a["severity"] == "critical"
            and a["node_ids"] == [outage["root_node_id"]]
            and a["seq"] < started_seq[outage_id]
            and start <= ts(a["emitted_at"]) <= ts(outage["detected_at"])
            for a in raised
        ), outage_id


def test_alarm_fields_pairing_and_volume(data: Dataset) -> None:
    raised = {e["alarm_id"]: e for e in data.events if e["type"] == "ALARM_RAISED"}
    cleared = {e["alarm_id"]: e for e in data.events if e["type"] == "ALARM_CLEARED"}
    assert set(raised) == set(cleared)
    for alarm_id, event in raised.items():
        assert event["alarm_type"] in {"LOS", "LINK_DOWN", "HIGH_BER", "POWER", "HIGH_TEMP"}
        assert event["severity"] in {"critical", "major", "minor"}
        assert cleared[alarm_id]["seq"] > event["seq"]
        assert cleared[alarm_id]["node_ids"] == event["node_ids"]
    n = len(data.outages)
    assert 4 * n <= len(raised) <= 8 * n
