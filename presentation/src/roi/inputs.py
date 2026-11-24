"""Every input of the ROI model, once, with its status and its source.

A figure is typed here and nowhere else. `build_roi.py` writes these rows to the `Inputs` sheet,
gives each one a defined name, and every other sheet reaches it by that name. To change the
published model, change a value here and rebuild; to try a what-if, type over the blue cell in
the workbook instead.

Statuses keep four things apart: what the brief states (case fact), what the evaluations
measured on synthetic data (measured in simulation), what the deployed demonstration costs at
AWS list prices, and what nobody has measured (assumption, or not established when there is no
figure at all).
"""

from __future__ import annotations

from dataclasses import dataclass

import style as s

CASE = "Case fact"
SIM = "Measured in simulation"
PACK = "Synthetic data pack (the generator's rates)"
DEMO = "AWS demo, at list prices"
ASSUME = "Assumption"
GAP = "Not established"
CONST = "Constant"

# Statuses whose value cell is filled yellow: replace with SkyLink's own figure.
TO_REPLACE = (ASSUME, GAP)

BRIEF_1 = "Brief p.1"
BRIEF_2 = "Brief p.2, success metrics"
PLAN = "Action plan p.10"
DISCOVERY_6 = "Discovery report, section 6"
TRUCK = "evals/reports/truck_rolls.md"
DETECTION = "evals/reports/outage_detection.md"
CREDITS = "evals/reports/credit_policy.md"
ARCH_COST = "docs/architecture_design.html, 'What the demonstration costs'"
README_AWS = "README.md, 'The demo on AWS'"
BLANK_IS_ZERO = "Blank counts as zero."
NOT_TWICE = (
    "Blank counts as zero. If you fill it in, lower the lump sum above by the same amount, "
    "so that nothing is counted twice."
)


@dataclass(frozen=True)
class Input:
    name: str
    label: str
    value: float | int | None
    unit: str
    status: str
    source: str
    note: str = ""
    fmt: str = s.COUNT


@dataclass(frozen=True)
class Section:
    title: str
    rows: tuple[Input, ...]


CASE_FACTS = Section(
    "1  Case facts, from the brief",
    (
        Input(
            "Broadband_customers",
            "Broadband customers",
            3_500_000,
            "customers",
            CASE,
            BRIEF_1,
            "Context only. Not used in any calculation.",
        ),
        Input(
            "Mobile_customers",
            "Mobile customers",
            8_000_000,
            "customers",
            CASE,
            BRIEF_1,
            "Context only. Not used in any calculation.",
        ),
        Input(
            "Unnecessary_visit_rate",
            "Outage-driven calls that end in an unnecessary technician visit",
            0.30,
            "share of outage calls",
            CASE,
            BRIEF_1,
            "The generator of the synthetic data encodes the same rate (docs/contracts/data.md).",
            s.PCT_0,
        ),
        Input(
            "Visit_cost",
            "Cost of one technician visit",
            650,
            "INR a visit",
            CASE,
            BRIEF_1,
            "",
            s.INR,
        ),
        Input(
            "Target_reduction",
            "Success target: unnecessary visits cut by at least",
            0.50,
            "share of unnecessary visits",
            CASE,
            BRIEF_2,
            "A target for the simulation. Used to test each case against it, never as a saving.",
            s.PCT_0,
        ),
        Input(
            "Detection_target",
            "Success target: outage detected for affected customers, at least",
            0.95,
            "share of affected contacts",
            CASE,
            BRIEF_2,
            "Used on the Simulation evidence sheet only.",
            s.PCT_0,
        ),
        Input(
            "Credit_target",
            "Success target: credits that match the policy",
            1.0,
            "share of cases",
            CASE,
            BRIEF_2,
            "Used on the Simulation evidence sheet only.",
            s.PCT_0,
        ),
    ),
)

