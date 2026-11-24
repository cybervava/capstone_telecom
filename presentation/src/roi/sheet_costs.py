"""The Costs sheet: what a pilot would cost (assumed), and what the demonstration costs on AWS
(from its configuration, at list prices), kept apart.

A cost keeps the currency of its source in the value column; the rupee column converts it with
the exchange rate on the Inputs sheet."""

# The multiplication sign in a working that is shown to the reader is meant.
# ruff: noqa: RUF001

from __future__ import annotations

import inputs as i
import style as s
from grid import Band, Lines, Refs, Remark, Row
from openpyxl.worksheet.worksheet import Worksheet

HEAD = ("Line", "Value", "Unit", "In INR", "Status", "Source or working")
LEAD = (
    "What a pilot would cost (assumed), and what the demonstration costs on AWS (from its "
    "configuration, at list prices). The two are kept apart.",
    "A cost is quoted in the currency of its source. The rupee column uses the exchange rate "
    "on the Inputs sheet, which is an assumption with its date.",
)
WIDTHS = {"A": 56, "B": 16, "C": 16, "D": 18, "E": 26, "F": 70}
SAME = "={b}"
IN_RUPEES = "={b}*USD_INR"
NOT_ESTABLISHED = '"not established"'
README_AWS = "README.md, 'The demo on AWS'"
ARCH = "docs/architecture_design.html"

PILOT = (
    Band("Pilot: one-time cost"),
    Row("One-time implementation cost", "=One_time_cost", s.INR, unit="INR, once",
        status=i.ASSUME, how="Action plan p.10: illustrative; excludes discounting and tax",
        kind="link", inr=SAME, inr_key="cost_one_time", strong=True),
    Band("Pilot: recurring cost a month"),
    Row("Recurring delivery cost, lump sum", "=Recurring_cost_lump", s.INR, unit="INR a month",
        status=i.ASSUME, how="Action plan p.10: 'Illustrative pilot assumption, not a cloud quote'",
        kind="link", inr=SAME, inr_key="cost_lump"),
    Row("Itemised: cloud cost of a pilot", "=Pilot_cloud_USD_month", s.USD_2, unit="USD a month",
        status=i.GAP, how=f"{ARCH}: the estimate 'has not been made yet'",
        kind="link", inr=IN_RUPEES, inr_key="cost_cloud"),
    Row("Itemised: language-model usage", "=Model_cost_USD_per_contact*Outage_calls_month",
        s.USD_2, unit="USD a month", status=i.GAP,
        how="Cost of one contact × outage calls a month. Not measured: the token counts of a "
            "model call are not stored (docs/EXECUTION_PLAN.md)",
        inr=IN_RUPEES, inr_key="cost_model"),
    Row("Itemised: integration support and human-review labour", "=Labour_cost_month", s.INR,
        unit="INR a month", status=i.GAP, how="Action plan p.10 names it; not estimated",
        kind="link", inr=SAME, inr_key="cost_labour"),
    Row("Recurring cost a month, total", None, s.INR, unit="INR a month", status=i.ASSUME,
        how="The lump sum plus the itemised lines. In the base case the lump sum is all of it",
        inr="={cost_lump}+{cost_cloud}+{cost_model}+{cost_labour}", inr_key="cost_recurring",
        strong=True),
    Row("Recurring cost a year", None, s.INR, unit="INR a year", status=i.ASSUME,
        how="Monthly total × months in a year", inr="={cost_recurring}*Months_per_year",
        inr_key="cost_recurring_year"),
    Band("Pilot: the first year"),
    Row("One-time cost and a year of recurring cost", None, s.INR, unit="INR", status=i.ASSUME,
        how="One-time cost + recurring cost a year",
        inr="={cost_one_time}+{cost_recurring_year}", inr_key="cost_first_year"),
)  # fmt: skip

