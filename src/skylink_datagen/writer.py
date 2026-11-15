"""Streaming CSV/JSONL writers, file hashing and the manifest."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from skylink_datagen.config import GENERATOR_VERSION, WINDOW_END, WINDOW_START, fmt_ts

CHUNK = 1 << 20


class CsvSink:
    """Context-managed CSV writer that counts data rows (header excluded)."""

    def __init__(self, path: Path, header: Iterable[str]) -> None:
        self._path = path
        self._header = tuple(header)
        self._handle: IO[str] | None = None
        self._writer: Any = None
        self.rows = 0

    def __enter__(self) -> Self:
        self._handle = self._path.open("w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._handle, lineterminator="\n")
        self._writer.writerow(self._header)
        return self

    def write(self, row: Iterable[object]) -> None:
        self._writer.writerow(row)
        self.rows += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()


def write_csv(path: Path, header: Iterable[str], rows: Iterable[Iterable[object]]) -> int:
    with CsvSink(path, header) as sink:
        for row in rows:
            sink.write(row)
        return sink.rows


def to_json_line(record: Mapping[str, object]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_jsonl(path: Path, records: Iterable[Mapping[str, object]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(to_json_line(record))
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build_manifest(
    out: Path, seed: int, customers: int, rates: Mapping[str, float], rows: Mapping[str, int]
) -> dict[str, Any]:
    files = {
        name: {"rows": count, "sha256": sha256_file(out / name)} for name, count in rows.items()
    }
    return {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "customers": customers,
        "history_window": {"start": fmt_ts(WINDOW_START), "end": fmt_ts(WINDOW_END)},
        "defect_rates": dict(rates),
        "files": files,
    }


def write_manifest(out: Path, manifest: Mapping[str, Any]) -> None:
    write_text(out / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
