"""address_node_map.csv (operational, defective) and truth_service_node.csv (ground truth)."""

from __future__ import annotations

import random
from collections import defaultdict
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

from skylink_datagen.config import MAP_VERSION, WINDOW_END, DefectRates, random_date
from skylink_datagen.nodes import Topology
from skylink_datagen.writer import CsvSink

MAP_COLUMNS = ("address_id", "service_type", "node_id", "source", "valid_from", "valid_to",
               "map_version")  # fmt: skip
TRUTH_COLUMNS = ("service_id", "true_node_id", "mapping_defect")
MAP_FILE, TRUTH_FILE = "address_node_map.csv", "truth_service_node.csv"
SOURCES, SOURCE_CUM = ("inventory", "gis", "manual"), (0.80, 0.95, 1.0)
HISTORY_SHARE = 0.05
LAST_DAY = WINDOW_END.date()
MapRow = tuple[str, str, str, str, str, str, str]


class MappingWriter:
    """Decides the defect per (address, service type) and emits mapping + truth rows."""

    def __init__(self, topology: Topology, rng: random.Random, rates: DefectRates) -> None:
        self._topology = topology
        self._rng = rng
        self._cuts = (rates.missing, rates.missing + rates.ambiguous,
                      rates.missing + rates.ambiguous + rates.stale)  # fmt: skip
        decom: dict[tuple[str, str], list[int]] = defaultdict(list)
        for stype, indices in sorted(topology.decommissioned.items()):
            for i in indices:
                decom[(topology.nodes[i].city, stype)].append(i)
        self._decom_city = {k: tuple(v) for k, v in decom.items()}

    def open(self, stack: ExitStack, out: Path) -> dict[str, CsvSink]:
        return {
            MAP_FILE: stack.enter_context(CsvSink(out / MAP_FILE, MAP_COLUMNS)),
            TRUTH_FILE: stack.enter_context(CsvSink(out / TRUTH_FILE, TRUTH_COLUMNS)),
        }

    def _defect(self) -> str:
        roll = self._rng.random()
        for cut, name in zip(self._cuts, ("missing", "ambiguous", "stale"), strict=True):
            if roll < cut:
                return name
        return "none"

    def _row(self, address: str, stype: str, node: int, source: str, start: date,
             end: date | None = None) -> MapRow:  # fmt: skip
        node_id = self._topology.nodes[node].node_id
        valid_to = end.isoformat() if end else ""
        return (address, stype, node_id, source, start.isoformat(), valid_to, MAP_VERSION)

    def _source(self) -> str:
        return self._rng.choices(SOURCES, cum_weights=SOURCE_CUM)[0]

    def _other_active(self, node: int) -> int | None:
        target = self._topology.nodes[node]
        pool = self._topology.by_city[(target.city, target.service_type)]
        options = [i for i in pool if i != node]
        return self._rng.choice(options) if options else None

    def _stale_node(self, node: int) -> int:
        target = self._topology.nodes[node]
        local = self._decom_city.get((target.city, target.service_type))
        pool = local or self._topology.decommissioned[target.service_type]
        return self._rng.choice(pool)

    def _correct_rows(self, address: str, stype: str, node: int, start: date) -> list[MapRow]:
        rng = self._rng
        other = self._other_active(node)
        if other is None or rng.random() >= HISTORY_SHARE or (LAST_DAY - start).days < 60:
            return [self._row(address, stype, node, self._source(), start)]
        moved = start + timedelta(days=rng.randint(30, (LAST_DAY - start).days - 1))
        return [
            self._row(address, stype, other, "inventory", start, moved),
            self._row(address, stype, node, self._source(), moved),
        ]

    def _rows(
        self, defect: str, address: str, stype: str, node: int, start: date
    ) -> tuple[str, list[MapRow]]:
        """Return the defect actually applied (it can degrade to none) and its rows."""
        if defect == "missing":
            return defect, []
        if defect == "stale" and self._topology.decommissioned.get(stype):
            return defect, [self._row(address, stype, self._stale_node(node), "inventory", start)]
        other = self._other_active(node) if defect == "ambiguous" else None
        if other is None:
            return "none", self._correct_rows(address, stype, node, start)
        second = self._row(address, stype, other, self._rng.choice(("gis", "manual")),
                           random_date(self._rng, start, LAST_DAY))  # fmt: skip
        first = self._row(address, stype, node, "inventory", start)
        return defect, [first, second] if self._rng.random() < 0.5 else [second, first]

    def emit(self, sinks: dict[str, CsvSink], service_row: tuple[str, ...], node: int) -> None:
        """service_row follows services.csv column order."""
        sid, stype, address = service_row[0], service_row[2], service_row[6]
        start = date.fromisoformat(service_row[9])
        defect, rows = self._rows(self._defect(), address, stype, node, start)
        for row in rows:
            sinks[MAP_FILE].write(row)
        sinks[TRUTH_FILE].write((sid, self._topology.nodes[node].node_id, defect))
