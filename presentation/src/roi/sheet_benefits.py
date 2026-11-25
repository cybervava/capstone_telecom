"""The Benefits sheet: the base case, a month. Truck rolls, call handling, the two benefits held
at zero with their reasons, then the net benefit and the payback."""

# The multiplication sign in a working that is shown to the reader is meant.
# ruff: noqa: RUF001

from __future__ import annotations

import inputs as i
import style as s
from grid import Band, Lines, Refs, Remark, Row
from openpyxl.worksheet.worksheet import Worksheet

HEAD = ("Line", "Value", "Unit", "How it is worked out", "Status")
LEAD = (
    "The base case, a month. Every saving here is a scenario: case facts from the brief, "
    "multiplied by assumptions. None is a measured saving.",
    "Visit savings are counted once, from the whole baseline. Call savings cover contact "
    "handling only, with no visit cost bundled in.",
)
WIDTHS = {"A": 60, "B": 18, "C": 28, "D": 62, "E": 48}
NOT_ESTABLISHED = '"not established"'
FROM_INPUTS = "Inputs"
SWITCH = "Switch on the Inputs sheet"

TRUCK_ROLLS = (
    Band("A  Truck rolls: unnecessary technician visits avoided"),
    Row("Unnecessary visits a month, baseline", "={visits_month}", s.COUNT,
        unit="visits a month", how="Baseline sheet", status="Case fact × assumed volume",
        kind="link", key="benefit_visits"),
    Row("Reduction in unnecessary visits, base case", "=Visit_reduction", s.PCT_0,
        unit="share of unnecessary visits", how=FROM_INPUTS,
        status="Assumption, set equal to the brief's target", kind="link", key="benefit_cut"),
    Row("Visits avoided a month", "={benefit_visits}*{benefit_cut}", s.COUNT,
        unit="visits a month", how="Baseline visits × reduction", status="Scenario",
        key="visits_avoided"),
    Row("Cost of one technician visit", "=Visit_cost", s.USD, unit="USD a visit",
        how=FROM_INPUTS, status=i.CASE, kind="link", key="benefit_visit_cost"),
    Row("Saving from avoided visits a month", "={visits_avoided}*{benefit_visit_cost}", s.USD,
        unit="USD a month", how="Visits avoided × cost of a visit",
        status="Scenario, not a measured saving: case facts × assumed volume × assumed reduction",
        key="saving_visits", strong=True),
)  # fmt: skip

CALLS = (
    Band("B  Call handling: outage calls avoided"),
    Row("Outage calls a month, pilot population", "=Outage_calls_month", s.COUNT,
        unit="calls a month", how=FROM_INPUTS, status=i.ASSUME, kind="link", key="benefit_calls"),
    Row("Outage calls avoided (deflection)", "=Call_deflection", s.PCT_0,
        unit="share of outage calls", how=FROM_INPUTS, status=i.ASSUME, kind="link",
        key="benefit_deflection"),
    Row("Calls avoided a month", "={benefit_calls}*{benefit_deflection}", s.COUNT,
        unit="calls a month", how="Outage calls × share avoided", status=i.ASSUME,
        key="calls_avoided"),
    Row("Handling cost of one call", "=Call_cost", s.USD, unit="USD a call", how=FROM_INPUTS,
        status=i.ASSUME, kind="link", key="benefit_call_cost"),
    Row("Saving from avoided calls a month, at the unit cost",
        "={calls_avoided}*{benefit_call_cost}", s.USD, unit="USD a month",
        how="Calls avoided × handling cost of a call", status=i.ASSUME, key="saving_calls_total"),
    Row("Share of that saving that is cashable", "=Cashable_share", s.PCT_0,
        unit="share of the saving", how=FROM_INPUTS, status=i.ASSUME, kind="link",
        key="benefit_cashable"),
    Row("of which cashable (counted)", "={saving_calls_total}*{benefit_cashable}", s.USD,
        unit="USD a month", how="Saving × cashable share", status=i.ASSUME, key="saving_calls",
        strong=True),
    Row("of which released staff capacity (not cash; not counted)",
        "={saving_calls_total}-{saving_calls}", s.USD_ZERO, unit="USD a month",
        how="Saving - cashable part",
        status="Shown apart: fixed salaries do not disappear when calls fall",
        key="released_capacity"),
)  # fmt: skip

