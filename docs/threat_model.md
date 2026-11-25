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
| A language model that is wrong, or has been talked round | Nine fields of a fixed shape, one of them a sentence; and, when it is asked about a router guide, the id of a section and two passages it points at |
| Somebody else's assistant, with the MCP key | Four tools that read: a node's status, a guide's steps, a credit quotation, the platform's health (`mcp_server/`). One gateway key that reads a node's status |
| Someone who can reach the MCP port without its key | Its health check, and a refusal |
| Someone holding one agent's gateway key | That agent's scopes at the gateway |
| A member of staff acting outside their role | An agent's or an operator's token |
| A dependency that fails | Not an attacker; the same care is owed (curveball, `evals/reports/curveball.md`) |
| Anyone on the internet, at the demo site on AWS | Its address. Everything but the health check is behind one shared sign-in (`deploy/aws/Caddyfile`); the host has ports 80 and 443 open and nothing else |
| Someone with the shared sign-in to the demo site | The desks, as on a laptop, and the demo console's page under `/sim`. The page holds no key: driving the simulator through it needs the simulator's admin key as well, and the rest of the simulator's APIs are not reachable from outside (`deploy/aws/Caddyfile`). The console reaches the stand-in systems only, never the platform |
| Whoever can push to `dev` of the GitLab.com project | A release on the demo host: the pipeline's role may push the image, upload the bundle and run one SSM document whose only input is a commit, and no other command (`deploy/aws/terraform/iam.tf`) |

Out of scope, and said so: SkyLink's systems behind the gateway (stand-ins here); the channel
that signs customers in and issues tokens; the network and cloud account of a production
deployment (the demo deployment on AWS is one host, described in the README); someone with
write access to the platform's database. Every process connects as the
one role that owns the schema, so such a person can do everything, including switching off the
trigger that keeps the audit log from being rewritten.

## The design that most of this rests on

1. **The model describes; code decides.** The model is shown the customer's new messages, masked, and returns
   nine fields. When the guide sections for a customer's router have been found, it is asked
   once which of them fits, and returns three more. It is given no tool. Identity, network
   status, eligibility, amounts and bookings are decided by code from evidence and a policy
   file.
2. **Customers are sent templates.** No sentence a model wrote is ever sent to a customer.
   The one model-written sentence is a summary for staff, cleaned and cut. In a guide answer
   a model points at a passage of the customer's message and one of the guide; what is put
   into the template is the source's own clause or sentence, found there and checked
   (`guardrails/grounding.py`), or nothing.
3. **Every side effect is a proposal first.** Reading agents hold read-only keys. One
   executor writes, with an idempotency key, after re-checking the evidence at that moment,
   and for a credit (as configured by default) only after a person approved the very request that was shown.
4. **A second lock where the first could fail.** A message is checked for what it claims
   when it is stored, whatever the routing decided. Quoted steps are checked when the guide
   is indexed and again when they are sent. A contact is bound to its customer by the agent
   and in the database. Who may approve is asked at the API and in the repository.
5. **Other people's assistants can ask, and cannot act.** The MCP server offers tools that
   read, on a key that reads one thing. The model in the customer's path is not given them.

## OWASP Top 10 for LLM Applications

### LLM01 Prompt injection

*Here:* a customer writes instructions into a message; a guide carries instructions for "the
assistant"; either is read by the model that is asked which guide section fits.

