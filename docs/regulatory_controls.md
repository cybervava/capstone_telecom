# Domain rules and the controls that meet them

The brief's checklist 3 asks for the domain rules to be identified and mapped to controls; for
a telco, the regulator's complaint-handling norms. This is that map.

**What the rules are.** The complaint-handling rules have not been supplied (decision D1).
The rules used here are the placeholder in the knowledge pack,
`data/knowledge/regulatory/complaint_handling_rules.md`, which says of itself that it is not a
regulation and not SkyLink's procedure. One rule in it is real: a complaint reference within
two minutes for every outage contact, which the brief's curveball states. Every other number
is an assumption for Regulatory Affairs to replace. The map is built so that replacing a
number changes a setting or a policy file, not the design.

A control is cited with the place it is enforced and the test or report that shows it. "Not
met" is said where it is not.

## Complaint reference

| Rule | Control | Shown by |
|---|---|---|
| Every contact receives a complaint reference, whatever it turns out to be about | The reference is issued when the contact is accepted, before anything is read or decided (`db/contacts.py`, `ContactRepository.accept`). A message that is declined still carries it | `tests/integration/api/test_api.py::test_intake_returns_a_reference_immediately_in_the_envelope`; attack suite H1, H2 |
| Given to the customer within 2 minutes, during outages, surges and failures of other systems | The time from receipt to reference is recorded on every contact (`contacts.reference_latency_ms`). Intake writes to the platform's own database and calls nothing else | Load run at ten times the normal rate with the Network Monitoring API down: every contact accepted, slowest reference 0.25 s (`loadtest/reports/`). `evals/reports/curveball.md`: a reference for every contact in 8 of 8 playings |
| Issued by the platform's own ledger; registered with Incident Management when that system can be reached | `test_intake_needs_no_enterprise_system_or_model`. Registration is an action of the executor with an idempotency key, retried until it lands | `tests/e2e/test_reliability.py`, `tests/e2e/test_resting_dependencies.py` |
| A retry returns the same reference; a different contact gets a different one | The channel's request id and a hash of the request make intake idempotent | `test_retry_returns_the_same_reference_with_200`, `test_reused_request_id_with_a_different_request_is_a_conflict` |
| Holding a reference does not prove identity | Identity comes from the signed-in channel, the caller's line, or a customer ID with the last four digits of the registered mobile; nothing of an account is read before. A conversation stays with the customer it was verified as, and wrong answers are counted for the claimed ID across conversations | `tests/e2e/test_identity_holds.py`; attack suite E1 to E5. What the check still is: decision D9 |

## What the customer is told

| Rule | Control | Shown by |
|---|---|---|
| The customer is told they are dealing with an automated assistant | Every template begins with the disclosure (`AI_DISCLOSURE`, `agents/replies.py`) | `tests/unit/agents/test_replies.py::test_every_message_discloses_that_it_comes_from_an_automated_assistant` |
| A confirmed outage is told with its reference, start, restoration estimate and where the estimate came from | `outage_active.v1` is built from the outage record of the NOC, with its times and source | `tests/e2e/test_outage_journey.py`; `evals/reports/outage_detection.md` |
| When the network status cannot be verified the customer is told exactly that; never that the network works without a fresh check, and then as "no outage has been reported" | Each template declares what it claims about the network, and a message is stored only if the evidence of that turn backs the claim (`guardrails/claims.py`, `orchestrator/outgoing.py`). A message that claims more is stopped and a person takes over | `tests/e2e/test_message_claims.py`; `evals/reports/curveball.md` (no all-clear in any playing); attack suite A4, F1 |
| A restoration time is never invented | The estimate is the NOC's or absent | `tests/unit/agents/test_notifications.py::test_started_message_does_not_invent_an_eta` |
| A customer who asks for a person is passed to one with a summary | Asked for in any message, it ends the automated handling at once; the summary is built from the recorded facts | `tests/unit/orchestrator/test_routing.py`; `orchestrator/handover.py` |

## Outage complaints and the shared incident

