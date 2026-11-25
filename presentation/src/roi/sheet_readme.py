"""The Read me sheet: what the model is, which figures are measured and which assumed, the
headline of the base case (live), how to use the workbook, the colour key, the sources and the
caveats."""

# The multiplication sign in a working that is shown to the reader is meant.
# ruff: noqa: RUF001

from __future__ import annotations

import textwrap

import inputs as i
import style as s
from grid import Cells, Refs
from openpyxl.worksheet.worksheet import Worksheet

LINE_LENGTH = 150
NAME_COLUMN = 50
WIDTHS = {"A": NAME_COLUMN, "B": 22, "C": 52, "D": 70}
TITLE = "SkyLink Telecom outage-aware customer care agent: ROI model"
LEAD = (
    "Group 3, Telco. Capstone submission item 8: ROI model with stated assumptions. "
    "Sources as at commit b9c70ef of the repository.",
)
WHAT_IT_IS = (
    "This workbook is a scenario model of what an outage-aware customer care agent could save "
    "SkyLink Telecom in a broadband pilot, and of what the pilot would cost. It is a scenario, "
    "not a forecast.",
    "SkyLink Telecom is a fictional client and all data is synthetic. The datasets, the credit "
    "policy, the router guides and the mock APIs behind the measured results are stand-ins built "
    "by the team, because the supplied ones were not received (README.md, 'Two things that are "
    "stand-ins').",
    "The base case reproduces the illustration in the team's action plan (p.10) and the volume "
    "table of the discovery report (section 6). Both were written before the system was finished: "
    "they are plans, and their figures are illustrations.",
    "Every amount is in US dollars. The brief, the action plan and the discovery report give "
    "their amounts in rupees; since the project review of 25 November 2026 the same figures are "
    "shown in dollars, and nothing is converted (docs/EXECUTION_PLAN.md, client decision D12).",
)
MEASURED_OR_ASSUMED = (
    "Case facts, from the brief: 30% of outage-driven calls end in an unnecessary technician "
    "visit, and a visit costs $650 (650 in the brief's rupees).",
    "Measured, in simulation on synthetic data: the workflow booked a visit for none of the 28 "
    "replayed contacts that did not need one, and for all 10 that did. A customer who goes on "
    "asking is handed to a person, and what that person decides no replay can measure.",
    "Assumed: the pilot's volume of outage calls, the reduction in visits achieved end to end, "
    "the share of calls avoided, the cost of a call, and both cost figures of the pilot. No dollar "
    "saving in this workbook is a measured result.",
)
HEADLINE_HEAD = (
    "Headline, base case",
    "Value",
    "Status",
    "Where it is worked out, and as published",
)
BOOKED = '=TEXT({sim_unneeded_booked},"0")&" of "&TEXT({sim_unneeded_replayed},"0")&" replayed"'
# (label, formula, format, status, the result whose cell is pointed at, as published)
HEADLINE = (
    ("Outage calls a month, pilot population", "=Outage_calls_month", s.COUNT, i.ASSUME, None,
     "Inputs. Action plan p.10: 10,000, to be replaced with the observed baseline"),
    ("Unnecessary visits a month, baseline", "={visits_month}", s.COUNT,
     "Case fact × assumed volume", "visits_month", "Action plan p.10: 3,000"),
    ("Saving from avoided visits a month", "={saving_visits}", s.USD,
     "Scenario: case facts × assumed volume × assumed reduction", "saving_visits",
     "Action plan p.10: 975,000"),
    ("Saving from avoided calls a month", "={saving_calls}", s.USD,
     "Assumption: share of calls avoided, cost of a call", "saving_calls",
     "Action plan p.10: 120,000"),
    ("Recurring cost a month", "={cost_recurring}", s.USD, "Assumption; not a cloud quote",
     "cost_recurring", "Action plan p.10: 400,000"),
    ("Net benefit a month", "={net_month}", s.USD, "Scenario; follows from the lines above",
     "net_month", "Action plan p.10: 695,000"),
    ("One-time implementation cost", "={cost_one_time}", s.USD, i.ASSUME, "cost_one_time",
     "Action plan p.10: 1,200,000"),
    ("Simple payback, months", "={payback_months}", s.RATE_2,
     "Scenario; excludes discounting and tax", "payback_months", "Action plan p.10: 1.73 months"),
    ("Saving per 1,000 outage contacts", "={block_saving}", s.USD,
     "Case facts × assumed reduction", "block_saving", "Discovery report, section 6: 97,500"),
    ("Unnecessary visits the workflow booked in simulation", BOOKED, s.TEXT,
     "Measured in simulation, on synthetic data", "sim_unneeded_booked",
     "evals/reports/truck_rolls.md"),
    ("Cloud cost of the demonstration, a day", "={demo_day_usd}", s.USD_2,
     "From the AWS demo's configuration, at list prices", "demo_day_usd",
     "README.md: about $1.90 a day"),
)  # fmt: skip
HOW_TO_USE = (
    "Type only in blue cells, and only on the Inputs sheet. Every other figure is a formula and "
    "follows.",
    "A yellow cell is an assumption. Replace it with SkyLink's own figure when there is one.",
    "A blank yellow cell is a figure nobody has established. It counts as zero until it is "
    "filled in.",
    "Each input has a name, shown on the Inputs sheet. Formulas use those names, so a formula "
    "reads as its meaning.",
    "The workbook is a plain .xlsx file: no macros, and no links to other files.",
)
# (sample, the kind of cell it shows, its fill, what it means)
COLOUR_KEY = (
    ("Input", "input", None,
     "Blue text: a typed input. Inputs are on the Inputs sheet and nowhere else."),
    ("Formula", "calc", None, "Black text: a formula. Do not type over it."),
    ("Link", "link", None,
     "Green text: a value brought from another sheet, or straight from one input."),
    ("Assumption", "input", s.YELLOW,
     "Yellow fill: an assumption, or a gap to fill. Replace it with SkyLink's own figure."),
)  # fmt: skip
SHEETS = (
    ("Inputs", "Every input once: value, unit, status, source, note and the name formulas use."),
    ("Baseline", "What unnecessary technician visits cost before any change."),
    ("Scenarios", "Annual waste and saving at five volumes; low, base and high cases."),
    ("Costs",
     "The pilot's one-time and recurring cost; the demonstration's cloud cost, kept apart."),
    ("Benefits",
     "The base case a month: truck rolls, call handling, repeat contacts and churn (both at "
     "zero), net benefit, payback."),
    ("Sensitivity", "Net benefit by reduction; by volume and reduction; the break-even points."),
    ("Simulation evidence",
     "What the evaluations measured on synthetic data, and what no simulation measures."),
)  # fmt: skip
RULES_HEAD = "Rules the model keeps (action plan p.10; discovery report, section 6)"
RULES = (
    "Visit savings are counted once, from the whole baseline, including visits avoided through "
    "call deflection. Call savings cover contact handling only.",
    "Released staff capacity is shown apart from cashable savings and is not counted.",
    "Repeat-contact and churn benefits are in the model and held at zero in the base case, each "
    "with its reason on the Benefits sheet.",
    "The demonstration's cloud cost is the capstone's own budget. It is shown apart from the "
    "pilot's cost and enters no benefit or payback figure.",
    "Annual figures are twelve times the monthly figure. There is no seasonality, discounting "
    "or tax.",
)
SOURCES = (
    ("Brief",
     "'Group 3 - Telco: Outage-Aware Customer Care Agent' (PDF, kept out of the public "
     "repository). Case facts "
     "p.1; success metrics p.2; item 8 p.3; rubric p.8."),
    ("Action plan",
     "G3_Telco_FDE_Action_Plan.pdf (13 November 2026), a plan, kept out of the public "
     "repository. p.10: the illustrative monthly "
     "case, the sensitivity row, the counting rules, the pilot targets."),
    ("Discovery report",
     "discovery-samples/G3_Telco_FDE_Discovery_Workshop_Report (1).pdf (22 November 2026), a "
     "simulated workshop. Section 6: per 1,000 contacts and the volume table; section 7: "
     "baselines still required; workshop decision D13."),
    ("README.md",
     "The results table; 'The demo on AWS' (prices, first release); 'Two things that are "
     "stand-ins'."),
    ("Truck rolls",
     "evals/reports/truck_rolls.md. Technician visits in simulation (run of 17 November 2026)."),
    ("Detection, credits",
     "evals/reports/outage_detection.md and evals/reports/credit_policy.md. Outage detection "
     "and credit results."),
    ("The data pack",
     "evals/README.md and docs/contracts/data.md. The 9,000 labelled outage contacts; the "
     "generator's 30%."),
    ("Architecture page",
     "docs/architecture_design.html. 'What the demonstration costs'; 'What a pilot would "
     "change'."),
    ("Execution plan",
     "docs/EXECUTION_PLAN.md. Client decision D4 (AWS); the token counts of model calls are "
     "not stored."),
    ("Terraform",
     "deploy/aws/terraform/variables.tf and database.tf. The instance sizes of the "
     "demonstration."),
)  # fmt: skip
CAVEATS = (
    "The volume of 10,000 outage calls a month is illustrative. Discovery report: 'Annual "
    "savings cannot be established without actual outage-contact volumes and realized "
    "suppression data.'",
    "The technician-visit simulation replayed 38 contacts. Its report: 'They show what the "
    "rules do; they are not a sample to take rates from.'",
    "The cloud cost of a pilot and the cost of model usage have not been estimated. The "
    "recurring cost is the action plan's single illustrative figure.",
    "Payback is simple payback from the first month of benefit. The pilot the action plan "
    "proposes starts in shadow mode (p.12), which costs and does not yet save.",
)