BASE_CASE = Section(
    "2  Base case: volume and effect (the action plan's illustration)",
    (
        Input(
            "Outage_calls_month",
            "Outage calls a month in the pilot population",
            10_000,
            "calls a month",
            ASSUME,
            PLAN,
            "Illustrative. Replace with the observed baseline: the discovery report says annual "
            "savings cannot be established without actual outage-contact volumes.",
        ),
        Input(
            "Visit_reduction",
            "Reduction in unnecessary visits, base case",
            0.50,
            "share of unnecessary visits",
            ASSUME,
            PLAN + ": 'Scenario, not measured saving'",
            "Set equal to the brief's target. In simulation the workflow books none of the "
            "unnecessary visits; what staff decide for a customer who insists is not measured "
            "(see Simulation evidence).",
            s.PCT_0,
        ),
        Input(
            "Call_deflection",
            "Outage calls avoided (deflection)",
            0.20,
            "share of outage calls",
            ASSUME,
            PLAN + ": deflection assumed",
            "Not measured by any evaluation.",
            s.PCT_0,
        ),
        Input(
            "Call_cost",
            "Handling cost of one call",
            60,
            "INR a call",
            ASSUME,
            PLAN + ": 'fully avoidable unit cost assumed'",
            "Contact handling only. No visit cost is bundled in it.",
            s.INR,
        ),
        Input(
            "Cashable_share",
            "Share of the call-handling saving that is cashable",
            1.0,
            "share of the saving",
            ASSUME,
            PLAN + ": unit cost assumed fully avoidable; 'separate released staff capacity from "
            "cashable savings'",
            "Fixed salaries do not disappear when calls fall. Lower this to count only what "
            "leaves the cost base; the rest is shown as released capacity and is not counted.",
            s.PCT_0,
        ),
    ),
)

PILOT_COST = Section(
    "3  Cost of a pilot (assumptions; the itemised lines are not established)",
    (
        Input(
            "One_time_cost",
            "One-time implementation cost",
            1_200_000,
            "INR, once",
            ASSUME,
            PLAN,
            "Illustrative; excludes discounting and tax.",
            s.INR,
        ),
        Input(
            "Recurring_cost_lump",
            "Recurring delivery cost, lump sum",
            400_000,
            "INR a month",
            ASSUME,
            PLAN,
            "'Illustrative pilot assumption, not a cloud quote.' Carries every recurring cost of "
            "the base case.",
            s.INR,
        ),
        Input(
            "Pilot_cloud_USD_month",
            "Cloud cost of a pilot, itemised",
            None,
            "USD a month",
            GAP,
            ARCH_COST + ": the estimate 'has not been made yet'",
            NOT_TWICE,
            s.USD_2,
        ),
        Input(
            "Model_cost_USD_per_contact",
            "Language-model usage cost of one contact, itemised",
            None,
            "USD a contact",
            GAP,
            "docs/EXECUTION_PLAN.md: token counts of a model call are not stored",
            "Not measured. " + NOT_TWICE,
            s.USD_4,
        ),
        Input(
            "Labour_cost_month",
            "Integration support and human-review labour, itemised",
            None,
            "INR a month",
            GAP,
            PLAN + ": 'R1 adds integration, support and human-review labour'",
            "Not estimated. " + NOT_TWICE,
            s.INR,
        ),
    ),
)

HELD_AT_ZERO = Section(
    "4  Benefits held at zero in the base case",
    (
        Input(
            "Count_repeat_saving",
            "Count the repeat-contact saving? (1 = yes, 0 = no)",
            0,
            "switch",
            ASSUME,
            DISCOVERY_6 + ": 'Do not double count deflection and repeat-contact savings'",
            "0 in the base case: the baseline is not established, and a repeat contact avoided "
            "is also a call avoided.",
            s.COUNT_ZERO,
        ),
        Input(
            "Repeat_rate_baseline",
            "Outage calls followed by a repeat contact within seven days, baseline",
            None,
            "share of outage calls",
            GAP,
            "Discovery report, section 7: a baseline still required",
            BLANK_IS_ZERO,
            s.PCT_1,
        ),
        Input(
            "Repeat_reduction",
            "Reduction in seven-day repeat contacts",
            0.20,
            "share of repeat contacts",
            ASSUME,
            PLAN + ": proposed pilot target",
            "A target proposed for the pilot. Not a result.",
            s.PCT_0,
        ),
        Input(
            "Count_churn_benefit",
            "Count the churn benefit? (1 = yes, 0 = no)",
            0,
            "switch",
            ASSUME,
            PLAN + "; workshop decision D13 (discovery report)",
            "'Base-case churn benefit = zero until a controlled pilot can support causality.'",
            s.COUNT_ZERO,
        ),
        Input(
            "Churned_customers_month",
            "Customers lost to churn a month in the pilot population, baseline",
            None,
            "customers a month",
            GAP,
            "In no source",
            BLANK_IS_ZERO,
        ),
        Input(
            "Churn_reduction",
            "Relative reduction in churn",
            0.05,
            "share of churned customers",
            ASSUME,
            PLAN + ": exploratory pilot target (60-day churn)",
            "'Churn impact cannot be established by the capstone deadline.'",
            s.PCT_0,
        ),
        Input(
            "Retained_customer_value",
            "Value of one retained customer",
            None,
            "INR a customer",
            GAP,
            "In no source",
            BLANK_IS_ZERO,
            s.INR,
        ),
    ),
)