| Control | Where | Shown by |
|---|---|---|
| The model has no tool and its answer cannot name an action: nine fields, extra fields refused | `llm/base.py` (`extra="forbid"`, `TRIAGE_SCHEMA`), `llm/claude.py`, `llm/openai_provider.py` | J1 |
| The message is wrapped as data and cannot close its wrapper | `llm/prompts.py` `customer_message`, used by both providers | `tests/unit/llm/test_claude_provider.py`, `tests/unit/llm/test_openai_provider.py` |
| Whatever the model reads, known injection phrasing with nothing of a fault in it is declined by the keyword rules beside it; with a fault reported, the message goes its usual way and can unlock nothing | `llm/offline.py` `second_look`, `orchestrator/routing.py` | A1, A3, A4; `tests/unit/orchestrator/test_routing.py` |
| A model that obeys the attacker still cannot move money, people or the truth about the network | The design above | Every attack of family A, C5, D1, D3, F1, tried with a scripted obedient model |
| Steps are quoted, never written, and a step that speaks to an assistant, of money, asks for a code, or sends the customer to a number or an outside address is refused at the index build and again when it is about to be sent | `rag/documents.py` `not_a_step`, `not_a_title`, `agents/troubleshooting.py` | B1 to B4, B7, B8 |
| A model reads the sections found for a customer, and its answer can do one thing: name which of them is quoted. A section it was not shown is not taken from it. The customer's words and the sections are wrapped as data and neither can close its wrapper; the answer is three strings, extra fields refused | `llm/prompts.py` `guide_question`, `llm/base.py` (`GuideAnswer`), `agents/troubleshooting.py` | K3; `tests/unit/llm/test_guide_answers.py`, `tests/unit/agents/test_troubleshooting.py` |
| A passage the model points at is used only as the whole clause or sentence of its source, found there word for word; a sentence of the model's own is not in a source and is left out, with a flag in the trail | `guardrails/grounding.py` | K1; `tests/unit/guardrails/test_grounding.py` |
| A flagged message leaves an audit entry of its own | `agents/triage.py` (`guardrail.input`) | `tests/e2e/test_flagged_messages.py` |

*What is left.* Phrasing the rules do not know gets past them; what it then meets is a model
with no tool. A model that has been talked round can decline a real request or send it to a
person (J3 shows the second: a plain fault report that a model calls off-topic goes to a
person, not to a refusal). A customer who writes a sentence in order to read it in the
assistant's reply gets it back at most as a quotation of their own words, and not at all if it
carries a quotation mark, speaks of money, of a promise or of the network, or names something
unsafe (K2); what passes those lists is still a customer's sentence, shown as theirs to the
customer who wrote it. Runs against a live model have been made: OpenAI's `gpt-5.4-mini`
read the messages of the 18 attacks made of words and answered the questions about a guide,
most recently on 25 Nov, and all 61 runs passed (`evals/reports/attack_suite_openai.md`).
One run of each attack says little about how
often a real model is moved; the obedient model remains the harder test. Claude has not been
run (no key). The demo uses that OpenAI model (decision D2), pinned to the dated snapshot that
served the run, so that a model cannot change under the demo without a change to the settings.

### LLM02 Sensitive information disclosure

*Here:* a customer's data reaching a model, a log, or another customer.

| Control | Where | Shown by |
|---|---|---|
| Before a model call: contact details masked by shape, and by value the personal details the CRM holds of this customer (the name and its parts, the registered mobile and its last digits, the name part of the email address, the numbers of the ids, the area, city and pincode of each service address) | `guardrails/pii.py`, `agents/triage.py` `personal_values` | G1; `tests/unit/llm/test_model_payloads.py` (300 customers, 15 messages) |
| An unidentified visitor's text is never sent to a model: there is no record to mask it by | `llm/gateway.py` (`allow_external`) | G2 |
| The question about a guide carries the customer's words masked the same way. What is to be masked by value is read from the CRM by the triage agent at that moment; when it cannot be read, the model is not asked. A passage with a masking token in it is not said back | `llm/gateway.py` `answer_from_guide`, `agents/triage.py` `personal_values_of`, `guardrails/grounding.py` | G1 (its recorder is also shown the guide question); `tests/unit/llm/test_model_payloads.py`, both questions |
| The MCP tools take and return nothing of a customer: a node, a router model, figures. The words of a problem description are masked by shape before the search and are not kept in the trail, only their length | `mcp_server/tools.py` | `tests/integration/test_mcp_server.py` |
| Logs and the audit trail redact contact details and secrets; the HTTP client's request lines are off | `observability/logging.py`, `guardrails/pii.py` `redact_for_log` | `tests/unit/test_logging_redaction.py` |
| A contact is read and written only by its owner or staff; someone else's looks like none | `api/app.py` `_owned_contact` | E5; `tests/integration/api/test_api.py` |
| A conversation stays with the customer it was verified as; another customer's ID typed into it is not looked at | `agents/triage.py`, `db/contacts.py` (written once) | E1, E2 |
| Wrong answers to the identity check are counted for the claimed ID across conversations; the answer is the same for an ID that exists and one that does not | `db/identity.py`, `agents/identity_guard.py` | E3, E4; `tests/e2e/test_identity_holds.py` |

