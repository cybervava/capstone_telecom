"""Check presentation/G3_Telco_ROI.xlsx after a build, or after its inputs were changed and saved.

    cd <repo> && uv run --no-project --with openpyxl python presentation/src/roi/check_roi.py

It reads the results stored in the workbook (it calculates nothing in the workbook) and checks:

1. every formula has a result, and none is an error;
2. each headline result equals an independent calculation, done here in Python from the inputs
   as they stand in the workbook;
3. while the inputs are the published ones, the results equal the figures of the action plan
   (p.10) and of the discovery report (section 6) to the rupee.

Exit status 0 when all of it holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import inputs as i
from build_roi import OUTPUT, build
from openpyxl import Workbook, load_workbook

MONTHS = 12
BLOCK = 1_000
TWO_WAY = dict(
    zip(
        "BCDEF",
        (
            "Floor_reduction",
            "Low_reduction",
            "Visit_reduction",
            "High_reduction",
            "Ceiling_reduction",
        ),
        strict=True,
    )
)
# Action plan p.10 and discovery report section 6, as published.
PUBLISHED = {
    "visits_month": 3_000,
    "saving_visits": 975_000,
    "saving_calls": 120_000,
    "cost_recurring": 400_000,
    "net_month": 695_000,
    "cost_one_time": 1_200_000,
    "block_visits": 300,
    "block_waste": 195_000,
    "block_avoided": 150,
    "block_saving": 97_500,
    "volume_1_waste": 2_340_000,
    "volume_1_saving": 1_170_000,
    "volume_2_waste": 11_700_000,
    "volume_2_saving": 5_850_000,
    "volume_3_waste": 23_400_000,
    "volume_3_saving": 11_700_000,
    "volume_4_waste": 58_500_000,
    "volume_4_saving": 29_250_000,
    "volume_5_waste": 117_000_000,
    "volume_5_saving": 58_500_000,
    "sens_low_visits": 487_500,
    "sens_base_visits": 975_000,
    "sens_high_visits": 1_462_500,
    "sens_low_net": 207_500,
    "sens_base_net": 695_000,
    "sens_high_net": 1_182_500,
    "saving_repeat": 0,
    "benefit_churn": 0,
}
PUBLISHED_PAYBACK_MONTHS = 1.73


def read_inputs(book: Workbook) -> dict[str, float]:
    """Each named input's value as it stands in the workbook; a blank counts as zero."""
    values = {}
    for name, definition in book.defined_names.items():
        if name.startswith("_xlnm"):
            continue
        sheet, ref = definition.attr_text.replace("'", "").replace("$", "").split("!")
        values[name] = book[sheet][ref].value or 0
    return values


def expected(p: dict[str, float]) -> dict[str, float]:
    """The model again, independently of the workbook's formulas."""
    visits = p["Outage_calls_month"] * p["Unnecessary_visit_rate"]
    call_total = p["Outage_calls_month"] * p["Call_deflection"] * p["Call_cost"]
    fixed = _fixed_part(p, p["Outage_calls_month"])
    net = visits * p["Visit_reduction"] * p["Visit_cost"] + fixed
    out = {
        "visits_month": visits,
        "waste_month": visits * p["Visit_cost"],
        "waste_year": visits * p["Visit_cost"] * MONTHS,
        "block_saving": BLOCK
        * p["Unnecessary_visit_rate"]
        * p["Visit_reduction"]
        * p["Visit_cost"],
        "saving_visits": visits * p["Visit_reduction"] * p["Visit_cost"],
        "saving_calls_total": call_total,
        "saving_calls": call_total * p["Cashable_share"],
        "released_capacity": call_total * (1 - p["Cashable_share"]),
        "cost_recurring": _recurring(p),
        "net_month": net,
        "net_year": net * MONTHS,
        "net_first_year": net * MONTHS - p["One_time_cost"],
        "case_base_net": net,
        "sens_base_net": net,
    }
    for case, name in (("low", "Low_reduction"), ("high", "High_reduction")):
        out[f"case_{case}_net"] = out[f"sens_{case}_net"] = (
            visits * p[name] * p["Visit_cost"] + fixed
        )
    return out | _expected_by_volume(p)