_VOLUMES = (1_000, 5_000, 10_000, 25_000, 50_000)

SETTINGS = Section(
    "5  Scenario and sensitivity settings",
    (
        *(
            Input(
                f"Scenario_volume_{n}",
                f"Volume scenario {n}: outage contacts a month",
                volume,
                "contacts a month",
                ASSUME,
                DISCOVERY_6,
                "",
            )
            for n, volume in enumerate(_VOLUMES, start=1)
        ),
        Input(
            "Low_reduction",
            "Low case: reduction in unnecessary visits",
            0.25,
            "share of unnecessary visits",
            ASSUME,
            PLAN + ", sensitivity row",
            "",
            s.PCT_0,
        ),
        Input(
            "High_reduction",
            "High case: reduction in unnecessary visits",
            0.75,
            "share of unnecessary visits",
            ASSUME,
            PLAN + ", sensitivity row",
            "",
            s.PCT_0,
        ),
        Input(
            "Floor_reduction",
            "Two-way table, lowest reduction",
            0.0,
            "share of unnecessary visits",
            ASSUME,
            TRUCK + ", first table",
            "The floor: nothing is cut if every customer insists and staff always send a "
            "technician.",
            s.PCT_0,
        ),
        Input(
            "Ceiling_reduction",
            "Two-way table, highest reduction",
            1.0,
            "share of unnecessary visits",
            ASSUME,
            TRUCK + ", first table",
            "The ceiling: what the workflow itself books in simulation. Reached end to end only "
            "if no person sends a technician to a customer who insists.",
            s.PCT_0,
        ),
    ),
)

DEMO_CLOUD = Section(
    "6  Cloud cost of the demonstration (as deployed on AWS, at list prices)",
    (
        Input(
            "Demo_server_USD_hour",
            "Server: one EC2 t3.medium in Mumbai, on demand",
            0.0448,
            "USD an hour",
            DEMO,
            README_AWS + "; deploy/aws/terraform/variables.tf",
            "",
            s.USD_4,
        ),
        Input(
            "Demo_database_USD_hour",
            "Database: one RDS db.t4g.micro, PostgreSQL 17",
            0.021,
            "USD an hour",
            DEMO,
            README_AWS + "; deploy/aws/terraform/database.tf",
            "",
            s.USD_4,
        ),
        Input(
            "Demo_other_USD_day",
            "Storage for both, the public address and the secrets",
            0.32,
            "USD a day",
            DEMO,
            ARCH_COST + ": 'About $0.32'",
            "",
            s.USD_2,
        ),
        Input(
            "Demo_days_kept",
            "Days the demonstration is kept running",
            9,
            "days",
            ASSUME,
            README_AWS + ": first release 22 Nov 2026; brief p.2: presentation 30 Nov 2026",
            "22 to 30 November 2026 inclusive. The deployment is to be destroyed after the "
            "presentation.",
        ),
        Input(
            "USD_INR",
            "Exchange rate: INR for one USD",
            92.92,
            "INR per USD",
            ASSUME,
            "European Central Bank reference rates of 23 November 2026 (USD to INR cross rate), "
            "read from api.frankfurter.dev",
            "An outside figure, not from the repository. Replace with the rate of the day.",
            s.RATE_2,
        ),
    ),
)

CONSTANTS = Section(
    "7  Constants",
    (
        Input(
            "Months_per_year",
            "Months in a year",
            12,
            "months",
            CONST,
            "",
            "Annual figures are the monthly figure times twelve: no seasonality.",
        ),
        Input("Hours_per_day", "Hours in a day", 24, "hours", CONST, "", ""),
        Input(
            "Days_per_month",
            "Days in a month, for the cloud cost",
            30,
            "days",
            CONST,
            "",
            "A round month.",
        ),
        Input(
            "Per_contacts_basis",
            "Basis of the per-contact figures",
            1_000,
            "contacts",
            CONST,
            DISCOVERY_6,
            "",
        ),
    ),
)

_BY_KIND = TRUCK + ", 'By kind of contact'"
_RESTS_ON = TRUCK + ", 'What this rests on'"