*What is left.* Masking cannot know digits spelled as words, a name in another script, or a
street the platform was never told. The guide question carries up to the last 2,000
characters of what the customer wrote, where a message carries one message: more room for
what masking cannot know. An MCP client can type anything into a problem description; it goes
to no model from here, and its provider is the client's own. The identity check of a customer who is not signed in is
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
(`docs/evidence/image_scan.md`). The GitLab.com pipeline runs the same scan before a release,
ECR scans the image again when it is pushed, and the host starts a release only by its commit
tag, which never moves. The image is not signed; no software bill of materials; no signature
on the knowledge pack.

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
| A guide answer is parsed against its own strict schema or thrown away; the search's choice then stands and the trail says so. Neither the customer's words nor the model's are copied into the trail: outcomes and citations are | `llm/base.py` `parse_answer`, `agents/troubleshooting.py` | `tests/unit/llm/test_guide_answers.py`, `tests/unit/agents/test_troubleshooting.py` |
| What is quoted into a message is put between quotation marks, so it may carry none: a customer's clause must be made of plain characters (no quotation mark, no markup, no colon, no letter of another alphabet), and a guide's sentence no quotation mark and no character that is not what it looks like | `guardrails/grounding.py` | K2; `tests/unit/guardrails/test_grounding.py`; `tests/e2e/test_guided_troubleshooting.py` |
| The pages write all text from the server as text, never as markup, including what an audit entry recorded as having gone in and come out | `web/shared.js`, `web/staff.js`, `web/app.js` (`textContent`) | The files |
| No model output is used in a query, a path, a URL or a command | By design | Reading `agents/`, `orchestrator/` |

### LLM06 Excessive agency

