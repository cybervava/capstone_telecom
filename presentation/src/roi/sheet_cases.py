"""The what-if sheets: Scenarios (five volumes; low, base and high cases) and Sensitivity (the
action plan's row, a two-way table and the break-even points). All formulas; the settings they
vary are inputs on the Inputs sheet.

A three-case table is data: each `CaseRow` is written once for each case. In its formula `{c}`
is the case's column, `{cut}` the case's reduction input, a row's tag in braces the number of
that earlier row (so `{c}{net}` is the case's own net benefit), and any other word in braces a
result kept elsewhere in the workbook."""

# The multiplication sign in a working that is shown to the reader is meant.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass

import inputs as i
import style as s
from grid import Cells, Refs
from openpyxl.formatting.rule import CellIsRule
from openpyxl.worksheet.worksheet import Worksheet

CASES = (
    ("low", "B", "Low_reduction"),
    ("base", "C", "Visit_reduction"),
    ("high", "D", "High_reduction"),
)
TWO_WAY_CUTS = (
    "Floor_reduction",
    "Low_reduction",
    "Visit_reduction",
    "High_reduction",
    "Ceiling_reduction",
)
WIDTHS = {"A": 58, "B": 20, "C": 20, "D": 20, "E": 22, "F": 20, "G": 46}


@dataclass(frozen=True)
class CaseRow:
    tag: str
    label: str
    formula: str
    fmt: str
    kind: str = "calc"
    how: str = ""
    strong: bool = False
    key: str | None = None


def _note(ws: Worksheet, row: int, text: str, column: str = "A") -> None:
    s.put(ws, f"{column}{row}", text, "note", rule=False)


def _case_table(ws: Worksheet, refs: Refs, row: int, rows: tuple[CaseRow, ...]) -> dict[str, int]:
    """Write the rows from `row` down, once for each case. Returns each tag's row number."""
    at: dict[str, int] = {}
    for offset, item in enumerate(rows):
        r = at[item.tag] = row + offset
        s.put(ws, f"A{r}", item.label, wrap=True)
        for case, column, cut in CASES:
            cells = Cells(refs, ws.title, {**at, "c": column, "cut": cut})
            s.put(ws, f"{column}{r}", item.formula.format_map(cells), item.kind, fmt=item.fmt)
            if item.key:
                refs.keep(item.key.format(case=case), ws, f"{column}{r}")
        if item.strong:
            s.emphasise(ws, r, last_col=4)
        if item.how:
            _note(ws, r, item.how, "E")
    return at


# ---------------------------------------------------------------- Scenarios

VOLUME_HEAD = (
    "Outage contacts a month (assumption)",
    "Unnecessary visits a year",
    "Annual baseline waste",
    "the same, in INR million",
    "Annual saving at the base-case reduction",
    "the same, in INR million",
)
VOLUME_NOTES = (
    "Outage contacts × share ending in an unnecessary visit × months in a year × cost of a "
    "visit; the saving is that waste × the reduction. Gross, before any cost.",
    "The volumes are assumptions. Discovery report, section 1: 'Annual savings cannot be "
    "established without actual outage-contact volumes and realized suppression data.'",
)
CASE_HEAD = ("A month, in the pilot population", "Low case", "Base case", "High case")
CASE_ROWS = (
    CaseRow("red", "Reduction in unnecessary visits", "={cut}", s.PCT_0, kind="link",
            how="Inputs. Low and high are the action plan's sensitivity row; base is the base "
                "case"),
    CaseRow("calls", "Outage calls a month", "=Outage_calls_month", s.COUNT, kind="link"),
    CaseRow("base", "Unnecessary visits a month, baseline",
            "={c}{calls}*Unnecessary_visit_rate", s.COUNT),
    CaseRow("avoided", "Visits avoided a month", "={c}{base}*{c}{red}", s.COUNT),
    CaseRow("truck", "Saving from avoided visits", "={c}{avoided}*Visit_cost", s.INR,
            key="case_{case}_visits"),
    CaseRow("call", "Saving from avoided calls (cashable)", "={saving_calls}", s.INR,
            kind="link", how="Benefits sheet; the same in every case"),
    CaseRow("other", "Repeat contacts and churn", "={saving_repeat}+{benefit_churn}",
            s.INR_ZERO, how="Benefits sheet; zero in the base case"),
    CaseRow("cost", "Recurring cost", "={cost_recurring}", s.INR, kind="link",
            how="Costs sheet; the same in every case"),
    CaseRow("net", "Net benefit a month", "={c}{truck}+{c}{call}+{c}{other}-{c}{cost}", s.INR,
            strong=True, key="case_{case}_net"),
    CaseRow("year", "Net benefit a year", "={c}{net}*Months_per_year", s.INR),
    CaseRow("once", "One-time implementation cost", "={cost_one_time}", s.INR, kind="link",
            how="Costs sheet"),
    CaseRow("payback", "Simple payback, months", '=IF({c}{net}>0,{c}{once}/{c}{net},"none")',
            s.RATE_2, strong=True, key="case_{case}_payback",
            how="One-time cost ÷ net benefit a month; excludes discounting and tax"),
    CaseRow("first", "Net benefit of the first year, after the one-time cost",
            "={c}{year}-{c}{once}", s.INR),
    CaseRow("target", "Is the assumed reduction at or above the brief's target?",
            '=IF({c}{red}>=Target_reduction,"Yes","No")', s.TEXT,
            how="An assumed input compared with the brief's target; not a result"),
    CaseRow("share", "If every customer insisted: largest share staff may send a technician to",
            "=1-{c}{red}", s.PCT_0, how="1 - reduction; see Simulation evidence"),
)  # fmt: skip
CASE_NOTES = (
    "The three cases differ only in the reduction, as the action plan's sensitivity row does "
    "('same illustrative costs', p.10).",
    "No case is a measurement. The simulation shows what the workflow books; the reduction end "
    "to end depends on what staff decide for customers who insist.",
)


