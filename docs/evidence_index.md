# Evidence index for the supporting checklists

The brief: "Every ticked item needs evidence in the submission (screenshot, test case, config
line or log). An unticked item needs a one-line reason." This is checklists 1 to 3, line by
line. Checklist 4 (operations readiness) belongs to M5.

☑ ticked, with the evidence. ◐ ticked in part: what is shown, and what is not. ☐ not ticked,
with the reason. Everything is measured on synthetic stand-in data (assumption A1).

To see the evidence run: `make up`, then `make test-all` (tests), `make eval` (reports in
`evals/reports/`), `uv run python scripts/attack_suite_can_fail.py` (that the attack suite
can fail).

## 1. Guardrails

| | Line of the checklist | Evidence |
|---|---|---|
| ☑ | Input guardrail: prompt-injection and jailbreak checks on all free-text user input and on retrieved documents | **Input.** Every message is looked at by keyword rules beside whoever reads it; known injection phrasing is declined whatever the reading, and leaves an audit entry (`llm/offline.py` `second_look`, `orchestrator/routing.py`, `guardrail.input`). `tests/e2e/test_flagged_messages.py`; attack suite A1 to A7. **Documents.** No model reads a guide; steps are quoted. A step that speaks to an assistant, of money, asks for a code or sends the customer to a number or an outside address is refused when the index is built and again when it is about to be sent (`rag/documents.py` `not_a_step`). `tests/unit/rag/test_documents_and_embedding.py`; attack suite B1 to B4, B7, B8. The stronger evidence is that injection has nothing to reach: the model has no tool, and every attack of the suite is also played with a model that obeys the attacker |
| ☑ | Input guardrail: PII detection and masking before any call to an external model | `guardrails/pii.py`; `tests/unit/llm/test_model_payloads.py` (the details of 300 customers written into 15 messages, searched for in the request the Claude provider would send); attack suite G1. An unidentified visitor's text is not sent to a model at all: G2. What masking cannot know is said in `docs/threat_model.md` (LLM02) |
| ☑ | Output guardrail: answers grounded in retrieved sources; ungrounded answers blocked or flagged | Customers are sent templates built from evidence; troubleshooting steps are quoted from the guide of the customer's own router and checked again when sent (`agents/troubleshooting.py`). A message that claims more about the network than the evidence of that turn backs is stopped where it is stored, recorded as `message.blocked`, and a person takes over (`guardrails/claims.py`, `orchestrator/outgoing.py`). `tests/e2e/test_message_claims.py`; attack suite A4, B4, B5, F1; `evals/reports/curveball.md` |
| ☑ | Output guardrail: citations shown for every policy, SOP or regulation claim | Quoted steps end with `Source: <guide> §<section> v<version>` (`agents/replies.py` `guided_steps`; `tests/e2e/test_guided_troubleshooting.py`). A credit message names the policy version it was worked out from (`tests/e2e/test_service_credits.py`). What is said of an outage carries the time and the source of the evidence. The assistant states no regulation to customers |
| ☑ | Output guardrail: toxicity and off-topic filters; agent refuses out-of-scope requests politely | Nothing the assistant sends is generated text, so there is nothing of its own to filter. Abuse and off-topic requests are declined with one polite message that still carries the complaint reference; a fault reported rudely is handled as a fault. `tests/e2e/test_flagged_messages.py`; attack suite H1 to H3 |
| ☑ | Action guardrail: every tool has an allow-list, typed inputs and limits (amount, count, scope) enforced in code, not in the prompt | A key for each agent identity with the scopes of its tools (`mock_oss/auth.py`; `tests/mock_oss/test_auth.py`, `tests/contract/`; attack suite I2). Every call has a typed request and response (`adapters/models.py`). Limits in code: the amount is the policy's, between its minimum and the monthly fee (`domain/credit.py`; `evals/reports/credit_policy.md`); one credit per service per outage; one open visit per service (migration `0007`). The prompt holds no rule |
| ☑ | Action guardrail: high-impact actions (credit above limit …) require human approval | Every credit waits for a person (`CARE_CREDIT_REVIEW_ALL=true`, decision D3); above INR 500 for a supervisor. The approval is bound to the hash of the request that was shown, the role is checked at the API and in the repository, and a rejection must give its reason. `tests/integration/api/test_approvals_api.py`; attack suite C1 to C5 |
| ☑ | Loop guardrail: maximum steps, maximum tool calls and timeout per request | Steps of one run of the workflow: 60 (`orchestrator/graph.py` `RECURSION_LIMIT`). Asks for identity: 3. Re-checks of an unverified network: 5 (`CARE_NETWORK_RECHECK_LIMIT`). Tries of a failed turn: 5, then a person. A model call: 8 s, then the keyword rules. Gateway calls: a timeout each, a circuit that rests a failing dependency. Tool calls are not counted as such: each step makes a fixed, small number, so the step limit bounds them. `tests/e2e/test_worker_loop.py`, `test_reliability.py`, `test_resting_dependencies.py` |
| ☑ | Confidence threshold: low-confidence cases routed to a human with a summary | A reading below 0.5, a reading the rules cannot place after a model failed, and a reading that contradicts the customer's plain words all go to a person with a summary built from the recorded facts (`orchestrator/routing.py`, `handover.py`). `tests/unit/orchestrator/test_routing.py`; attack suite J3 |
| ☑ | Guardrail test set of at least 15 attack and edge prompts, with pass rate reported | `evals/reports/attack_suite.md`: 40 attacks, 58 runs, 58 passed (100%), no critical failure. As first measured 50 of 56 passed; the five gaps it found were closed (M4 slice D). That the suite can fail: `scripts/attack_suite_can_fail.py`, 19 removed controls each reported |

