# The same situation, the same outcome, in both segments

**What this proves.** Consumer and SME customers put through the same situations meet with the same messages, the same handoffs at the same priority, the same decision on a visit and the same rule for a credit. The customer's segment is not an input to any decision; this shows it by outcome.

**Target.** No difference between the segments in any situation, other than the amount of a credit and what follows from the amount, both explained by the monthly fee.

**Result (met).** 8 of 8 situations end the same way in both segments (100.0%), over 80 customers.

Written by the run of 2026-11-25T17:34:41+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: offline (keyword rules stand in for the language model; none was called).

## Outcomes by situation

| Situation | Consumer | SME | Same |
|---|---|---|---|
| An outage on the customer's node | 5 of them: outage_active.v1 | 5 of them: outage_active.v1 | yes |
| An outage of five hours has ended | 5 of them: outage_active.v1 → outage_restored.v1, credit: the policy's amount, 1 approval(s) waiting | 5 of them: outage_active.v1 → outage_restored.v1, credit: the policy's amount, 1 approval(s) waiting | yes |
| An outage of three days has ended | 5 of them: outage_active.v1 → outage_restored.v1, credit: the policy's amount, 1 approval(s) waiting | 5 of them: outage_active.v1 → outage_restored.v1, credit: the policy's amount, 1 approval(s) waiting | yes |
| A clear node and a line that passes its test | 5 of them: guided_steps.v1 | 5 of them: guided_steps.v1 | yes |
| A line that fails its test through the whole hold | 5 of them: line_fault_hold.v1 → visit_offer.v1 | 5 of them: line_fault_hold.v1 → visit_offer.v1 | yes |
| The Network Monitoring API is down | 5 of them: network_unknown.v1 | 5 of them: network_unknown.v1 | yes |
| A billing question | 5 of them: handed_to_human.v1, handed over: BILLING (priority 5) | 5 of them: handed_to_human.v1, handed over: BILLING (priority 5) | yes |
| The customer asks for a person | 5 of them: handed_to_human.v1, handed over: CUSTOMER_REQUESTED_HUMAN (priority 5) | 5 of them: handed_to_human.v1, handed over: CUSTOMER_REQUESTED_HUMAN (priority 5) | yes |

## What does differ: the credit, by monthly fee

| Situation | Segment | Monthly fee ($) | Credit ($) | Share of the fee | Approved by |
|---|---|---|---|---|---|
| An outage of five hours has ended | consumer | 999.00 | 50.00 | 5.01% | agent |
| An outage of five hours has ended | consumer | 1499.00 | 50.00 | 3.34% | agent |
| An outage of five hours has ended | sme | 4999.00 | 50.00 | 1.00% | agent |
| An outage of five hours has ended | sme | 9999.00 | 69.44 | 0.69% | agent |
| An outage of five hours has ended | sme | 14999.00 | 104.16 | 0.69% | agent |
| An outage of three days has ended | consumer | 999.00 | 199.80 | 20.00% | agent |
| An outage of three days has ended | consumer | 1499.00 | 299.80 | 20.00% | agent |
| An outage of three days has ended | sme | 4999.00 | 999.80 | 20.00% | supervisor |
| An outage of three days has ended | sme | 9999.00 | 1999.80 | 20.00% | supervisor |
| An outage of three days has ended | sme | 14999.00 | 2999.80 | 20.00% | supervisor |

## What this rests on

- A1: synthetic customers. In the stand-in data the segment decides which plans a customer can have and nothing else; a real customer base may tie the segment to things this data does not hold.
- Segment is the one attribute of this kind the platform is given. It holds no age, gender, language, religion or income, and reads the customer's name only to mask it.
- Where a customer lives decides which network node serves them, and with that whether an outage is theirs. That is the platform's job and not a proxy the evaluation can separate: here every customer is put on a node of their own and the node is given the situation.
- A few customers of each segment in each situation: this shows the absence of a rule that treats segments differently, not a statistical equality of outcomes over SkyLink's customers.
- The credit is the same share of the monthly fee for the same outage in both segments, except where the policy's minimum lifts it. Above $500.00 a supervisor approves instead of an agent: SME plans cost more, so their credits reach that level sooner. If supervisors are slower than agents, SME customers wait longer for a larger credit. That follows from the amount, is the placeholder policy's rule (A4), and is for SkyLink to confirm.