def scenarios(ws: Worksheet, refs: Refs) -> None:
    row = s.title(
        ws,
        "Scenarios",
        (
            "What the same case facts give at other volumes, and three cases that differ in how "
            "far unnecessary visits are cut. Scenarios, not forecasts.",
        ),
    )
    row = _volume_table(ws, refs, row)
    _three_cases(ws, refs, row + 1)
    s.widths(ws, WIDTHS)


def _volume_table(ws: Worksheet, refs: Refs, row: int) -> int:
    s.put(ws, f"A{row}", "Reduction in unnecessary visits used in this table (base case)")
    s.put(ws, f"B{row}", "=Visit_reduction", "link", fmt=s.PCT_0)
    row += 2
    s.table_head(ws, row, VOLUME_HEAD)
    ws.freeze_panes = ws.cell(row=row + 1, column=2)
    for number, name in enumerate(i.VOLUME_NAMES, start=1):
        r = row + number
        s.put(ws, f"A{r}", f"={name}", "link", fmt=s.COUNT, align="left")
        s.put(ws, f"B{r}", f"=A{r}*Unnecessary_visit_rate*Months_per_year", "calc", fmt=s.COUNT)
        s.put(ws, f"C{r}", f"=B{r}*Visit_cost", "calc", fmt=s.INR)
        s.put(ws, f"D{r}", f"=C{r}", "calc", fmt=s.INR_M)
        s.put(ws, f"E{r}", f"=C{r}*Visit_reduction", "calc", fmt=s.INR)
        s.put(ws, f"F{r}", f"=E{r}", "calc", fmt=s.INR_M)
        refs.keep(f"volume_{number}", ws, f"A{r}")
        refs.keep(f"volume_{number}_waste", ws, f"C{r}")
        refs.keep(f"volume_{number}_saving", ws, f"E{r}")
    row += len(i.VOLUME_NAMES) + 1
    for offset, text in enumerate(VOLUME_NOTES):
        _note(ws, row + offset, text)
    return row + len(VOLUME_NOTES)


def _three_cases(ws: Worksheet, refs: Refs, row: int) -> None:
    s.table_head(ws, row, CASE_HEAD)
    at = _case_table(ws, refs, row + 1, CASE_ROWS)
    agrees = row + 1 + len(CASE_ROWS)
    check = f'=IF(ABS(C{at["net"]}-{refs.link("net_month")})<0.005,"Yes","No")'
    s.put(ws, f"A{agrees}", "Base case agrees with the Benefits sheet?")
    s.put(ws, f"C{agrees}", check, "calc", fmt=s.TEXT)
    for offset, text in enumerate(CASE_NOTES, start=2):
        _note(ws, agrees + offset, text)


# ---------------------------------------------------------------- Sensitivity

