"""Loading helpers shared by the datagen tests (independent of the generator's internals)."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any

DATA_FILES = (
    "nodes.csv",
    "addresses.csv",
    "customers.csv",
    "services.csv",
    "address_node_map.csv",
    "truth_service_node.csv",
    "outages.csv",
    "network_events.jsonl",
    "transcripts.jsonl",
)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, value
    return parsed


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Dataset:
    root: Path
    _cache: dict[str, Any] = field(default_factory=dict)

    def table(self, name: str) -> list[dict[str, str]]:
        if name not in self._cache:
            self._cache[name] = read_csv(self.root / name)[1]
        rows: list[dict[str, str]] = self._cache[name]
        return rows

    def header(self, name: str) -> list[str]:
        return read_csv(self.root / name)[0]

    @cached_property
    def events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.root / "network_events.jsonl")

    @cached_property
    def transcripts(self) -> list[dict[str, Any]]:
        return read_jsonl(self.root / "transcripts.jsonl")

    @cached_property
    def nodes(self) -> dict[str, dict[str, str]]:
        return {row["node_id"]: row for row in self.table("nodes.csv")}

    @cached_property
    def customers(self) -> dict[str, dict[str, str]]:
        return {row["customer_id"]: row for row in self.table("customers.csv")}

    @cached_property
    def services(self) -> dict[str, dict[str, str]]:
        return {row["service_id"]: row for row in self.table("services.csv")}

    @cached_property
    def addresses(self) -> dict[str, dict[str, str]]:
        return {row["address_id"]: row for row in self.table("addresses.csv")}

    @cached_property
    def truth(self) -> dict[str, dict[str, str]]:
        return {row["service_id"]: row for row in self.table("truth_service_node.csv")}

    @cached_property
    def outages(self) -> dict[str, dict[str, str]]:
        return {row["outage_id"]: row for row in self.table("outages.csv")}

    @cached_property
    def mapping_by_key(self) -> dict[tuple[str, str], list[dict[str, str]]]:
        grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for row in self.table("address_node_map.csv"):
            grouped[(row["address_id"], row["service_type"])].append(row)
        return dict(grouped)

    @cached_property
    def node_outage_windows(self) -> dict[str, list[tuple[datetime, datetime, str]]]:
        windows: dict[str, list[tuple[datetime, datetime, str]]] = defaultdict(list)
        for outage in self.outages.values():
            start, end = ts(outage["started_at"]), ts(outage["resolved_at"])
            for node_id in outage["affected_node_ids"].split("|"):
                windows[node_id].append((start, end, outage["outage_id"]))
        return dict(windows)
