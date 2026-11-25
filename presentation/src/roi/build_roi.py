"""Build presentation/G3_Telco_ROI.xlsx, the ROI model of the submission (item 8).

    cd <repo> && uv run --no-project --with openpyxl python presentation/src/roi/build_roi.py

The inputs, with their status and source, are in `inputs.py`. Everything else in the workbook is
a formula over them, and each sheet is described as data in its own module: `sheet_baseline.py`,
`sheet_costs.py`, `sheet_benefits.py`, `sheet_cases.py` (Scenarios and Sensitivity),
`sheet_evidence.py` and `sheet_readme.py`. `grid.py` writes the rows and keeps track of where
each result sits; `style.py` holds the looks. To change a published figure, change it in
`inputs.py` and run this again.

openpyxl writes formulas without their results, so the build ends by having LibreOffice
calculate a copy and storing each result beside its formula (`results.py`). That needs
LibreOffice on the PATH, or Docker with the presentation's image (`docker build -t
skylink-presentation-lo:1 presentation/src/libreoffice`). Without either the workbook is still
written and still calculates when opened and edited, but a preview or Excel's Protected View
shows its figures empty; the build says so and exits with status 2. `--no-store` skips the step.

`--cells FILE` also writes a JSON map from each named result to its cell, for whoever quotes the
workbook elsewhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import inputs as i
import style as s
from grid import Refs, check_names
from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet
from results import ResultsError, store_results
from sheet_baseline import baseline
from sheet_benefits import benefits
from sheet_cases import scenarios, sensitivity
from sheet_costs import costs
from sheet_evidence import evidence
from sheet_readme import read_me

OUTPUT = Path(__file__).resolve().parents[2] / "G3_Telco_ROI.xlsx"
# Each sheet, in the order of the tabs, with the last column its print area runs to.
SHEETS = {
    "Read me": "D",
    "Inputs": "G",
    "Baseline": "E",
    "Scenarios": "G",
    "Costs": "E",
    "Benefits": "E",
    "Sensitivity": "G",
    "Simulation evidence": "H",
}
INPUT_HEAD = ("Input", "Value", "Unit", "Status", "Source", "Note", "Name in formulas")
PERCENT_FORMATS = (s.PCT_0, s.PCT_1, s.PCT_2)


def _validations(ws: Worksheet) -> dict[str, DataValidation]:
    """What a blue cell accepts: a share from 0 to 1, a switch of 0 or 1, or a number from 0 up."""
    share = DataValidation(
        type="decimal", operator="between", formula1="0", formula2="1", allow_blank=True
    )
    share.error, share.errorTitle = "Enter a share between 0% and 100%.", "Share"
    switch = DataValidation(type="list", formula1='"0,1"', allow_blank=False)
    switch.error, switch.errorTitle = "Enter 1 to count this benefit, 0 to leave it out.", "Switch"
    amount = DataValidation(
        type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True
    )
    amount.error, amount.errorTitle = "Enter a number of zero or more.", "Amount"
    rules = {"share": share, "switch": switch, "amount": amount}
    for rule in rules.values():
        rule.showErrorMessage = True
        ws.add_data_validation(rule)
    return rules


def _rule_for(item: i.Input) -> str:
    if item.name in i.SWITCH_NAMES:
        return "switch"
    return "share" if item.fmt in PERCENT_FORMATS else "amount"


def inputs_sheet(wb: Workbook, ws: Worksheet) -> frozenset[str]:
    """Write every input once and give it a defined name. Returns the names."""
    row = s.title(
        ws,
        "Inputs",
        (
            "Every figure the model does not calculate is typed here, once. Type over a blue value "
            "to change the model; nothing is typed on any other sheet.",
            "A yellow value is an assumption, or a gap nobody has filled: replace it with "
            "SkyLink's "
            "own figure. A blank value counts as zero.",
        ),
    )
    s.table_head(ws, row, INPUT_HEAD)
    ws.freeze_panes = ws.cell(row=row + 1, column=2)
    rules = _validations(ws)
    names: set[str] = set()
    row += 1
    for section in i.SECTIONS:
        s.section(ws, row, section.title, last_col=len(INPUT_HEAD))
        row += 1
        for item in section.rows:
            _input_row(ws, row, item)
            rules[_rule_for(item)].add(f"B{row}")
            wb.defined_names[item.name] = DefinedName(item.name, attr_text=f"'{ws.title}'!$B${row}")
            if item.name in names:
                raise ValueError(f"input {item.name!r} is defined twice")
            names.add(item.name)
            row += 1
    s.widths(ws, {"A": 54, "B": 16, "C": 24, "D": 27, "E": 50, "F": 58, "G": 28})
    return frozenset(names)


def _input_row(ws: Worksheet, row: int, item: i.Input) -> None:
    shade = s.YELLOW if item.status in i.TO_REPLACE else None
    s.put(ws, f"A{row}", item.label, wrap=True)
    s.put(ws, f"B{row}", item.value, "input", fmt=item.fmt, shade=shade)
    s.put(ws, f"C{row}", item.unit, "note", wrap=True)
    s.put(ws, f"D{row}", item.status, bold=item.status != i.CONST)
    s.put(ws, f"E{row}", item.source, "note", wrap=True)
    s.put(ws, f"F{row}", item.note, "note", wrap=True)
    s.put(ws, f"G{row}", item.name, "note", indent=1)


def build() -> tuple[Workbook, Refs]:
    wb = s.new_workbook()
    sheet = {name: wb.create_sheet(name) for name in SHEETS}
    refs = Refs()
    names = inputs_sheet(wb, sheet["Inputs"])
    # Order matters: a sheet may only point at results that are already placed.
    baseline(sheet["Baseline"], refs)
    costs(sheet["Costs"], refs)
    benefits(sheet["Benefits"], refs)
    scenarios(sheet["Scenarios"], refs)
    sensitivity(sheet["Sensitivity"], refs)
    evidence(sheet["Simulation evidence"], refs)
    read_me(sheet["Read me"], refs)
    for name, ws in sheet.items():
        s.fit_rows(ws)
        s.page(
            ws,
            tab=s.BLUE if name == "Inputs" else s.INK,
            last_column=SHEETS[name],
            repeat_rows="5:5" if name == "Inputs" else None,
        )
    check_names(wb, names)
    wb.calculation.fullCalcOnLoad = True
    wb.properties.title = "SkyLink outage-aware customer care agent: ROI model"
    wb.properties.subject = (
        "G3 Telco capstone, submission item 8. Fictional client, synthetic data."
    )
    wb.properties.creator = "Group 3, Telco"
    wb.properties.lastModifiedBy = "Group 3, Telco"
    wb.active = 0
    return wb, refs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT, help="where to write the workbook")
    parser.add_argument("--cells", type=Path, help="also write a JSON map of result name to cell")
    parser.add_argument(
        "--no-store",
        action="store_true",
        help="do not calculate the workbook and store the results in it",
    )
    args = parser.parse_args()
    wb, refs = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.output)
    if args.cells:
        args.cells.write_text(json.dumps(refs.all(), indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{args.output}\n")
    if args.no_store:
        return 0
    try:
        stored = store_results(args.output)
    except ResultsError as problem:
        sys.stderr.write(
            f"Results NOT stored: {problem}.\nThe workbook is written and calculates when it is "
            "opened and edited, but a preview or Excel's Protected View shows its figures empty.\n"
        )
        return 2
    sys.stdout.write(f"results of {stored} formulas stored\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