def _recurring(p: dict[str, float]) -> float:
    usd = p["Pilot_cloud_USD_month"] + p["Model_cost_USD_per_contact"] * p["Outage_calls_month"]
    return p["Recurring_cost_lump"] + usd * p["USD_INR"] + p["Labour_cost_month"]


def _fixed_part(p: dict[str, float], volume: float) -> float:
    """What a net benefit holds besides the saving from avoided visits, at a volume of calls."""
    calls = volume * p["Call_deflection"] * p["Call_cost"] * p["Cashable_share"]
    repeat = (
        p["Count_repeat_saving"] * volume * p["Repeat_rate_baseline"] * p["Repeat_reduction"]
    ) * p["Call_cost"]
    churn = (p["Count_churn_benefit"] * p["Churned_customers_month"] * p["Churn_reduction"]) * p[
        "Retained_customer_value"
    ]
    return calls + repeat + churn - _recurring(p)


def _expected_by_volume(p: dict[str, float]) -> dict[str, float]:
    out = {}
    for number, name in enumerate(i.VOLUME_NAMES, start=1):
        waste = p[name] * p["Unnecessary_visit_rate"] * MONTHS * p["Visit_cost"]
        out[f"volume_{number}_waste"] = waste
        out[f"volume_{number}_saving"] = waste * p["Visit_reduction"]
        for column, cut in TWO_WAY.items():
            visits = p[name] * p["Unnecessary_visit_rate"] * p[cut] * p["Visit_cost"]
            out[f"two_way_{number}_{column}"] = visits + _fixed_part(p, p[name])
    return out


def unresolved(formulas: Workbook, results: Workbook) -> list[str]:
    """Formula cells without a result, or whose result is an error."""
    problems = []
    for sheet in formulas.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                    continue
                result = results[sheet.title][cell.coordinate].value
                if result is None or (isinstance(result, str) and result.startswith("#")):
                    problems.append(f"{sheet.title}!{cell.coordinate}: {result!r}")
    return problems


def differences(results: Workbook, cells: dict[str, str], want: dict[str, float]) -> list[str]:
    problems = []
    for key, value in want.items():
        sheet, ref = cells[key].split("!")
        got = results[sheet][ref].value
        close = isinstance(got, int | float) and abs(got - value) <= 1e-6 * max(1.0, abs(value))
        if not close:
            problems.append(f"{key} ({cells[key]}): workbook {got!r}, expected {value!r}")
    return problems


def published_inputs() -> dict[str, float]:
    return {item.name: item.value or 0 for section in i.SECTIONS for item in section.rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbook", nargs="?", type=Path, default=OUTPUT)
    args = parser.parse_args()
    cells = build()[1].all()
    results = load_workbook(args.workbook, data_only=True)
    values = read_inputs(results)
    problems = unresolved(load_workbook(args.workbook), results)
    problems += differences(results, cells, expected(values))
    if values == published_inputs():
        problems += differences(results, cells, PUBLISHED)
        sheet, ref = cells["payback_months"].split("!")
        if round(results[sheet][ref].value, 2) != PUBLISHED_PAYBACK_MONTHS:
            problems.append(f"payback is not {PUBLISHED_PAYBACK_MONTHS} months")
        sys.stdout.write("inputs are the published ones: checked against the published figures\n")
    else:
        sys.stdout.write("inputs differ from the published ones: published figures not checked\n")
    for problem in problems:
        sys.stderr.write(f"FAIL {problem}\n")
    sys.stdout.write(f"{args.workbook}: {'FAILED' if problems else 'ok'}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