DEMONSTRATION = (
    Band("Deployment (cloud) cost of the demonstration: the capstone's own budget, apart from "
         "the pilot"),
    Row("Exchange rate used", "=USD_INR", s.RATE_2, unit="INR per USD", status=i.ASSUME,
        how="European Central Bank reference rates of 23 November 2026; see Inputs", kind="link"),
    Row("Server: one EC2 t3.medium in Mumbai, on demand", "=Demo_server_USD_hour", s.USD_4,
        unit="USD an hour", status=i.DEMO, how=README_AWS, kind="link", key="demo_server_hour"),
    Row("Database: one RDS db.t4g.micro, PostgreSQL 17", "=Demo_database_USD_hour", s.USD_4,
        unit="USD an hour", status=i.DEMO, how=README_AWS, kind="link",
        key="demo_database_hour"),
    Row("Server, a day", "={demo_server_hour}*Hours_per_day", s.USD_2, unit="USD a day",
        status=i.DEMO, how="Hourly price × hours in a day", inr=IN_RUPEES,
        key="demo_server_day"),
    Row("Database, a day", "={demo_database_hour}*Hours_per_day", s.USD_2, unit="USD a day",
        status=i.DEMO, how="Hourly price × hours in a day", inr=IN_RUPEES,
        key="demo_database_day"),
    Row("Storage for both, the public address and the secrets, a day", "=Demo_other_USD_day",
        s.USD_2, unit="USD a day", status=i.DEMO,
        how=f"{ARCH}, 'What the demonstration costs'", kind="link", inr=IN_RUPEES,
        key="demo_other_day"),
    Row("The demonstration, a day", "={demo_server_day}+{demo_database_day}+{demo_other_day}",
        s.USD_2, unit="USD a day", status=i.DEMO,
        how="Sum of the three lines above. README.md: 'about $1.90 a day'", inr=IN_RUPEES,
        key="demo_day_usd", inr_key="demo_day_inr", strong=True),
    Row("Days the demonstration is kept running", "=Demo_days_kept", s.COUNT, unit="days",
        status=i.ASSUME, kind="link", key="demo_days",
        how="22 to 30 November 2026 inclusive; to be destroyed after the presentation (README.md)"),
    Row("The demonstration, for the days it is kept", "={demo_day_usd}*{demo_days}", s.USD_2,
        unit="USD", status="List price × assumed days",
        how="Daily cost × days kept. The spend is capped by time: the deployment is destroyed "
            "after the presentation",
        inr=IN_RUPEES, key="demo_total_usd", inr_key="demo_total_inr"),
    Row("For scale: the same server and database kept for a month",
        "={demo_day_usd}*Days_per_month", s.USD_2, unit="USD a month",
        status="List price × a round month",
        how="Daily cost × days in a month. One server and one database in one zone: not a pilot",
        inr=IN_RUPEES, key="demo_month_usd", inr_key="demo_month_inr"),
    Remark("Not in these figures: the language model's fees for the demonstration "
           "(OpenAI gpt-5.4-mini). Model usage is not recorded, so no figure is given."),
)  # fmt: skip

PILOT_CLOUD = (
    Band("Deployment (cloud) cost of a pilot: not estimated"),
    Row("Cloud cost of a pilot",
        f"=IF(ISBLANK(Pilot_cloud_USD_month),{NOT_ESTABLISHED},Pilot_cloud_USD_month)", s.USD_2,
        unit="USD a month", status=i.GAP,
        how=f"{ARCH}: 'That estimate depends on SkyLink's real contact volumes and has not been "
            "made yet.' Enter it on the Inputs sheet when it is made"),
    Remark(f"A pilot would add ({ARCH}, 'What a pilot would change'): at least two copies of each "
           "program across two zones, behind a load balancer;"),
    Remark("a standby database in a second zone; a private, encrypted link to SkyLink's data "
           "centre; a managed queue and a managed stream; and the AI model's fees."),
    Remark("None of that has been built or priced. The recurring lump sum above is the action "
           "plan's illustration and stands in for all of it."),
)  # fmt: skip


def costs(ws: Worksheet, refs: Refs) -> None:
    lines = Lines(ws, refs, s.title(ws, "Costs", LEAD), rupees=True).head(HEAD)
    lines.write(PILOT + DEMONSTRATION + PILOT_CLOUD)
    s.widths(ws, WIDTHS)
