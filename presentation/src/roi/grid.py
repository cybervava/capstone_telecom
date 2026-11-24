"""Bookkeeping for the workbook: where each named result sits, and the writer for the sheets
that are laid out as one figure a row (label, value, unit, working, status).

A sheet is described as data: a tuple of `Band`, `Row` and `Remark`. A row's formula names the
inputs by their defined names, and other results in braces: `{visits_month}` becomes that
result's cell, written as a plain reference on its own sheet and with the sheet's name anywhere
else. `{b}` is the row's own value cell.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import style as s
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

_FUNCTIONS = frozenset({"IF", "ISBLANK", "ABS", "INT", "ROUND", "MAX", "TEXT", "SUM", "AND", "OR"})
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CELL = re.compile(r"(?<![A-Za-z0-9_])\$?[A-Z]{1,3}\$?\d+(?![A-Za-z0-9_])")
_QUOTED = re.compile(r'"[^"]*"')
_SHEET = re.compile(r"'[^']*'!")
_REF = re.compile(r"([A-Z]+)(\d+)")


@dataclass(frozen=True)
class Band:
    """A band across the table that names the group of rows under it."""

    title: str


@dataclass(frozen=True)
class Remark:
    """A sentence under a group of rows, running across the sheet from the label column."""

    text: str


@dataclass(frozen=True)
class Row:
    """One figure. `key` names its value cell for other rows, other sheets and the summary.

    On the Costs sheet a row also has a rupee column: `inr` is its formula and `inr_key` its name.
    """

    label: str
    formula: str | None
    fmt: str
    unit: str = ""
    how: str = ""
    status: str = ""
    kind: str = "calc"
    key: str | None = None
    strong: bool = False
    inr: str | None = None
    inr_key: str | None = None


@dataclass
class Refs:
    """Remembers the cell of every result another sheet, the summary or a reviewer needs."""

    _at: dict[str, tuple[str, str]] = field(default_factory=dict)

    def keep(self, key: str, ws: Worksheet, ref: str) -> None:
        if key in self._at:
            raise ValueError(f"result {key!r} is placed twice")
        self._at[key] = (ws.title, ref)

    def link(self, key: str) -> str:
        """The cell as another sheet's formula writes it: 'Sheet name'!$B$12."""
        sheet, ref = self._at[key]
        column, row = _REF.fullmatch(ref).groups()
        return f"'{sheet}'!${column}${row}"

    def where(self, key: str) -> str:
        """The cell as a person reads it: Sheet name!B12."""
        sheet, ref = self._at[key]
        return f"{sheet}!{ref}"

    def seen_from(self, key: str, sheet: str) -> str:
        """The cell as a formula on `sheet` writes it: bare on its own sheet, linked elsewhere."""
        home, ref = self._at[key]
        return ref if home == sheet else self.link(key)

    def all(self) -> dict[str, str]:
        return {key: self.where(key) for key in self._at}


class Cells(Mapping[str, str]):
    """What the braces of a formula stand for, on one sheet.

    `local` holds what only this table knows (its own cell, a column letter, a row number);
    every other word is a result kept in `Refs`."""

    def __init__(self, refs: Refs, sheet: str, local: Mapping[str, object] | None = None) -> None:
        self._refs, self._sheet, self._local = refs, sheet, local or {}

    def __getitem__(self, key: str) -> str:
        if key in self._local:
            return str(self._local[key])
        return self._refs.seen_from(key, self._sheet)

    def __iter__(self) -> Iterator[str]:
        return iter(self._local)

    def __len__(self) -> int:
        return len(self._local)


@dataclass
class Lines:
    """Writes `Band`, `Row` and `Remark` items down a sheet, one row each.

    Columns are label, value, unit, working, status; with `rupees` they are label, value, unit,
    rupee value, status, source or working."""

    ws: Worksheet
    refs: Refs
    row: int
    rupees: bool = False

    @property
    def last_col(self) -> int:
        return 6 if self.rupees else 5

    def head(self, labels: tuple[str, ...]) -> Lines:
        s.table_head(self.ws, self.row, labels)
        self.ws.freeze_panes = self.ws.cell(row=self.row + 1, column=2)
        self.row += 1
        return self

    def write(self, items: tuple[Band | Row | Remark, ...]) -> None:
        for item in items:
            if isinstance(item, Band):
                s.section(self.ws, self.row, item.title, last_col=self.last_col)
            elif isinstance(item, Remark):
                s.put(self.ws, f"A{self.row}", item.text, "note", rule=False)
            else:
                self._figure(item)
            self.row += 1

    def _figure(self, item: Row) -> None:
        r, ws = self.row, self.ws
        cells = Cells(self.refs, ws.title, {"b": f"B{r}"})
        value = item.formula.format_map(cells) if item.formula else None
        s.put(ws, f"A{r}", item.label, wrap=True)
        s.put(ws, f"B{r}", value, item.kind, fmt=item.fmt)
        s.put(ws, f"C{r}", item.unit, "note")
        if self.rupees:
            rupees = item.inr.format_map(cells) if item.inr else None
            s.put(ws, f"D{r}", rupees, "calc", fmt=s.INR)
            s.put(ws, f"E{r}", item.status, "note", wrap=True)
            s.put(ws, f"F{r}", item.how, "note", wrap=True)
        else:
            s.put(ws, f"D{r}", item.how, "note", wrap=True)
            s.put(ws, f"E{r}", item.status, "note", wrap=True)
        if item.strong:
            s.emphasise(ws, r, last_col=self.last_col)
        if item.key:
            self.refs.keep(item.key, ws, f"B{r}")
        if item.inr_key:
            self.refs.keep(item.inr_key, ws, f"D{r}")


def check_names(wb: Workbook, names: frozenset[str]) -> None:
    """Fail the build if a formula uses a name that no input defines (a typing slip)."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    _check_formula(cell.value, f"{ws.title}!{cell.coordinate}", names)


def _check_formula(formula: str, at: str, names: frozenset[str]) -> None:
    """With texts, sheet prefixes and cell references gone, each word left is a name or function."""
    bare = _CELL.sub("", _SHEET.sub("", _QUOTED.sub("", formula)))
    for word in _WORD.findall(bare):
        if word not in names and word not in _FUNCTIONS:
            raise ValueError(
                f"{at}: formula {formula!r} uses {word!r}, which is not a defined input"
            )