## 2. Security

| | Line of the checklist | Evidence, or the reason |
|---|---|---|
| ☐ | Secrets in a vault or managed secret store; none in code or logs | **No managed store in this build**: it needs the target cloud account (decision D4). What is done: secrets come from the environment only, are generated by `make init`, each process is started with the keys of its own identities only (`scripts/dev_stack.sh`); none is in the code (checked before each commit; `ruff` S rules); logs and the audit trail redact them by name and by shape (`tests/unit/test_logging_redaction.py`) |
| ◐ | Least-privilege IAM: each agent has its own identity and only the permissions its tools need | At the gateway: yes. A key for each agent, reading agents cannot write, only triage reads a profile, the executor reads consent and nothing else of the CRM (`tests/mock_oss/test_auth.py`, `tests/contract/test_adapters_against_mock.py`; attack suite I2). **Not shown**: cloud IAM (no cloud account), and the database, where every process has the same role |
| ☐ | Encryption in transit (TLS) and at rest for data stores and vector index | **Not in this build**: the stack runs on one machine without TLS. It belongs to the deployment the architecture describes |
| ☑ | Vector index access filtered by user role (no cross-tenant or cross-role retrieval) | The search filters by the reader's role, by the customer's router model and by the quotable sections, in the query (`rag/index.py`). `tests/integration/test_guide_index.py::test_role_without_access_and_unknown_model_find_nothing`; attack suite B5. One tenant |
| ◐ | API authentication on every endpoint; rate limiting on public-facing ones | Every route but the health check needs a token, and every route is tried against every role (`tests/integration/api/test_role_matrix.py`; attack suite I1, I3). New contacts for each caller and messages for each contact are limited (`tests/integration/api/test_api.py`). **Not shown**: the limits live in one API process; limits at the edge belong to the deployment |
| ☑ | Container image scanned; dependencies pinned | Pinned: `uv.lock`, with hashes. Scanned by hand with the scanner the CI file names (Trivy 0.57.1): no HIGH or CRITICAL vulnerability that has a fix; 101 in the base image without one (`docs/evidence/image_scan.md`). **The CI job itself has never run**: where the repository lives and which CI runs it is open (decision D6). Running it by hand found that it would have failed on false alarms; it is corrected |
| ☑ | Logs redact PII and secrets | `observability/logging.py`, `guardrails/pii.py` `redact_for_log`; `tests/unit/test_logging_redaction.py`, `tests/unit/guardrails/test_pii.py` |
| ☑ | Threat model done for the agent (OWASP Top 10 for LLM Applications) | `docs/threat_model.md` |

## 3. Compliance and responsible AI

| | Line of the checklist | Evidence, or the reason |
|---|---|---|
| ◐ | Data used is listed with purpose, source and retention period | `docs/data_inventory.md`. **Not shown**: the retention periods are proposals on a placeholder rule, and no job enforces them except for identity attempts (a day) and workflow checkpoints (the life of the conversation) |
| ◐ | Personal data processing follows privacy principles: purpose limitation, minimisation, consent where needed | `docs/data_inventory.md`, principle by principle. Consent for proactive notices is read from the CRM at the moment of sending (`tests/e2e/test_proactive_notifications.py`). **Not built**: export and erasure on request |
| ◐ | Domain rules identified and mapped to controls (telco: regulator complaint-handling norms) | `docs/regulatory_controls.md`. **The rules are a placeholder** (decision D1); the one real rule, a reference within two minutes, is met with seconds to spare (`loadtest/reports/`, `evals/reports/curveball.md`) |
| ☑ | Bias check: decisions tested across customer segments; no protected attributes or close proxies used | The one such attribute the platform is given, the segment, is read nowhere (`tests/unit/test_decision_inputs.py`). Consumer and SME customers in the same situations end the same way (`evals/reports/segment_parity.md`). What differs, the amount of a credit and with it who approves, follows from the monthly fee and is reported there |
| ☑ | Explainability: every recommendation shows the reason and source a human can verify | The approver sees the working of a credit: hours, rate, limits applied, policy version (`GET /v1/approvals`). The person who takes a conversation over sees why, with the evidence and its time (`orchestrator/handover.py`). The customer sees the source of quoted steps and of an estimate |
| ☑ | Full audit trail: who or what did each step, inputs, outputs, model version, prompt version, timestamp | `audit_log` (migration `0001`): actor, step, outcome, inputs, outputs, model, prompt version, policy version, duration, time. It refuses update, delete and truncate. `GET /v1/contacts/{id}/audit`; `tests/integration/test_audit_and_reference.py` |
| ☑ | Human override available and logged | A person decides every credit (`approval.decided`), closes what the workflow hands over (`handoff.done`), and can take any conversation from the assistant with a reason (`staff.takeover`). `tests/e2e/test_staff_takeover.py`, `tests/integration/api/test_approvals_api.py` |
| ☑ | Clear AI disclosure to end users | Every message begins by saying it comes from an automated assistant. `tests/unit/agents/test_replies.py::test_every_message_discloses_that_it_comes_from_an_automated_assistant` |

## What this index does not claim

- That the numbers are SkyLink's. The policy, the complaint rules and the data are stand-ins.
- That a live language model was attacked. The suite can be run against one with a setting
  (decision D2); what was run is the keyword rules and a scripted model that obeys the attacker.
- That the build is a deployment. Vault, TLS, cloud IAM, edge limits and a retention job are
  named above as what is missing.
