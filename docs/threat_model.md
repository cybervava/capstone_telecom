# Threat model

What could go wrong with an assistant that speaks to customers, reads their records and can
set money and people in motion; what stands in the way; where that is shown; and what is
left. It follows the OWASP Top 10 for LLM Applications (2025 list,
<https://genai.owasp.org/llm-top-10/>), as the brief's security checklist asks, and then goes
through what that list does not cover.

Every control is cited with the place it is enforced and the test, evaluation or attack that
shows it. Attack keys (A1, C3, …) are those of `evals/reports/attack_suite.md`.

## What is being protected

| Asset | Why it matters |
|---|---|
| A customer's record (name, number, address, services) | Personal data; another customer must never see it |
| Money: service credits | A credit is SkyLink's money, owed by a policy and approved by a person |
| A technician's time | A visit that the network explains is the waste the brief wants cut |
| What customers are told about the network | A false all-clear during an outage is the failure the curveball names |
| The complaint record | The regulator's rule: a reference for every contact, and a trail nobody can quietly rewrite |
| Staff attention | A queue that can be flooded hides the case that matters |
| Keys and secrets | One key for each agent identity; a signing secret for the API |

## Who might attack, and from where

| Actor | What they have |
|---|---|
| A visitor who is not signed in | A session, free text, as many conversations as the channel allows |
| A signed-in customer | The same, and their own account |
| Someone who knows another customer's ID | The ID is on every bill; four digits of a phone number are not a secret |
| Someone who can edit a guide, or a row of the guide index | Words that are quoted to customers verbatim |
| A language model that is wrong, or has been talked round | Nine fields of a fixed shape, one of them a sentence |
| Someone holding one agent's gateway key | That agent's scopes at the gateway |
| A member of staff acting outside their role | An agent's or an operator's token |
| A dependency that fails | Not an attacker; the same care is owed (curveball, `evals/reports/curveball.md`) |

Out of scope, and said so: SkyLink's systems behind the gateway (stand-ins here); the channel
that signs customers in and issues tokens; the network, hosts and cloud account of the target
deployment; someone with write access to the platform's database. Every process connects as the
one role that owns the schema, so such a person can do everything, including switching off the
trigger that keeps the audit log from being rewritten.

## The design that most of this rests on

1. **The model describes; code decides.** The model is shown the customer's new messages, masked, and returns
   nine fields. It is given no tool. Identity, network status, eligibility, amounts and
   bookings are decided by code from evidence and a policy file.
2. **Customers are sent templates.** No sentence a model wrote is ever sent to a customer.
   The one model-written sentence is a summary for staff, cleaned and cut.
3. **Every side effect is a proposal first.** Reading agents hold read-only keys. One
   executor writes, with an idempotency key, after re-checking the evidence at that moment,
   and for a credit (as configured by default) only after a person approved the very request that was shown.
4. **A second lock where the first could fail.** A message is checked for what it claims
   when it is stored, whatever the routing decided. Quoted steps are checked when the guide
   is indexed and again when they are sent. A contact is bound to its customer by the agent
   and in the database. Who may approve is asked at the API and in the repository.

## OWASP Top 10 for LLM Applications

### LLM01 Prompt injection

*Here:* a customer writes instructions into a message; a guide carries instructions for "the
assistant".

| Control | Where | Shown by |
|---|---|---|
| The model has no tool and its answer cannot name an action: nine fields, extra fields refused | `llm/base.py` (`extra="forbid"`, `TRIAGE_SCHEMA`), `llm/claude.py`, `llm/openai_provider.py` | J1 |
| The message is wrapped as data and cannot close its wrapper | `llm/prompts.py` `customer_message`, used by both providers | `tests/unit/llm/test_claude_provider.py`, `tests/unit/llm/test_openai_provider.py` |
| Whatever the model reads, known injection phrasing with nothing of a fault in it is declined by the keyword rules beside it; with a fault reported, the message goes its usual way and can unlock nothing | `llm/offline.py` `second_look`, `orchestrator/routing.py` | A1, A3, A4; `tests/unit/orchestrator/test_routing.py` |
| A model that obeys the attacker still cannot move money, people or the truth about the network | The design above | Every attack of family A, C5, D1, D3, F1, tried with a scripted obedient model |
| No model reads a guide. Steps are quoted, and a step that speaks to an assistant, of money, asks for a code, or sends the customer to a number or an outside address is refused at the index build and again when it is about to be sent | `rag/documents.py` `not_a_step`, `not_a_title`, `agents/troubleshooting.py` | B1 to B4, B7, B8 |
| A flagged message leaves an audit entry of its own | `agents/triage.py` (`guardrail.input`) | `tests/e2e/test_flagged_messages.py` |

*What is left.* Phrasing the rules do not know gets past them; what it then meets is a model
with no tool. A model that has been talked round can decline a real request or send it to a
person (J3 shows the second: a plain fault report that a model calls off-topic goes to a
person, not to a refusal). One run against a live model has been made: OpenAI's
`gpt-5.4-mini` read the messages of the 18 attacks made of words, and all 58 runs passed
(`evals/reports/attack_suite_openai.md`). One run of each of 18 attacks says little about how
often a real model is moved; the obedient model remains the harder test. Claude has not been
run (no key). The demo uses that OpenAI model (decision D2), pinned to the dated snapshot that
served the run, so that a model cannot change under the demo without a change to the settings.

### LLM02 Sensitive information disclosure

*Here:* a customer's data reaching a model, a log, or another customer.

| Control | Where | Shown by |
|---|---|---|
| Before a model call: contact details masked by shape, and by value the personal details the CRM holds of this customer (the name and its parts, the registered mobile and its last digits, the name part of the email address, the numbers of the ids, the area, city and pincode of each service address) | `guardrails/pii.py`, `agents/triage.py` `personal_values` | G1; `tests/unit/llm/test_model_payloads.py` (300 customers, 15 messages) |
| An unidentified visitor's text is never sent to a model: there is no record to mask it by | `llm/gateway.py` (`allow_external`) | G2 |
| Logs and the audit trail redact contact details and secrets; the HTTP client's request lines are off | `observability/logging.py`, `guardrails/pii.py` `redact_for_log` | `tests/unit/test_logging_redaction.py` |
| A contact is read and written only by its owner or staff; someone else's looks like none | `api/app.py` `_owned_contact` | E5; `tests/integration/api/test_api.py` |
| A conversation stays with the customer it was verified as; another customer's ID typed into it is not looked at | `agents/triage.py`, `db/contacts.py` (written once) | E1, E2 |
| Wrong answers to the identity check are counted for the claimed ID across conversations; the answer is the same for an ID that exists and one that does not | `db/identity.py`, `agents/identity_guard.py` | E3, E4; `tests/e2e/test_identity_holds.py` |

*What is left.* Masking cannot know digits spelled as words, a name in another script, or a
street the platform was never told. The identity check of a customer who is not signed in is
two things other people can know; guessing spread thinly over many customers succeeds about
once in ten thousand tries and is told to staff only from fifty customers an hour (decision
D9).

### LLM03 Supply chain

| Control | Where | Shown by |
|---|---|---|
| Dependencies pinned with hashes | `uv.lock` (78 packages with hashes, and the project itself) | The file |
| The model is reached through the vendor's own SDK (Anthropic's or OpenAI's), behind one gateway; the audit trail records the model and the prompt version of every reading | `llm/gateway.py` | `audit_log.model`, `prompt_version` |
| Traces of the workflow go to LangSmith only when switched on, and then with inputs and outputs hidden unless a setting says otherwise on purpose: the run tree, timings, errors and metadata (the contact's id, the step, the git revision) leave, no state or message | `observability/tracing.py`, called by `build_graph`; `LANGSMITH_*` in `.env.example` and `docker-compose.yml`; only the orchestrator is given them (`scripts/dev_stack.sh`) | `tests/unit/test_tracing.py`; checked by hand on the traces of the live attack-suite run, 16 Nov |
| The guide index is built by a local hashing embedder: no model download, no embedding service | `rag/embedding.py` | `evals/reports/retrieval.md` |
| Guides are typed documents with access roles; untrusted material is kept in a folder the index build never opens, and the parser refuses it if it is copied in | `pipelines/guide_index.py`, `rag/documents.py` | B1, B2 |

*What is left.* CI (GitHub Actions, D6) scans the image on every push and fails on anything
HIGH or CRITICAL that has a fix; there is none, and 101 findings in the base image have no fix
(`docs/evidence/image_scan.md`). The published image is not signed; no software bill of
materials; no signature on the knowledge pack.

### LLM04 Data and model poisoning

No model is trained or tuned here. What can be poisoned is the knowledge the assistant
quotes.

| Control | Where | Shown by |
|---|---|---|
| The index build refuses a guide whose quotable steps are unsafe or out of place, and a failed build leaves the previous index in place | `rag/documents.py`, `rag/index.py` (`rebuild` in one transaction) | B2, B3 |
| The passage about to be quoted is checked again: the customer's own router, a section a customer may follow, steps that pass the same test | `agents/troubleshooting.py` `_not_quotable` | B4, B5 |
| Only sections a customer can safely follow are ever quoted; a factory reset is not among them | `rag/documents.py` `SELF_SERVICE_SECTIONS` | B6 |

*What is left.* A step that is wrong but harmless-looking ("wait 30 minutes" for "30
seconds") passes every check: the guide is the control, and whoever can change it is
trusted for its wording. The stored index carries no signature.

### LLM05 Improper output handling

| Control | Where | Shown by |
|---|---|---|
| The model's answer is parsed against a strict schema or thrown away; the keyword rules then read the message and the trail says so | `llm/claude.py`, `llm/openai_provider.py`, `llm/gateway.py` | J1; the providers' unit tests |
| The one free-text field is cleaned before it is kept in the conversation's state and shown to staff: contact details masked, markup and control characters removed, cut to 240 characters. Its copy in the audit entry has contact details masked and is otherwise as the model wrote it | `orchestrator/handover.py` `clean_summary` | A7 |
| The staff page writes all text from the server as text, never as markup | `web/staff.js` (`textContent`) | The file |
| No model output is used in a query, a path, a URL or a command | By design | Reading `agents/`, `orchestrator/` |

### LLM06 Excessive agency

| Control | Where | Shown by |
|---|---|---|
| A key for each agent identity, with the scopes of its own tools; reading agents cannot write; the executor reads a customer's consent and nothing else of the CRM | `mock_oss/auth.py`, `adapters/gateway.py` | I2; `tests/contract/` |
| Every write is typed, keyed and limited in code: one credit per service per outage, of the policy's amount; one open visit per service; a notice only with consent | `executor/`, `domain/credit.py`, migration `0007_one_open_visit.sql` | `evals/reports/credit_policy.md`; `tests/e2e/test_credit_lifecycle.py`, `test_visits.py` |
| A credit needs a person's approval of the request that was shown, by its hash; above INR 500 a supervisor's. Below that, an agent's by default (`CARE_CREDIT_REVIEW_ALL=true`; with it off, small credits need nobody: decision D3) | `db/approvals.py`, `api/approvals.py` | C1 to C4 |
| The executor looks at the network again before it books a visit | `executor/visits.py` | D2 |
| Loops are bounded: steps for each run of the workflow, asks for identity, re-checks of the network, retries of a turn and of a write, seconds for a model call | `orchestrator/routing.py` (`MAX_STEPS_PER_RUN`, 12 decisions a run, then a person; LangGraph's ceiling of 60 behind it in `graph.py`), `worker.py`, `llm/gateway.py` | `tests/unit/orchestrator/test_routing.py`, `test_routing_events.py`, `tests/unit/llm/test_gateway.py`, `tests/e2e/test_reliability.py` |
| A person can take any conversation from the assistant, with a reason, on record | `api/takeover.py` | `tests/e2e/test_staff_takeover.py` |

### LLM07 System prompt leakage

The prompt holds no secret and no rule that matters: every rule is in code, and the prompt
is a file in the repository. It cannot be echoed to a customer, because customers are sent
templates (A3). Its text is versioned by hash and the version is written with every call.

### LLM08 Vector and embedding weaknesses

| Control | Where | Shown by |
|---|---|---|
| Retrieval is filtered in the query by the customer's router model (from the service record, never from what they write), by the reader's role, and by the list of quotable sections | `rag/index.py` `_SEARCH` | B5; `tests/e2e/test_guided_troubleshooting.py` |
| One tenant: SkyLink's own guides. The index holds no customer data | `docs/data_inventory.md` | |
| The index lives in the platform's own PostgreSQL; nothing is sent to an embedding service | `rag/embedding.py` | |

*What is left.* Retrieval quality: the right guide, but not always the best section
(`evals/reports/retrieval.md`). A wrong section is still a safe one.

### LLM09 Misinformation

| Control | Where | Shown by |
|---|---|---|
| Each conversation template declares what it claims about the network, and is stored only when the evidence of that turn backs the claim; a message that claims more is stopped and a person takes over. (The proactive outage notices are built from a recent NOC event and are not checked again when sent.) | `guardrails/claims.py`, `orchestrator/outgoing.py` | A4, F1; `tests/e2e/test_message_claims.py`; `evals/reports/curveball.md` |
| Amounts come from the policy engine and show their working; steps come from the guide with its source; a restoration time is the NOC's or absent | `domain/credit.py`, `agents/replies.py` | `evals/reports/credit_policy.md` |
| A complaint is not closed on a reading of "it works" when the customer's words say otherwise | `orchestrator/routing.py` | J2 |

*What is left.* The NOC's own data being wrong. For about a second after the NOC resolves an
outage, a status read can still show the alarm (noted in the plan's known gaps).

### LLM10 Unbounded consumption

| Control | Where | Shown by |
|---|---|---|
| At most one model reading for each turn of a conversation (it reads all the customer's messages since the last), with a timeout, a token limit and a fallback to the keyword rules; a failed turn is tried again, and the SDK may retry a call once | `llm/gateway.py`, `llm/claude.py`, `llm/openai_provider.py` | `tests/unit/llm/test_gateway.py`, `tests/unit/llm/test_build_provider.py` |
| When the platform is behind, the model is not called at all | `surge.py` | `tests/e2e/test_surge_mode.py`; `loadtest/reports/` |
| Limits on new contacts for each caller and on messages for each contact; a caller's first contact is never limited, because it must get its reference. A channel integration as a whole is capped (600 at once, then 60 a second), first contacts included | `api/ratelimit.py`, `api/app.py` | `tests/integration/api/test_api.py` |
| The operations queue cannot be written full by one caller: one item for each kind of trouble and subject, a cap on items about locked customers. (The staff queue takes one item per contact and reason; a caller can open five new contacts a minute.) | `db/ops.py`, `agents/identity_guard.py` | `tests/integration/test_identity_attempts.py` |

*What is left.* The limits live in one API process and count sessions; limits at the edge
and a budget alarm for model spend belong to the deployment and to M5.

## Beyond the LLM list

| Risk | Control | Shown by | What is left |
|---|---|---|---|
| Broken access control | A token on every route but the health check, the static pages and, in `dev` only, the token endpoint (which there signs anyone in as any customer); roles enforced in code; every route against every role as one table; object-level ownership | `tests/integration/api/test_role_matrix.py`; I1, I3, E5 | Staff sign-in is a stand-in (a shared key and a typed name) |
| Forged or altered tokens | One algorithm, issuer, audience and expiry required; a short signing secret refused at start-up | I1 | Tokens are issued by SkyLink's identity provider in the target deployment |
| Guessing | The identity lock; equal answers | E3, E4 | Decision D9 |
| Replay and double writes | Idempotency keys on intake and on every write; an approval decided once | C4; `tests/e2e/test_reliability.py` | |
| Secrets | From the environment only; generated by `make init`; redacted from logs and trail by name and by shape | `tests/unit/test_logging_redaction.py` | No managed secret store; no rotation |
| Tampering with the record | The audit log refuses update, delete and truncate, by trigger | `tests/integration/test_audit_and_reference.py` | One database role for every process, the one that owns the schema: a process that is taken over can write to any table and can switch the trigger off. Separate roles, with only the migrations allowed to alter the audit log, are not built. Other records (contacts, messages, actions) are updated in place |
| Transport and storage | Not in this build | | TLS, encryption at rest and network policy are the deployment's |
| Denial of the unsigned-in path to one customer | Whoever knows a customer ID can keep its identity check locked | Stated in `docs/EXECUTION_PLAN.md` | The customer keeps the signed-in path, the caller's line and a person |

## What was not tried

- Claude against the attack suite (it can be run with one setting; no key was available), and
  any live model more than once: the one live run, with OpenAI's `gpt-5.4-mini`, read each of
  the 18 attacks made of words once.
- Attacks that take many turns and a patient attacker working on a member of staff. The
  staff see summaries built from recorded facts, and no approval can be raised by a note
  (A7), but people can be talked into things no control sees.
- Timing: whether a refused identity answer returns measurably sooner than a checked one.
- Speech: instructions hidden in a call transcript by how they sound. Transcripts arrive as
  text and are read like any other text.
- Load as an attack at the edge, and anything at the level of network or host.
- The stand-in enterprise systems themselves. They are not SkyLink's.