def _heading(ws: Worksheet, row: int, text: str) -> int:
    s.section(ws, row, text, last_col=4)
    return row + 1


def _prose(ws: Worksheet, row: int, paragraphs: tuple[str, ...], *, numbered: bool = False) -> int:
    """Paragraphs as lines of plain text that run across the empty cells to their right."""
    for number, paragraph in enumerate(paragraphs, start=1):
        lead = f"{number}.  " if numbered else ""
        hang = "     " if numbered else ""
        for line in textwrap.wrap(lead + paragraph, LINE_LENGTH, subsequent_indent=hang):
            ws.cell(row=row, column=1, value=line).font = s.font(s.INK)
            row += 1
        if not numbered:
            row += 1
    return row + (1 if numbered else 0)


def _headline(ws: Worksheet, refs: Refs, row: int) -> int:
    s.table_head(ws, row, HEADLINE_HEAD)
    cells = Cells(refs, ws.title)
    for r, (label, formula, fmt, status, key, published) in enumerate(HEADLINE, start=row + 1):
        pointer = f"{refs.where(key)}. {published}" if key else published
        s.put(ws, f"A{r}", label)
        s.put(ws, f"B{r}", formula.format_map(cells), "link", fmt=fmt)
        s.put(ws, f"C{r}", status, "note", wrap=True)
        s.put(ws, f"D{r}", pointer, "note", wrap=True)
    return row + len(HEADLINE) + 2


