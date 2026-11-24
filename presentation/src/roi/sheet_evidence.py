"""The Simulation evidence sheet: what the evaluations measured on synthetic data, kept apart
from what is assumed for SkyLink, and what no simulation measures.

Every figure is an input of section 8 of the Inputs sheet, or a formula over those inputs."""

# The multiplication sign in a working that is shown to the reader is meant.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass

import style as s
from grid import Cells, Refs
from openpyxl.worksheet.worksheet import Worksheet

LEAD = (
    "What the evaluations measured, in simulation, on synthetic stand-in data. Nothing on this "
    "sheet is a measurement of SkyLink's network or of real customers.",
    "Runs of 17 November 2026, with keyword rules standing in for the language model (none was "
    "called); the credit check involves no language model. Each figure is typed once, on the "
    "Inputs sheet (section 8), with its report.",
)
WIDTHS = {"A": 66, "B": 18, "C": 18, "D": 18, "E": 18, "F": 18, "G": 16, "H": 44}
NA = '"n/a"'


@dataclass(frozen=True)
class Fact:
    """A label, a figure beside it, and a note that runs to the right. In the formula a word in
    braces is a row number of the table above (`{total}`) or a result kept in the workbook."""

    label: str
    formula: str
    fmt: str
    note: str = ""
    kind: str = "calc"
    key: str | None = None


def _facts(
    ws: Worksheet, refs: Refs, row: int, facts: tuple[Fact, ...], rows: dict[str, int]
) -> int:
    """Write the facts from `row` down. Returns the next free row."""
    for r, fact in enumerate(facts, start=row):
        cells = Cells(refs, ws.title, rows)
        s.put(ws, f"A{r}", fact.label, wrap=True)
        s.put(ws, f"B{r}", fact.formula.format_map(cells), fact.kind, fmt=fact.fmt)
        if fact.note:
            s.put(ws, f"C{r}", fact.note, "note", rule=False)
        if fact.key:
            refs.keep(fact.key, ws, f"B{r}")
    return row + len(facts)


# ---------------------------------------------------------------- 1 the pack

PACK = (
    Fact("Labelled contacts in the pack", "=Sim_pack_contacts", s.COUNT,
         "The generator's pack. Its rates are the generator's, not SkyLink's.", kind="link"),
    Fact("of which outage contacts", "=Sim_pack_outage_contacts", s.COUNT, kind="link"),
    Fact("Technician visits recorded in the pack",
         "=Sim_base_visits_outage+Sim_base_visits_line+Sim_base_visits_home", s.COUNT,
         "Outage + line fault + home equipment", key="sim_baseline_visits"),
    Fact("of which unnecessary", "=Sim_base_visits_outage+Sim_base_visits_home", s.COUNT,
         "Sent during an outage, or for a fault the customer could have fixed",
         key="sim_baseline_unnecessary"),
    Fact("Unnecessary visits sent during an outage, as a share of outage contacts",
         f"=IF(Sim_pack_outage_contacts>0,Sim_base_visits_outage/Sim_pack_outage_contacts,{NA})",
         s.PCT_1,
         "The generator encodes the brief's rate (docs/contracts/data.md): the case fact "
         "repeated, not a second measurement of it."),
)  # fmt: skip

# ---------------------------------------------------------------- 2 the replay

REPLAY_HEAD = (
    "2  What the replay showed, by kind of contact",
    "Visits in the baseline",
    "Contacts replayed",
    "Visit booked by the workflow",
    "Ended with a person, no visit",
    "Share of replayed with a visit booked",
    "Was a visit needed?",
)
# (label, the suffix of its inputs, whether a visit was needed)
KINDS = (
    ("Outage on the customer's node", "outage", "No"),
    ("Line fault", "line", "Yes"),
    ("Home equipment the customer could fix", "home", "No"),
)
REPLAY_COLUMNS = ("base_visits", "replayed", "booked", "person")
REPLAY_FACTS = (
    Fact("Unnecessary visits the workflow would book, at the baseline's volumes",
         f"=IF(AND(C{{outage}}>0,C{{home}}>0),"
         f"B{{outage}}*D{{outage}}/C{{outage}}+B{{home}}*D{{home}}/C{{home}},{NA})",
         s.COUNT_ZERO,
         "Each kind's baseline visits × the share of its replayed contacts with a visit booked",
         key="sim_booked_at_volume"),
    Fact("Cut in what the workflow itself books",
         f"=IF(B{{total}}>0,1-{{sim_booked_at_volume}}/B{{total}},{NA})", s.PCT_1,
         "Met for the visits the workflow books. End to end it depends on decisions by people "
         "that a replay cannot measure.", key="sim_cut_workflow"),
    Fact("Needed visits that were booked", f"=IF(C{{line}}>0,D{{line}}/C{{line}},{NA})", s.PCT_1,
         "Every line fault replayed was offered a visit and booked one.",
         key="sim_needed_booked"),
    Fact("Median minutes from contact to the booking of a needed visit",
         "=Sim_minutes_to_booking", s.COUNT, "The price of the hold before a visit is offered.",
         kind="link"),
    Fact("Contacts replayed in all", "=C{outage}+C{line}+C{home}", s.COUNT,
         "'They show what the rules do; they are not a sample to take rates from.' "
         "(evals/reports/truck_rolls.md)", key="sim_replayed"),
    Fact("Contacts with no visit needed that ended with a person", "=E{total}", s.COUNT_ZERO,
         "The simulated customer went on asking for a technician until a person took over: the "
         "customer most likely to get a visit they do not need."),
)  # fmt: skip

