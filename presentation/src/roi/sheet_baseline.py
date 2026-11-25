"""The Baseline sheet: what unnecessary technician visits cost before any change."""

# The multiplication sign in a working that is shown to the reader is meant.
# ruff: noqa: RUF001

from __future__ import annotations

import inputs as i
import style as s
from grid import Band, Lines, Refs, Row
from openpyxl.worksheet.worksheet import Worksheet

HEAD = ("Line", "Value", "Unit", "How it is worked out", "Status")
NOT_ESTABLISHED = '"not established"'
LEAD = (
    "What unnecessary technician visits cost before any change: the brief's two case facts, "
    "applied to an assumed pilot volume.",
)
WIDTHS = {"A": 56, "B": 18, "C": 28, "D": 58, "E": 34}
FROM_INPUTS = "Inputs"

CUSTOMERS = (
    Band("Customers (context only; not used in any calculation)"),
    Row("Broadband customers", "=Broadband_customers", s.COUNT, unit="customers",
        how=FROM_INPUTS, status=i.CASE, kind="link"),
    Row("Mobile customers", "=Mobile_customers", s.COUNT, unit="customers",
        how=FROM_INPUTS, status=i.CASE, kind="link"),
)  # fmt: skip

VISITS = (
    Band("Unnecessary technician visits"),
    Row("Outage calls a month, pilot population", "=Outage_calls_month", s.COUNT,
        unit="calls a month", how=FROM_INPUTS, status=i.ASSUME, kind="link", key="pilot_calls"),
    Row("Share that ends in an unnecessary technician visit", "=Unnecessary_visit_rate", s.PCT_0,
        unit="share of outage calls", how=FROM_INPUTS, status=i.CASE, kind="link",
        key="visit_rate"),
    Row("Unnecessary visits a month", "={pilot_calls}*{visit_rate}", s.COUNT,
        unit="visits a month", how="Outage calls × share", status="Case fact × assumed volume",
        key="visits_month", strong=True),
    Row("Cost of one technician visit", "=Visit_cost", s.USD, unit="USD a visit",
        how=FROM_INPUTS, status=i.CASE, kind="link", key="visit_cost"),
    Row("Baseline waste a month", "={visits_month}*{visit_cost}", s.USD, unit="USD a month",
        how="Unnecessary visits × cost of a visit", status="Case facts × assumed volume",
        key="waste_month"),
    Row("Baseline waste a year", "={waste_month}*Months_per_year", s.USD, unit="USD a year",
        how="Monthly waste × months in a year; no seasonality",
        status="Case facts × assumed volume", key="waste_year"),
)  # fmt: skip

PER_CALL = (
    Band("For each outage call"),
    Row("Avoidable dispatch burden of one outage call", "={visit_rate}*{visit_cost}", s.USD_2,
        unit="USD a call", how="Share × cost of a visit", status="Case facts",
        key="burden_per_call"),
    Row("Avoided for each outage call at the base-case reduction",
        "={burden_per_call}*Visit_reduction", s.USD_2, unit="USD a call",
        how="Burden × reduction in unnecessary visits", status="Case facts × assumed reduction",
        key="saving_per_call"),
)  # fmt: skip

PER_BLOCK = (
    Band("For a block of outage contacts (the discovery report's basis)"),
    Row("Outage contacts in the block", "=Per_contacts_basis", s.COUNT, unit="contacts",
        how=FROM_INPUTS, status=i.CONST, kind="link", key="block_size"),
    Row("Unnecessary visits in the block", "={block_size}*{visit_rate}", s.COUNT, unit="visits",
        how="Contacts × share", status="Case fact", key="block_visits"),
    Row("Baseline waste of the block", "={block_visits}*{visit_cost}", s.USD, unit="USD",
        how="Unnecessary visits × cost of a visit", status="Case facts", key="block_waste"),
    Row("Visits avoided at the base-case reduction", "={block_visits}*Visit_reduction", s.COUNT,
        unit="visits", how="Unnecessary visits × reduction",
        status="Case fact × assumed reduction", key="block_avoided"),
    Row("Saving at the base-case reduction", "={block_avoided}*{visit_cost}", s.USD, unit="USD",
        how="Visits avoided × cost of a visit", status="Case facts × assumed reduction",
        key="block_saving", strong=True),
)  # fmt: skip

CALL_HANDLING = (
    Band("Call handling"),
    Row("Handling cost of one call", "=Call_cost", s.USD, unit="USD a call", how=FROM_INPUTS,
        status=i.ASSUME, kind="link", key="call_cost"),
    Row("Handling cost of the outage calls a month", "={pilot_calls}*{call_cost}", s.USD,
        unit="USD a month", how="Outage calls × handling cost of a call",
        status="Assumed volume × assumed unit cost", key="handling_month"),
)  # fmt: skip

GAPS = (
    Band("Baselines that are not established"),
    Row("Outage calls followed by a repeat contact within seven days",
        f"=IF(ISBLANK(Repeat_rate_baseline),{NOT_ESTABLISHED},Repeat_rate_baseline)", s.PCT_1,
        unit="share of outage calls", status=i.GAP,
        how="The discovery report (section 7) lists it among the baselines still required"),
    Row("Customers lost to churn a month, pilot population",
        f"=IF(ISBLANK(Churned_customers_month),{NOT_ESTABLISHED},Churned_customers_month)",
        s.COUNT, unit="customers a month", status=i.GAP, how="In no source"),
)  # fmt: skip


def baseline(ws: Worksheet, refs: Refs) -> None:
    lines = Lines(ws, refs, s.title(ws, "Baseline", LEAD)).head(HEAD)
    lines.write(CUSTOMERS + VISITS + PER_CALL + PER_BLOCK + CALL_HANDLING + GAPS)
    s.widths(ws, WIDTHS)