def _colour_key(ws: Worksheet, row: int) -> int:
    for r, (sample, kind, shade, meaning) in enumerate(COLOUR_KEY, start=row):
        s.put(ws, f"A{r}", sample, kind, align="left", shade=shade, rule=False)
        s.put(ws, f"B{r}", meaning, "note", rule=False)
    return row + len(COLOUR_KEY) + 1


def _pairs(ws: Worksheet, row: int, rows: tuple[tuple[str, str], ...]) -> int:
    """A small table: a name in A, then text from B running right, on as many lines as it needs."""
    for name, text in rows:
        s.put(ws, f"A{row}", name, bold=True, rule=False)
        for line in textwrap.wrap(text, LINE_LENGTH - NAME_COLUMN):
            s.put(ws, f"B{row}", line, "note", rule=False)
            row += 1
    return row + 1


def read_me(ws: Worksheet, refs: Refs) -> None:
    row = s.title(ws, TITLE, LEAD)
    row = _prose(ws, _heading(ws, row, "What this workbook is"), WHAT_IT_IS)
    row = _heading(ws, row, "Which figures are case facts, which are measured, which are assumed")
    row = _prose(ws, row, MEASURED_OR_ASSUMED, numbered=True)
    row = _headline(ws, refs, row)
    row = _prose(ws, _heading(ws, row, "How to use it"), HOW_TO_USE, numbered=True)
    row = _colour_key(ws, _heading(ws, row, "Colour key"))
    row = _pairs(ws, _heading(ws, row, "The sheets"), SHEETS)
    row = _prose(ws, _heading(ws, row, RULES_HEAD), RULES, numbered=True)
    row = _pairs(ws, _heading(ws, row, "Sources"), SOURCES)
    _prose(ws, _heading(ws, row, "Caveats"), CAVEATS, numbered=True)
    s.widths(ws, WIDTHS)