# ---------------------------------------------------------------- 3 end to end

END_HEAD = (
    "3  End to end, if every customer went on asking",
    "Reduction in unnecessary visits",
    "Largest share staff may send a technician to",
    "Unnecessary visits left, at the pack's volume",
    "Meets the brief's target?",
)
END_TO_END = (
    ("Ceiling: no person sends a technician", "Ceiling_reduction"),
    ("High case", "High_reduction"),
    ("Base case (assumed in this model)", "Visit_reduction"),
    ("Low case", "Low_reduction"),
    ("Floor: every person sends a technician", "Floor_reduction"),
)
# The report rounds a half to the even number (1612.5 prints as 1612), so this does too.
# {x} is the unrounded figure.
HALF_TO_EVEN = "=IF(ABS({x}-INT({x})-0.5)<0.000001,2*ROUND({x}/2,0),ROUND({x},0))"
END_NOTES = (
    "A customer who goes on asking is handed to a person, with the evidence. What that person "
    "decides, and how many customers go on asking, no replay can measure.",
    "The base case of this model assumes the reduction on the third line. The simulation "
    "supports that the workflow does its part; it does not measure the line itself.",
)

# ---------------------------------------------------------------- 4 other results

OTHER_HEAD = (
    "4  Other results the value case rests on",
    "Result",
    "Target (brief)",
    "Met?",
    "What was replayed",
)
DETECTION_BASIS = (
    '=TEXT(Sim_detection_replayed,"#,##0")&" contacts replayed on "&'
    'TEXT(Sim_detection_nodes,"#,##0")&" nodes, weighted by how often each kind occurs '
    '(evals/reports/outage_detection.md)"'
)
CREDIT_BASIS = (
    '=TEXT(Sim_credit_matched,"#,##0")&" of "&TEXT(Sim_credit_cases,"#,##0")&'
    '" grid cases, and "&TEXT(Sim_credit_hand_matched,"0")&" of "&TEXT(Sim_credit_hand,"0")&'
    '" amounts worked out by hand (evals/reports/credit_policy.md)"'
)
# (label, result, kind, format, target, what was replayed)
OTHER = (
    ("Outage detected for affected customers", "=Sim_detection_rate", "link", s.PCT_2,
     "Detection_target", DETECTION_BASIS),
    ("Credits that match the policy",
     f"=IF(Sim_credit_cases>0,Sim_credit_matched/Sim_credit_cases,{NA})", "calc", s.PCT_1,
     "Credit_target", CREDIT_BASIS),
)  # fmt: skip
OTHER_NOTE = (
    "Why they bear on the money: a visit is avoided only where the outage is recognised, and a "
    "credit that matches the policy is neither too much nor too little. Neither result is "
    "priced in this model."
)

# ---------------------------------------------------------------- 5 not measured

NOT_MEASURED_HEAD = (
    "5  Not measured by any simulation: assumed on the Inputs sheet, or not established"
)
NOT_MEASURED = (
    "SkyLink's real volume of outage contacts. The volumes of the synthetic pack are the "
    "generator's.",
    "The reduction in unnecessary visits end to end. The workflow books none; people decide "
    "for customers who insist.",
    "The share of calls avoided, and the handling cost of a call.",
    "Repeat contacts within seven days, and churn.",
    "The cost of a pilot: cloud, model usage and labour.",
)