REDUCTION_HEAD = (
    "Sensitivity to avoided visits, a month (action plan p.10)",
    "Low",
    "Base",
    "High",
)
REDUCTION_ROWS = (
    CaseRow("red", "Reduction in unnecessary visits", "={cut}", s.PCT_0, kind="link"),
    CaseRow("visits", "Avoided-visit benefit a month",
            "=Outage_calls_month*Unnecessary_visit_rate*{c}{red}*Visit_cost", s.INR,
            how="Outage calls × share × reduction × cost of a visit", key="sens_{case}_visits"),
    CaseRow("net", "Net benefit, including the call saving and the recurring cost",
            "={c}{visits}+{saving_calls}+{saving_repeat}+{benefit_churn}-{cost_recurring}",
            s.INR, strong=True, key="sens_{case}_net",
            how="Benefits and Costs sheets supply the call saving and the recurring cost"),
    CaseRow("payback", "Simple payback, months",
            '=IF({c}{net}>0,{cost_one_time}/{c}{net},"none")', s.RATE_2,
            how="One-time cost ÷ net benefit a month"),
)  # fmt: skip
TWO_WAY_HEAD = (
    "Net benefit a month, by volume (down) and reduction (across)",
    "Floor",
    "Low",
    "Base",
    "High",
    "Ceiling",
)
# One cell of the two-way table: {r} its row (the volume is in column A), {c} its column (the
# reduction is in row {head}).
TWO_WAY_CELL = (
    "=$A{r}*Unnecessary_visit_rate*{c}${head}*Visit_cost"
    "+$A{r}*Call_deflection*Call_cost*Cashable_share"
    "+Count_repeat_saving*$A{r}*Repeat_rate_baseline*Repeat_reduction*Call_cost"
    "+{benefit_churn}-{cost_recurring}"
)
TWO_WAY_NOTES = (
    "A figure in brackets is a loss: at that volume and reduction the pilot would not cover its "
    "recurring cost.",
    "The recurring cost is the Costs sheet's total at every volume ('same illustrative costs'). "
    "In practice cost moves with volume.",
    "Floor and ceiling are the ends of the simulation's end-to-end table: staff send a "
    "technician to every customer who insists, or to none.",
)
WASTE = "Outage_calls_month*Unnecessary_visit_rate*Visit_cost"
FIXED = "{cost_recurring}-{saving_calls}-{saving_repeat}-{benefit_churn}"
PER_CALL = (
    "(Unnecessary_visit_rate*Visit_reduction*Visit_cost+Call_deflection*Call_cost*Cashable_share)"
)
# (label, formula, format, working, name of the result)
BREAK_EVEN = (
    (
        "Reduction in unnecessary visits needed, at the base-case volume",
        f'=IF({WASTE}>0,MAX(0,({FIXED})/({WASTE})),"n/a")',
        s.PCT_1,
        "(Recurring cost - call saving) ÷ baseline waste a month",
        "break_even_reduction",
    ),
    (
        "Outage calls a month needed, at the base-case reduction",
        f'=IF({PER_CALL}>0,{{cost_recurring}}/{PER_CALL},"n/a")',
        s.COUNT,
        "Recurring cost ÷ saving for each outage call (visits and calls only)",
        "break_even_volume",
    ),
)


def sensitivity(ws: Worksheet, refs: Refs) -> None:
    row = s.title(
        ws,
        "Sensitivity",
        (
            "How the net benefit a month moves with the reduction in unnecessary visits and with "
            "the volume of outage contacts. Costs are held as in the base case.",
        ),
    )
    s.table_head(ws, row, REDUCTION_HEAD)
    ws.freeze_panes = ws.cell(row=row + 1, column=2)
    _case_table(ws, refs, row + 1, REDUCTION_ROWS)
    row = _two_way(ws, refs, row + len(REDUCTION_ROWS) + 2)
    _break_even(ws, refs, row + 2)
    s.widths(ws, WIDTHS)


def _two_way(ws: Worksheet, refs: Refs, row: int) -> int:
    s.table_head(ws, row, TWO_WAY_HEAD)
    head = row + 1
    s.put(ws, f"A{head}", "Outage contacts a month (down); reduction (across)", bold=True)
    for column, name in zip("BCDEF", TWO_WAY_CUTS, strict=True):
        s.put(ws, f"{column}{head}", f"={name}", "link", fmt=s.PCT_0, bold=True)
    for number, name in enumerate(i.VOLUME_NAMES, start=1):
        r = head + number
        s.put(ws, f"A{r}", f"={name}", "link", fmt=s.COUNT, align="left")
        for column in "BCDEF":
            cells = Cells(refs, ws.title, {"r": r, "c": column, "head": head})
            s.put(ws, f"{column}{r}", TWO_WAY_CELL.format_map(cells), "calc", fmt=s.INR)
            refs.keep(f"two_way_{number}_{column}", ws, f"{column}{r}")
    last = head + len(i.VOLUME_NAMES)
    loss = CellIsRule(operator="lessThan", formula=["0"], fill=s.fill(s.WARN_SOFT))
    ws.conditional_formatting.add(f"B{head + 1}:F{last}", loss)
    for offset, text in enumerate(TWO_WAY_NOTES, start=1):
        _note(ws, last + offset, text)
    return last + len(TWO_WAY_NOTES)


def _break_even(ws: Worksheet, refs: Refs, row: int) -> None:
    s.table_head(ws, row, ("Break-even points (net benefit a month of zero)", "Value"))
    cells = Cells(refs, ws.title)
    for offset, (label, formula, fmt, how, key) in enumerate(BREAK_EVEN, start=1):
        r = row + offset
        s.put(ws, f"A{r}", label)
        s.put(ws, f"B{r}", formula.format_map(cells), "calc", fmt=fmt)
        _note(ws, r, how, "C")
        refs.keep(key, ws, f"B{r}")