| Rule | Control | Shown by |
|---|---|---|
| All complaints caused by one outage on one node are attached to one incident; no second incident while the first is open | One incident per node and episode, opened by an action whose key is the node and the episode | `evals/reports/curveball.md` (no node with a second incident); `tests/e2e/test_edges_and_degraded_mode.py` |
| Each complaint keeps its own reference inside the incident | The complaint is registered once and linked to the incident | The same tests count complaints, incidents and links |
| No technician visit while an outage is confirmed or suspected on the node | A visit is proposed only on a node confirmed clear with a line that failed its test through the whole hold, and the executor looks at the network again before it books | `evals/reports/truck_rolls.md`; `tests/e2e/test_visits.py`; attack suite A5, D1 to D3 |

## Resolution and closure

| Rule | Control | Shown by |
|---|---|---|
| A complaint is resolved when service is restored and that is confirmed, not when it was acknowledged | "Restored" is said only on the NOC's resolution with a live status that agrees. The customer is asked whether it works. The complaint closes on their yes, or after a quiet period, and not on a yes read into words that say no | `tests/e2e/test_feed_and_restoration.py`; attack suite J2 |
| Target for confirming closure after restoration: 4 hours (placeholder) | **Not met as a measured target.** The quiet period before a close is a setting; nothing reports how long closures took | M5 (online metrics) |
| A repeat complaint within 7 days about the same service is flagged for priority review | A customer's second contact within seven days raises the priority of whatever reaches the staff queue (`orchestrator/node_support.py`, `_priority`) | The count: `tests/integration/test_intake.py::test_repeat_contacts_by_a_customer_are_counted`. The rule is by customer, not yet by service |

## Records

| Rule | Control | Shown by |
|---|---|---|
| For every complaint: when received, when the reference was issued, each step and by whom or what, the evidence used, the outcome | `contacts` holds the times; `audit_log` holds one entry for every step with actor, inputs, outputs, model, prompt and policy version; `actions` holds every side effect with its outcome | `GET /v1/contacts/{id}/audit`; `tests/integration/test_audit_and_reference.py` |
| Records are kept for 24 months (placeholder), then deleted or anonymised | **Not met.** The period is stated in `docs/data_inventory.md`; no job enforces it | Listed as a gap |
| Records cannot be altered after the fact | The audit log refuses update, delete and truncate in the database itself | `tests/integration/test_audit_and_reference.py::test_audit_log_cannot_be_rewritten` |

## Money

The service-credit policy is also a placeholder (`data/policies/credit_policy.v0.yaml`,
assumption A4). The controls do not depend on its numbers.

| Rule | Control | Shown by |
|---|---|---|
| A credit follows the policy, to the paisa | One engine computes eligibility, amount and approval level from the policy file | `evals/reports/credit_policy.md`: 4,050 of 4,050 cases |
| A credit above the limit needs a supervisor; while the mentors have not said otherwise, every credit needs a person | The approval is bound to the hash of the request that was shown; the role is checked at the API and again in the repository; a rejection must give its reason | `tests/integration/api/test_approvals_api.py`; attack suite C1 to C5 |
| A credit is applied once | One credit per service per outage, written with an idempotency key | `tests/e2e/test_credit_lifecycle.py` |

## Personal data

See `docs/data_inventory.md`, which goes through the privacy principles one by one.

## Responsible AI lines of the checklist

| Line | Control | Shown by |
|---|---|---|
| Bias check across segments; no protected attribute or close proxy | The one such attribute the platform is given, the segment, is read nowhere | `tests/unit/test_decision_inputs.py`; `evals/reports/segment_parity.md` |
| Every recommendation shows the reason and a source a person can verify | A credit request shows its working (hours, rate, clamps, policy version). Steps are quoted with guide, section and version. What is said about the network carries the time of the evidence | The approval view; `Source:` in `guided_steps.v1`; `outage_active.v1` |
| Full audit trail | As under Records | |
| Human override available and logged | A person decides every credit, closes what the workflow hands over, and can take a conversation over from the assistant with a reason; each is in the audit trail with who | `tests/e2e/test_staff_takeover.py`; `approval.decided`, `handoff.done`, `staff.takeover` in `audit_log` |
| Clear AI disclosure | As above | |
