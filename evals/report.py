"""Evaluation reports: one JSON file for machines and one Markdown file for people.

A report states what was measured, on what, under which assumptions, and against which target.
Numbers come from the run that wrote the file; nothing in a report is typed in by hand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORTS = Path(__file__).resolve().parent / "reports"
CASES = Path(__file__).resolve().parent / "cases"
NO_MODEL = "none (no language model takes part in this measurement)"


def stand_in(provider: str) -> str:
    """How a report names the provider that read the customers' messages in a replay."""
    if provider == "offline":
        return "offline (keyword rules stand in for the language model; none was called)"
    return provider


def percent(part: float, whole: float) -> float:
    """A share in percent, to two decimals. Nothing out of nothing is 0."""
    return round(100 * part / whole, 2) if whole else 0.0


@dataclass
class Report:
    name: str
    title: str
    proves: str
    target: str
    # The one line a reader takes away, and whether the target was met.
    headline: str = ""
    met: bool = False
    # In words, where "met" alone would claim more than was measured.
    verdict: str = ""
    # Which language-model provider produced the result.
    provider: str = NO_MODEL
    assumptions: list[str] = field(default_factory=list)
    tables: list[tuple[str, list[str], list[list[Any]]]] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)

    def table(self, title: str, columns: list[str], rows: list[list[Any]]) -> None:
        self.tables.append((title, columns, rows))

    def write(self, directory: Path = REPORTS) -> Path:
        """Write the two files. ``directory`` is for a report that is not an evaluation's (the
        load test keeps its own beside its code)."""
        directory.mkdir(parents=True, exist_ok=True)
        written_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        payload = {
            "name": self.name,
            "written_at": written_at,
            "target": self.target,
            "met": self.met,
            "verdict": self.verdict or ("met" if self.met else "NOT met"),
            "headline": self.headline,
            "llm_provider": self.provider,
            "numbers": self.numbers,
            "assumptions": self.assumptions,
        }
        (directory / f"{self.name}.json").write_text(json.dumps(payload, indent=2) + "\n")
        lines = [
            f"# {self.title}",
            "",
            f"**What this proves.** {self.proves}",
            "",
            f"**Target.** {self.target}",
            "",
            f"**Result ({self.verdict or ('met' if self.met else 'NOT met')}).** {self.headline}",
            "",
            f"Written by the run of {written_at}. Measured on synthetic stand-in data"
            " (assumption A1); not a measurement of a real network or of real customers."
            f" Language-model provider: {self.provider}.",
            "",
        ]
        for title, columns, rows in self.tables:
            lines += [f"## {title}", "", "| " + " | ".join(columns) + " |"]
            lines.append("|" + "|".join("---" for _ in columns) + "|")
            lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
            lines.append("")
        if self.assumptions:
            lines += ["## What this rests on", ""]
            lines += [f"- {item}" for item in self.assumptions]
            lines.append("")
        path = directory / f"{self.name}.md"
        path.write_text("\n".join(lines))
        return path


def frozen(name: str) -> Any:
    """A frozen case file from ``evals/cases``."""
    return json.loads((CASES / name).read_text(encoding="utf-8"))