REPEAT_CONTACTS = (
    Band("C  Repeat contacts within seven days: held at zero in the base case"),
    Row("Counted in the base case?", '=IF(Count_repeat_saving=1,"Yes","No")', s.TEXT,
        how=SWITCH, status="Switch"),
    Row("Outage calls followed by a repeat contact within seven days, baseline",
        f"=IF(ISBLANK(Repeat_rate_baseline),{NOT_ESTABLISHED},Repeat_rate_baseline)", s.PCT_1,
        unit="share of outage calls", how=FROM_INPUTS, status=i.GAP),
    Row("Reduction in repeat contacts", "=Repeat_reduction", s.PCT_0,
        unit="share of repeat contacts", how=FROM_INPUTS,
        status="A target proposed for the pilot; not a result", kind="link"),
    Row("Saving from avoided repeat contacts a month",
        "=Count_repeat_saving*Outage_calls_month*Repeat_rate_baseline*Repeat_reduction*Call_cost",
        s.USD_ZERO, unit="USD a month",
        how="Switch × outage calls × repeat rate × reduction × handling cost of a call",
        status="Zero in the base case", key="saving_repeat", strong=True),
    Remark("Why zero: the baseline repeat rate is not established, and a repeat contact avoided "
           "is also a call avoided, so adding both would count the same call twice "
           "(discovery report, section 6)."),
)  # fmt: skip

CHURN = (
    Band("D  Churn: held at zero in the base case"),
    Row("Counted in the base case?", '=IF(Count_churn_benefit=1,"Yes","No")', s.TEXT,
        how=SWITCH, status="Switch"),
    Row("Relative reduction in churn", "=Churn_reduction", s.PCT_0,
        unit="share of churned customers", how=FROM_INPUTS,
        status="An exploratory target for the pilot; not a result", kind="link"),
    Row("Benefit from lower churn a month",
        "=Count_churn_benefit*Churned_customers_month*Churn_reduction*Retained_customer_value",
        s.USD_ZERO, unit="USD a month",
        how="Switch × customers lost a month × reduction × value of a retained customer",
        status="Zero in the base case", key="benefit_churn", strong=True),
    Remark("Why zero: 'Base-case churn benefit = zero until a controlled pilot can support "
           "causality' (action plan p.10). The baseline and the value of a customer are in no "
           "source."),
)  # fmt: skip

RESULT = (
    Band("E  Result"),
    Row("Benefit counted a month",
        "={saving_visits}+{saving_calls}+{saving_repeat}+{benefit_churn}", s.USD,
        unit="USD a month", how="A + B (cashable) + C + D", status="Scenario",
        key="benefit_month"),
    Row("Recurring cost a month", "={cost_recurring}", s.USD, unit="USD a month",
        how="Costs sheet", status=i.ASSUME, kind="link", key="benefit_recurring"),
    Row("Net benefit a month", "={benefit_month}-{benefit_recurring}", s.USD, unit="USD a month",
        how="Benefit counted - recurring cost",
        status="Scenario; requires cost and benefit validation", key="net_month", strong=True),
    Row("Net benefit a year", "={net_month}*Months_per_year", s.USD, unit="USD a year",
        how="Net benefit a month × months in a year; no seasonality", status="Scenario",
        key="net_year"),
    Row("One-time implementation cost", "={cost_one_time}", s.USD, unit="USD, once",
        how="Costs sheet", status=i.ASSUME, kind="link", key="benefit_one_time"),
    Row("Simple payback",
        '=IF({net_month}>0,{benefit_one_time}/{net_month},"none: the net benefit is not positive")',
        s.RATE_2, unit="months", how="One-time cost ÷ net benefit a month",
        status="Scenario; excludes discounting and tax", key="payback_months", strong=True),
    Row("Net benefit of the first year, after the one-time cost", "={net_year}-{benefit_one_time}",
        s.USD, unit="USD", how="Net benefit a year - one-time cost",
        status="Scenario; excludes discounting and tax", key="net_first_year"),
)  # fmt: skip


def benefits(ws: Worksheet, refs: Refs) -> None:
    lines = Lines(ws, refs, s.title(ws, "Benefits", LEAD)).head(HEAD)
    lines.write(TRUCK_ROLLS + CALLS + REPEAT_CONTACTS + CHURN + RESULT)
    s.widths(ws, WIDTHS)