def evidence(ws: Worksheet, refs: Refs) -> None:
    row = s.title(ws, "Simulation evidence", LEAD)
    s.table_head(ws, row, ("1  The baseline in the synthetic data pack", "Value"))
    ws.freeze_panes = ws.cell(row=row + 1, column=2)
    row = _facts(ws, refs, row + 1, PACK, {})
    row = _replay(ws, refs, row + 1)
    row = _end_to_end(ws, refs, row + 1)
    row = _other_results(ws, row + 1)
    s.section(ws, row + 1, NOT_MEASURED_HEAD, last_col=8)
    for offset, text in enumerate(NOT_MEASURED, start=2):
        s.put(ws, f"A{row + offset}", text, "note", rule=False)
    s.widths(ws, WIDTHS)


def _replay(ws: Worksheet, refs: Refs, row: int) -> int:
    s.table_head(ws, row, REPLAY_HEAD)
    rows: dict[str, int] = {}
    for r, (label, kind, needed) in enumerate(KINDS, start=row + 1):
        rows[kind] = r
        s.put(ws, f"A{r}", label)
        for column, name in zip("BCDE", REPLAY_COLUMNS, strict=True):
            s.put(ws, f"{column}{r}", f"=Sim_{name}_{kind}", "link", fmt=s.COUNT_ZERO)
        s.put(ws, f"F{r}", f"=IF(C{r}>0,D{r}/C{r},{NA})", "calc", fmt=s.PCT_1)
        s.put(ws, f"G{r}", needed, "note", align="center")
    total = rows["total"] = row + len(KINDS) + 1
    s.put(ws, f"A{total}", "Contacts where no visit was needed (outage and home equipment)")
    for column in "BCDE":
        both = f"={column}{rows['outage']}+{column}{rows['home']}"
        s.put(ws, f"{column}{total}", both, "calc", fmt=s.COUNT_ZERO)
    s.put(ws, f"F{total}", f"=IF(C{total}>0,D{total}/C{total},{NA})", "calc", fmt=s.PCT_1)
    s.put(ws, f"G{total}", "No", "note", align="center")
    s.emphasise(ws, total, last_col=7)
    refs.keep("sim_unneeded_replayed", ws, f"C{total}")
    refs.keep("sim_unneeded_booked", ws, f"D{total}")
    return _facts(ws, refs, total + 2, REPLAY_FACTS, rows)


def _end_to_end(ws: Worksheet, refs: Refs, row: int) -> int:
    s.table_head(ws, row, END_HEAD)
    unnecessary = refs.link("sim_baseline_unnecessary").split("!")[1]
    for r, (label, name) in enumerate(END_TO_END, start=row + 1):
        left = HALF_TO_EVEN.format(x=f"{unnecessary}*C{r}")
        meets = f'=IF(B{r}>=Target_reduction,"Yes","No")'
        s.put(ws, f"A{r}", label)
        s.put(ws, f"B{r}", f"={name}", "link", fmt=s.PCT_0)
        s.put(ws, f"C{r}", f"=1-B{r}", "calc", fmt=s.PCT_0)
        s.put(ws, f"D{r}", left, "calc", fmt=s.COUNT_ZERO)
        s.put(ws, f"E{r}", meets, "calc", fmt=s.TEXT, align="center")
        refs.keep(f"sim_left_{r - row}", ws, f"D{r}")
    row += len(END_TO_END) + 1
    holds = Fact(
        "The brief's target holds while staff send a technician to at most",
        "=1-Target_reduction",
        s.PCT_0,
        "of the customers who go on asking (evals/reports/truck_rolls.md)",
        key="sim_largest_share",
    )
    row = _facts(ws, refs, row, (holds,), {})
    for offset, text in enumerate(END_NOTES):
        s.put(ws, f"A{row + offset}", text, "note", rule=False)
    return row + len(END_NOTES)


def _other_results(ws: Worksheet, row: int) -> int:
    s.table_head(ws, row, OTHER_HEAD)
    for r, (label, result, kind, fmt, target, basis) in enumerate(OTHER, start=row + 1):
        s.put(ws, f"A{r}", label)
        s.put(ws, f"B{r}", result, kind, fmt=fmt)
        s.put(ws, f"C{r}", f"={target}", "link", fmt=s.PCT_0)
        s.put(ws, f"D{r}", f'=IF(B{r}>=C{r},"Yes","No")', "calc", fmt=s.TEXT, align="center")
        s.put(ws, f"E{r}", basis, "calc", fmt=s.TEXT, align="left", rule=False)
    s.put(ws, f"A{row + len(OTHER) + 1}", OTHER_NOTE, "note", rule=False)
    return row + len(OTHER) + 2