SIMULATION = Section(
    "8  Measured in simulation, on synthetic stand-in data (runs of 17 November 2026)",
    (
        Input(
            "Sim_pack_contacts",
            "Labelled contacts in the synthetic data pack",
            20_000,
            "contacts",
            PACK,
            _RESTS_ON,
            "The generator's pack, not SkyLink's contacts.",
        ),
        Input(
            "Sim_pack_outage_contacts",
            "of which labelled as outage contacts",
            9_000,
            "contacts",
            PACK,
            "evals/README.md",
            "",
        ),
        Input(
            "Sim_base_visits_outage",
            "Baseline visits sent during an outage (unnecessary)",
            2_700,
            "visits",
            PACK,
            _BY_KIND,
            "The generator's rate, not SkyLink's.",
        ),
        Input(
            "Sim_base_visits_line",
            "Baseline visits for a line fault (needed)",
            1_225,
            "visits",
            PACK,
            _BY_KIND,
            "",
        ),
        Input(
            "Sim_base_visits_home",
            "Baseline visits for a fault the customer could fix (unnecessary)",
            525,
            "visits",
            PACK,
            _BY_KIND,
            "",
        ),
        Input("Sim_replayed_outage", "Outage contacts replayed", 18, "contacts", SIM, _BY_KIND, ""),
        Input(
            "Sim_replayed_line", "Line-fault contacts replayed", 10, "contacts", SIM, _BY_KIND, ""
        ),
        Input(
            "Sim_replayed_home",
            "Home-equipment contacts replayed",
            10,
            "contacts",
            SIM,
            _BY_KIND,
            "",
        ),
        Input(
            "Sim_booked_outage",
            "Outage contacts for which the workflow booked a visit",
            0,
            "contacts",
            SIM,
            _BY_KIND,
            "",
            s.COUNT_ZERO,
        ),
        Input(
            "Sim_booked_line",
            "Line-fault contacts for which the workflow booked a visit",
            10,
            "contacts",
            SIM,
            _BY_KIND,
            "",
            s.COUNT_ZERO,
        ),
        Input(
            "Sim_booked_home",
            "Home-equipment contacts for which the workflow booked a visit",
            0,
            "contacts",
            SIM,
            _BY_KIND,
            "",
            s.COUNT_ZERO,
        ),
        Input(
            "Sim_person_outage",
            "Outage contacts that ended with a person, no visit",
            18,
            "contacts",
            SIM,
            _BY_KIND,
            "The simulated customer went on asking.",
            s.COUNT_ZERO,
        ),
        Input(
            "Sim_person_line",
            "Line-fault contacts that ended with a person, no visit",
            0,
            "contacts",
            SIM,
            _BY_KIND,
            "",
            s.COUNT_ZERO,
        ),
        Input(
            "Sim_person_home",
            "Home-equipment contacts that ended with a person, no visit",
            10,
            "contacts",
            SIM,
            _BY_KIND,
            "",
            s.COUNT_ZERO,
        ),
        Input(
            "Sim_minutes_to_booking",
            "Median minutes from contact to the booking of a needed visit",
            10,
            "minutes",
            SIM,
            TRUCK + ", result",
            "The price of the hold before a visit is offered.",
        ),
        Input(
            "Sim_detection_rate",
            "Outage detected for affected contacts",
            0.9747,
            "share of affected contacts",
            SIM,
            DETECTION + ", result",
            "Weighted by how often each kind of contact occurs. What is missed is mobile, "
            "which has no remote test.",
            s.PCT_2,
        ),
        Input(
            "Sim_detection_replayed",
            "Contacts replayed for detection",
            140,
            "contacts",
            SIM,
            DETECTION + ", result",
            "",
        ),
        Input(
            "Sim_detection_nodes",
            "Nodes those contacts were on",
            32,
            "nodes",
            SIM,
            DETECTION + ", result",
            "",
        ),
        Input(
            "Sim_credit_cases",
            "Credit cases in the grid",
            4_050,
            "cases",
            SIM,
            CREDITS + ", result",
            "",
        ),
        Input(
            "Sim_credit_matched",
            "Grid cases that match an independent calculation",
            4_050,
            "cases",
            SIM,
            CREDITS + ", result",
            "",
        ),
        Input(
            "Sim_credit_hand",
            "Credit amounts worked out by hand",
            14,
            "cases",
            SIM,
            CREDITS + ", result",
            "",
        ),
        Input(
            "Sim_credit_hand_matched",
            "Hand-worked amounts that match",
            14,
            "cases",
            SIM,
            CREDITS + ", result",
            "",
        ),
    ),
)

SECTIONS: tuple[Section, ...] = (
    CASE_FACTS,
    BASE_CASE,
    PILOT_COST,
    HELD_AT_ZERO,
    SETTINGS,
    DEMO_CLOUD,
    CONSTANTS,
    SIMULATION,
)

VOLUME_NAMES: tuple[str, ...] = tuple(f"Scenario_volume_{n}" for n in range(1, len(_VOLUMES) + 1))
SWITCH_NAMES: tuple[str, ...] = ("Count_repeat_saving", "Count_churn_benefit")
