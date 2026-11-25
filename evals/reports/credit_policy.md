# Credits match the policy

**What this proves.** For every case of a grid on the policy's boundaries the credit engine gives the same eligibility, the same reason for a refusal, the same amount to the cent and the same approval level as an independent calculation in exact arithmetic, and as amounts worked out by hand.

**Target.** 100% of cases match (brief: credits match policy 100%).

**Result (met).** 4050 of 4050 grid cases match (100.0%); 14 of 14 hand-worked amounts match.

Written by the run of 2026-11-25T18:52:04+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: none (no language model takes part in this measurement).

## Grid, by expected outcome

| Outcome | Cases |
|---|---|
| ALREADY_CREDITED | 162 |
| BELOW_MINIMUM_DURATION | 1260 |
| CAP_REACHED | 324 |
| ELIGIBLE | 1134 |
| NOT_AFFECTED | 288 |
| OUTAGE_NOT_RESOLVED | 18 |
| PLANNED_WORK | 576 |
| SERVICE_SUSPENDED | 288 |

## Hand-worked amounts

| Case | By hand | Engine | Approval |
|---|---|---|---|
| 4 h on a 999 plan: 5.55 by the formula, raised to the broadband minimum | 50.00 | 50.00 | agent |
| one minute short of the broadband minimum duration | BELOW_MINIMUM_DURATION | BELOW_MINIMUM_DURATION | none |
| 10 h on a 3999 plan: 55.5416... | 55.54 | 55.54 | agent |
| 6 h on a 9999 plan: 83.325, a tie that rounds up | 83.33 | 83.33 | agent |
| 26 h on a 9999 plan at 1.5: 541.6125 | 541.61 | 541.61 | supervisor |
| 72 h on a 999 plan at 2.0: 199.80 | 199.80 | 199.80 | agent |
| one minute short of three days stays at 1.5: 72 billable hours, 149.85 | 149.85 | 149.85 | agent |
| 24 h on a 399 mobile plan: 13.30, raised to the mobile minimum | 25.00 | 25.00 | agent |
| 5 h is below the mobile minimum duration of a day | BELOW_MINIMUM_DURATION | BELOW_MINIMUM_DURATION | none |
| the cap leaves exactly 500.00: an agent may approve | 500.00 | 500.00 | agent |
| the cap leaves 500.01: a supervisor must | 500.01 | 500.01 | supervisor |
| the cap leaves 499.99 | 499.99 | 499.99 | agent |
| the cap is used up | CAP_REACHED | CAP_REACHED | none |
| planned maintenance is excluded however long it ran | PLANNED_WORK | PLANNED_WORK | none |

## What this rests on

- Policy v0-placeholder is a synthetic placeholder (assumption A2), not SkyLink's real policy. The evaluation shows the engine follows the policy file it is given; it says nothing about whether that policy is the right one.
- The oracle is a second implementation by the same author from the same policy document. The hand-worked amounts are the check that does not share that reading.
- This covers the calculation. That the workflow proposes exactly the calculated amount, once, and applies it only after approval is covered by the end-to-end tests (tests/e2e/test_service_credits.py, test_credit_lifecycle.py).