| Control | Where | Shown by |
|---|---|---|
| A key for each agent identity, with the scopes of its own tools; reading agents cannot write; the executor reads a customer's consent and nothing else of the CRM | `mock_oss/auth.py`, `adapters/gateway.py` | I2; `tests/contract/` |
| Every write is typed, keyed and limited in code: one credit per service per outage, of the policy's amount; one open visit per service; a notice only with consent | `executor/`, `domain/credit.py`, migration `0007_one_open_visit.sql` | `evals/reports/credit_policy.md`; `tests/e2e/test_credit_lifecycle.py`, `test_visits.py` |
| A credit above $500 needs a supervisor's approval of the request that was shown, by its hash. At or under that, the stack applies it without review (`CARE_CREDIT_REVIEW_ALL=false`, decision D3 of 23 Nov); with the setting on, as the attack suite runs, an agent's approval is needed | `db/approvals.py`, `api/approvals.py` | C1 to C4 |
| The executor looks at the network again before it books a visit | `executor/visits.py` | D2 |
| Loops are bounded: steps for each run of the workflow, asks for identity, re-checks of the network, retries of a turn and of a write, seconds for a model call | `orchestrator/routing.py` (`MAX_STEPS_PER_RUN`, 12 decisions a run, then a person; LangGraph's ceiling of 60 behind it in `graph.py`), `worker.py`, `llm/gateway.py` | `tests/unit/orchestrator/test_routing.py`, `test_routing_events.py`, `tests/unit/llm/test_gateway.py`, `tests/e2e/test_reliability.py` |
| A person can take any conversation from the assistant, with a reason, on record | `api/takeover.py` | `tests/e2e/test_staff_takeover.py` |
| An assistant outside the platform is offered tools that read and none that act. The MCP server's gateway key has the scope `network:read`: the gateway refuses it a line test, a customer, a write. Every argument is typed and bounded in the tool's declaration, and one out of bounds is refused before the tool runs | `mcp_server/server.py`, `mock_oss/auth.py`, `adapters/gateway.py` `network_for_mcp` | `tests/integration/test_mcp_server.py`; `tests/contract/test_adapters_against_mock.py`; `tests/mock_oss/test_auth.py` |
| The model in the customer's path is not given those tools, or any | `runtime.py`; ADR 0003 | Reading `orchestrator/`, `agents/` |

### LLM07 System prompt leakage

The prompt holds no secret and no rule that matters: every rule is in code, and the prompt
is a file in the repository. It cannot be echoed to a customer, because customers are sent
templates (A3). Its text is versioned by hash and the version is written with every call.

### LLM08 Vector and embedding weaknesses

| Control | Where | Shown by |
|---|---|---|
| Retrieval is filtered in the query by the customer's router model (from the service record, never from what they write), by the reader's role, and by the list of quotable sections | `rag/index.py` `_SEARCH` | B5; `tests/e2e/test_guided_troubleshooting.py` |
| What the search hands on to a model, and to an MCP client, is only what could be quoted: the customer's own router's guide, the sections a customer may follow, each passage checked. A first passage that may not be quoted ends the answer, and a person takes over | `agents/troubleshooting.py` `retrieve`, `_not_quotable` | B4, B8; `tests/unit/agents/test_troubleshooting.py` |
| One tenant: SkyLink's own guides. The index holds no customer data | `docs/data_inventory.md` | |
| The index lives in the platform's own PostgreSQL; nothing is sent to an embedding service | `rag/embedding.py` | |

*What is left.* Retrieval quality: the right guide, but not always the best section
(`evals/reports/retrieval.md`; with a model choosing among what was found,
`evals/reports/retrieval_openai.md`). A wrong section is still a safe one.

### LLM09 Misinformation

| Control | Where | Shown by |
|---|---|---|
| Each conversation template declares what it claims about the network, and is stored only when the evidence of that turn backs the claim; a message that claims more is stopped and a person takes over. (The proactive outage notices are built from a recent NOC event and are not checked again when sent.) | `guardrails/claims.py`, `orchestrator/outgoing.py` | A4, F1; `tests/e2e/test_message_claims.py`; `evals/reports/curveball.md` |
| Amounts come from the policy engine and show their working; steps come from the guide with its source; a restoration time is the NOC's or absent | `domain/credit.py`, `agents/replies.py` | `evals/reports/credit_policy.md` |
| A complaint is not closed on a reading of "it works" when the customer's words say otherwise | `orchestrator/routing.py` | J2 |
| A quotation says nothing about the network: a passage that reads as an all-clear or as an outage is not quoted, and a customer's clause that names an outage, a technician, a promise or SkyLink is not said back. A model points at a whole clause or sentence and cannot cut "working fine" out of "not working fine" | `guardrails/grounding.py`, `guardrails/claims.py` `claims_in_words` | K1, K2; `tests/unit/guardrails/test_grounding.py` |
| An MCP client is told what an answer may be used for with the answer: `UNKNOWN` comes with the sentence that it is not an all-clear, a quotation with the sentence that nothing is applied | `mcp_server/tools.py` | `tests/integration/test_mcp_server.py` |

*What is left.* What an MCP client's own model then says to a person is outside the platform:
the server can say what an answer means, and cannot make an assistant repeat it. The NOC's own
data being wrong. For about a second after the NOC resolves an
outage, a status read can still show the alarm (noted in the plan's known gaps).

### LLM10 Unbounded consumption

| Control | Where | Shown by |
|---|---|---|
| At most one model reading for each turn of a conversation (it reads all the customer's messages since the last), with a timeout, a token limit and a fallback to the keyword rules; a failed turn is tried again, and the SDK may retry a call once | `llm/gateway.py`, `llm/claude.py`, `llm/openai_provider.py` | `tests/unit/llm/test_gateway.py`, `tests/unit/llm/test_build_provider.py` |
| The question about a guide is asked at most once in a conversation (steps are given once), with the same timeout and token limit; a step that runs again reads back what was sent and does not ask again | `orchestrator/network_nodes.py`, `llm/gateway.py` | `tests/e2e/test_guided_troubleshooting.py` |
| When the platform is behind, the model is not called at all | `surge.py` | `tests/e2e/test_surge_mode.py`; `loadtest/reports/` |
| All MCP clients together may make 60 calls a minute, readings of the two documents included; a call beyond that is refused before anything is asked for it. The first such refusal of a minute is in the trail and the rest are in the log, so that a refusal does not cost a write each time. The tools call no model | `mcp_server/tools.py`, `api/ratelimit.py` | `tests/integration/test_mcp_server.py` |
| Limits on new contacts for each caller and on messages for each contact; a caller's first contact is never limited, because it must get its reference. A channel integration as a whole is capped (600 at once, then 60 a second), first contacts included | `api/ratelimit.py`, `api/app.py` | `tests/integration/api/test_api.py` |
| The operations queue cannot be written full by one caller: one item for each kind of trouble and subject, a cap on items about locked customers. (The staff queue takes one item per contact and reason; a caller can open five new contacts a minute.) | `db/ops.py`, `agents/identity_guard.py` | `tests/integration/test_identity_attempts.py` |

*What is left.* The limits live in one API process and count sessions. The demo on AWS has
one shared sign-in at its edge and no budget alarm for model spend (M5).

## Beyond the LLM list

| Risk | Control | Shown by | What is left |
|---|---|---|---|
| Broken access control | A token on every route but the health check, the static pages and, in `dev` only, the token endpoint (which there signs anyone in as any customer); roles enforced in code; every route against every role as one table; object-level ownership | `tests/integration/api/test_role_matrix.py`; I1, I3, E5 | Staff sign-in is a stand-in (a shared key and a typed name). The demo on AWS runs in `dev` for it, so its token endpoint is up; the shared sign-in keeps it from the internet |
| Forged or altered tokens | One algorithm, issuer, audience and expiry required; a short signing secret refused at start-up | I1 | Tokens are issued by SkyLink's identity provider in the target deployment |
| The MCP endpoint reached by someone it is not for | Over HTTP every request but the health check must carry the key, compared in constant time: the guard names what is open, so a path it was never told about, or a prefix the server is mounted under, is closed too. With no key configured it answers nobody. A request that has the key is still turned away under a host name other than the loopback names, so a page in a browser cannot drive a local server through a name of its own, even with a key it has come by. Bound to loopback on a laptop and on the AWS host, where Caddy does not serve it | `tests/unit/test_mcp_http.py`; `scripts/smoke.py` journey 9 | One key for every client, and the trail names them all `mcp:client`. Sign-in for each assistant belongs to a deployment with real staff sign-in (ADR 0003). Over stdio there is no key: whoever can start the process is the client |
| Guessing | The identity lock; equal answers | E3, E4 | Decision D9 |
| Replay and double writes | Idempotency keys on intake and on every write; an approval decided once | C4; `tests/e2e/test_reliability.py` | |
| Secrets | From the environment only; generated by `make init`; redacted from logs and trail by name and by shape | `tests/unit/test_logging_redaction.py` | On a laptop, `.env`. The demo on AWS keeps its settings in Secrets Manager, which only the host's role may read, and never in Terraform's state; nothing rotates them |
| Tampering with the record | The audit log refuses update, delete and truncate, by trigger | `tests/integration/test_audit_and_reference.py` | One database role for every process, the one that owns the schema: a process that is taken over can write to any table and can switch the trigger off. (On RDS that role is no longer a superuser: it cannot create roles or databases. The master user's password is in none of the processes' settings, and the containers cannot reach the host's role, which can read it: the metadata service answers one hop away, the host itself.) Separate roles, with only the migrations allowed to alter the audit log, are not built. Other records (contacts, messages, actions) are updated in place |
| Transport and storage | The demo on AWS: HTTPS at Caddy with a Let's Encrypt certificate; only 80 and 443 open; an encrypted disk; the instance metadata service (IMDSv2 only) out of the containers' reach. The database is RDS, encrypted at rest, private to the host's security group, and reached over TLS with its certificate checked against the RDS certificate authorities | `deploy/aws/`, `deploy/aws/terraform/database.tf` | Inside the host the containers talk plain HTTP on Docker's network. A laptop stack has no TLS |
| Denial of the unsigned-in path to one customer | Whoever knows a customer ID can keep its identity check locked | Stated in `docs/EXECUTION_PLAN.md` | The customer keeps the signed-in path, the caller's line and a person |

## What was not tried

- Claude against the attack suite (it can be run with one setting; no key was available), and
  any live model more than a few times: each live run, with OpenAI's `gpt-5.4-mini`, read each
  of the attacks made of words once.
- The MCP server with an assistant that is itself being attacked. What a client's model does
  with a tool's answer, and what a person can talk it into, is on the client's side; the
  server was tried with the protocol's own client library, in process and over HTTP.
- Attacks that take many turns and a patient attacker working on a member of staff. The
  staff see summaries built from recorded facts, and no approval can be raised by a note
  (A7), but people can be talked into things no control sees.
- Timing: whether a refused identity answer returns measurably sooner than a checked one.
- Speech: instructions hidden in a call transcript by how they sound. Transcripts arrive as
  text and are read like any other text.
- Load as an attack at the edge, and anything at the level of network or host.
- The stand-in enterprise systems themselves. They are not SkyLink's.
