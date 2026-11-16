# SkyLink outage-aware customer care agent

Capstone build for **G3 Telco — Outage-Aware Customer Care Agent** (FDE programme). It makes customer care "know what the network knows": every contact gets a complaint reference at once, customers on a failed network node are told about the outage instead of being walked through a reboot script, and no enterprise write happens twice.

- Plan, milestones and decisions still open: [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md)
- Contracts of the stand-in data pack and mock APIs: [`docs/contracts/`](docs/contracts/)

## Status

Milestones M0 (foundations), M1 (reference-first outage path) and M2 (the six capabilities) are built and tested: the NOC event feed, proactive notifications, remote line tests, cited troubleshooting, service credits with approval, technician visits, the disputed-outage path with a mobile journey, the staff desk, and scored evaluations of three of the brief's metrics. Everything after M2 is not built.

**Works today**

- **Reference first.** Contact, complaint reference and wake-up event commit in one transaction before any model or dependency call.
- **Triage.** Identity from the signed-in channel, the caller line, or two things the customer knows; intent from masked text.
- **Network truth from two sources.** A three-state decision (`ACTIVE`, `CONFIRMED_CLEAR`, `UNKNOWN`) from the live status and from a read model fed by the NOC event feed. The feed can prove an outage while the Network API is down; neither a silent nor a stale feed is ever an all-clear.
- **Outage reply** with sourced ETA; one incident per node episode; complaint registered and linked, through an action ledger and a separate executor.
- **Suspected faults.** A fault that is suspected but not yet declared (equipment alarm, or three lines failing on one node) is raised once for the node and every complaint is linked to that ticket. When the NOC declares the outage the same ticket is upgraded and the waiting customers are told.
- **Proactive notifications.** Customers on an affected node are told when an outage is declared and again when it is resolved. Consent is read from the CRM at the moment of sending; one message per customer, outage and kind; nothing personal in the text.
- **Restoration.** A waiting customer is told when their outage is resolved, and the contact closes when they confirm or after a quiet period.
- **Remote line tests.** The test names the node that really serves the line, which repairs a missing, ambiguous, stale or simply wrong address mapping. A failed line on a healthy-looking node is watched for the NOC's detection window before any repair is arranged.
- **Guided troubleshooting.** With the node confirmed clear and the customer's line passing its test, a customer who asks gets the steps from the guide for **their own router model**, quoted verbatim with guide, section and version. The model comes from the service record, never from the message. Factory reset is never offered; steps are given once, then a person takes over.
- **Service credits.** When an outage a customer waited on is resolved, a credit is worked out in code from the versioned policy file (never by a model, never from what the customer wrote) and offered in the same message. It is proposed with its amount frozen; an agent, or a supervisor above INR 500, approves that exact request through the API; only then does the executor apply it to Billing, one customer at a time and never twice. The customer is told the outcome, and the contact stays open until it is settled.
- **Technician visits.** A visit is offered only when the customer's own line still fails its test after the hold, on a node proven clear. The customer is shown the earliest free slots and chooses one; the reply is read by rules and narrowly, so "I can't do 2" or "ok, how much is it?" books nothing and is asked again. The executor books the visit after looking at the node, the line's last test and Field Service's bookings for itself, and declines when any of it has changed: an outage declared in the meantime means no visit, however the customer answered. One visit per service.
- **A customer who disputes the outage.** Evidence decides. While an outage is active on the customer's node, nothing is arranged for their own equipment, however sure they are that the router is at fault: they are told the outage with its evidence, why a restart, a replacement or a technician would not help while it lasts, and that their line is tested the moment it is over. If they say it again, a person takes the contact, with the evidence.
- **Mobile, as a thin path.** A tower outage is told, ticketed, restored and credited (by the mobile rule of the policy) like a broadband outage, in words that never mention a router. There is no remote test of a mobile connection here and no guide for a phone: on a working tower the customer is told what the network shows and a person takes over.
- **Degraded mode.** When nothing can be verified: reference still issued, no all-clear, no device steps; the status is re-checked on a timer and a person takes over if it stays unverified.
- **Staff desk** (`/staff.html`). What the assistant could not or may not settle on its own: the credits that wait for a decision, each shown exactly as it was frozen, with its working, and approved or rejected there; and the queue of contacts passed to a person, each with the handover summary, the conversation and the audit trail, closed with a note once dealt with. Every decision carries the name of who took it.
- **Evaluations** (`make eval`). Scored replays through the real workflow on the stand-in data: outage detected for 97.47% of affected contacts (target 95%); credits match the policy in 4,050 of 4,050 cases (target 100%); none of the baseline's unnecessary technician visits is booked by the workflow, and every needed one is. What these figures do and do not show is under "Test it".
- Per-contact audit trail, API with roles, per-caller rate limits, customer desk.

**Not built yet**

- Credits for affected customers who did not contact us. A reply to the customer from the staff desk (a person answers by their own channel). Real staff sign-in.
- Changing or cancelling a booked visit (a person does that); a visit for a line that passes its test.
- Anything for mobile beyond the outage path (no tower inference, no device guidance, no SIM or plan checks).
- A learned embedding model for retrieval (the index uses a deterministic hashing embedder; see "Guide retrieval").
- Surge test, circuit breaker, dashboards and alerts.
- The evaluations on the container stack at 400,000 customers and on the demo's language model (they run in process, on a 1,000-customer pack, with the offline rules and with weights measured on the large pack).
- Simulator controls in the console (outages and faults are started with the admin API or the smoke run).

## Two things that are stand-ins

The brief says SkyLink supplies the datasets, the credit policy, the router guides and mock REST APIs. None of them had been received when this was built, so the repository contains replacements that sit behind narrow boundaries:

- **`skylink_datagen`** generates a seeded synthetic data pack at the scale the brief states (400,000 customers, 6 months of events, 20,000 transcripts) with deliberate mapping defects. **`mock_oss`** is a stateful mock of the Network Monitoring, CRM, Billing, Field Service and Incident Management APIs with fault injection. The agent system reaches them only through `skylink_care/adapters` and `skylink_care/pipelines`, so the real ones replace them there.
- **`data/policies/credit_policy.v0.yaml`** is a synthetic placeholder policy. Every number in it is an assumption. It is labelled `v0-placeholder` wherever it is used.

## Run it

Requirements: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Docker with Compose.

```bash
make init        # writes .env with freshly generated local secrets; on an existing .env it only adds a secret that is new
make install     # installs the locked dependencies
make up          # PostgreSQL with pgvector, on 127.0.0.1:55432
make data        # synthetic data pack, 40,000 customers (CUSTOMERS=400000 for full scale)
make dev         # migrate, seed, then start the gateway, API, orchestrator, executor and feed consumer
make smoke       # live journeys over HTTP, with checks
make demo        # instead of `make dev`: the wait before a visit is offered is 20 seconds, not 10 minutes
```

`make smoke` runs seven journeys against `make dev`. Against `make demo` it runs an eighth: the failing line is still failing after the hold, a visit is offered, the customer picks the second slot and the executor books it.

Open <http://127.0.0.1:8080/> for the customer desk and <http://127.0.0.1:8080/staff.html> for the staff desk. The staff desk asks for a role, a name and the staff key: `grep CARE_DEV_STAFF_KEY .env` shows it. On the customer desk, leave the customer ID empty to arrive as an unidentified guest, or enter one; `make smoke` prints an outage being started, and the simulator can find customers for you:

```bash
set -a; . ./.env; set +a
curl -s -H "X-API-Key: $OSS_KEY_ADMIN" \
  "http://127.0.0.1:8081/admin/v1/customers/search?service_type=broadband&mapping_defect=none&limit=3"
curl -s -X POST -H "X-API-Key: $OSS_KEY_ADMIN" -H "Content-Type: application/json" \
  -d '{"node_id":"<true_node_id from above>","cause":"FIBRE_CUT","eta_minutes":120}' \
  http://127.0.0.1:8081/admin/v1/outages
```

Starting an outage this way also notifies the consenting customers on that node; the simulated SMS and push messages can be read back from the gateway:

```bash
curl -s -H "X-API-Key: $OSS_KEY_ADMIN" "http://127.0.0.1:8081/notify/v1/messages?outage_id=<outage_id>&limit=5"
curl -s -X POST -H "X-API-Key: $OSS_KEY_ADMIN" http://127.0.0.1:8081/admin/v1/outages/<outage_id>/resolve
```

`make stop` stops the processes. Logs are in `var/log/`.

`make dev` (and `make stack-up`) re-seeds the mock gateway, which empties its tickets and restarts its event feed, so the care platform's own contacts, actions and network state are cleared in the same step (`care-admin reset-runtime`, refused unless `CARE_ENV` is `dev` or `test`). To restart the processes and keep the state, use `./scripts/dev_stack.sh restart`.

The feed consumer's position and health can be read at any time. If an event was lost or could not be applied, the outage read model is marked as having a gap and stops being used as evidence (the live status still is) until someone has compared it with the NOC's list and clears the mark:

```bash
uv run --env-file .env care-admin feed-status      # exits 1 while a gap is marked
uv run --env-file .env care-admin feed-clear-gap
```

Everything in containers instead (generates and seeds a data pack on first start):

```bash
make init && make stack-up      # then: make smoke
make stack-down
```

## Test it

```bash
make lint typecheck     # ruff and mypy --strict
make test               # unit tests, no services needed
make test-all           # everything with coverage (needs `make up`)
```

`make test-all` runs unit, integration (real PostgreSQL), contract (adapters against the mock gateway, in process) and end-to-end journey tests. The language model is replaced by the offline rules provider in tests; nothing else is stubbed.

### Evaluations

```bash
make eval               # four scored runs, about a minute (needs `make up`)
```

`make eval` replays contacts through the real workflow and rewrites `evals/reports/`. Everything is measured on the synthetic stand-in data; none of it is a measurement of SkyLink's network, policy or customers. How each figure is measured is in [`evals/README.md`](evals/README.md); what it rests on is at the end of each report.

| Brief metric | Result on the stand-in data | Report |
|---|---|---|
| Outage detected for affected customers ≥ 95% | **97.47%** of affected contacts, weighted by how often each kind occurs in the 400,000-customer pack. 140 contacts replayed: before and after the NOC declares the outage, with and without an alarm, every kind of mapping defect. What is missed is mobile, which has no remote test: no hold before the NOC declares, no mapping repair | `outage_detection` |
| Unnecessary truck rolls cut ≥ 50% (simulation) | The workflow books **none** of the baseline's 3,225 unnecessary visits, and every needed one after the 10-minute hold. A customer who goes on asking for a technician always ends with a person, so the cut end to end depends on what that person decides, which no replay can measure. If every customer insisted, the target would hold only while staff turn down at least half of them | `truck_rolls` |
| Credits match policy 100% | **4,050 of 4,050** grid cases agree with an independent calculation in exact arithmetic, 14 of 14 with amounts worked out by hand | `credit_policy` |
| (no brief metric: guide retrieval) | Right section for **81.25%** of 48 descriptions written without sight of the retriever (always answering "No internet" would score 47.92%); 99% on the phrases its rules were written from. Never a step from another router model's guide | `retrieval` |

## Configuration

All settings come from the environment. `.env.example` lists every variable; `make init` creates `.env` from it. No secret is committed, and no process reads `.env` by itself: the launchers pass each process only what it needs.

| Variable | Used by | Meaning |
|---|---|---|
| `CARE_DEV_STAFF_KEY` | API | Local demos only. What a person types into the staff desk to sign in as an agent or a supervisor; `make init` generates it. Unset or shorter than 16 characters: no staff token can be had from the dev token endpoint |
| `CARE_ENV` | all | `dev` enables the local token endpoint used by the console. Anything else leaves it off; unset means `prod`. In `dev` that endpoint gives a customer or guest token to whoever asks, and a staff token only to someone who knows `CARE_DEV_STAFF_KEY`. It is a stand-in for sign-in on a machine nobody else can reach, not sign-in |
| `CARE_DB_HOST`, `CARE_DB_PORT`, `CARE_DB_NAME`, `CARE_DB_USER`, `CARE_DB_PASSWORD` | all | PostgreSQL connection |
| `CARE_JWT_SECRET` | API only | Signs and verifies API tokens (at least 32 characters) |
| `CARE_API_HOST`, `CARE_API_PORT` | API | Listen address |
| `CARE_OSS_BASE_URL` | orchestrator, executor, feed consumer | OSS/BSS gateway |
| `OSS_KEY_TRIAGE`, `OSS_KEY_DIAGNOSTICS`, `OSS_KEY_RESOLUTION`, `OSS_KEY_DISPATCH`, `OSS_KEY_NOTIFICATION` | orchestrator | One read-only key per agent identity |
| `OSS_KEY_EXECUTOR` | executor only | The only key that can write to enterprise systems |
| `OSS_KEY_INGEST` | feed consumer only | Reads the NOC event feed and nothing else |
| `OSS_KEY_ADMIN` | simulator controls | Scenario and fault injection. No care process holds it |
| `CARE_STATUS_MAX_AGE_SECONDS`, `CARE_FEED_MAX_AGE_SECONDS` | orchestrator | How old network evidence may be |
| `CARE_LINE_TEST_CLUSTER_THRESHOLD`, `CARE_LINE_TEST_CLUSTER_WINDOW_SECONDS`, `CARE_LINE_RETEST_SECONDS` | orchestrator | How many lines failing on one node, within what time, count as a node fault; how soon a line may be tested again |
| `CARE_DISPATCH_HOLD_SECONDS` | orchestrator, executor | How long a failed line on a healthy-looking node is watched before a visit is offered. Both judge it: the executor checks the visit again before booking |
| `CARE_VISIT_SLOTS_OFFERED`, `CARE_VISIT_LINE_TEST_MAX_AGE_SECONDS`, `CARE_VISIT_EVIDENCE_MAX_AGE_SECONDS` | orchestrator, executor | How many slots are offered; how old the failing line test may be when a visit is proposed, and when it is booked |
| `CARE_NETWORK_RECHECK_SECONDS`, `CARE_NETWORK_RECHECK_LIMIT`, `CARE_SUSPECTED_WAIT_SECONDS` | orchestrator | How often an unverified network status is looked at again, and how long before a person takes over |
| `CARE_OUTAGE_RECHECK_SECONDS` | orchestrator | How often the live status is looked at for a customer waiting on a declared outage, behind the event feed |
| `CARE_CLOSE_QUIET_SECONDS` | orchestrator | How long a contact waits for the customer before it is closed (assumption; default 24 h) |
| `CARE_NOTIFY_MAX_EVENT_AGE_SECONDS` | feed consumer | An outage learnt later than this is recorded but not announced |
| `CARE_CREDIT_POLICY_PATH`, `CARE_CREDIT_REVIEW_ALL` | orchestrator, executor | Policy file; whether every credit waits for a person. Both processes read them: one proposes a credit, the other checks again before applying it |
| `CARE_APPROVAL_TTL_SECONDS` | orchestrator | How long an approval may wait |
| `CARE_KNOWLEDGE_DIR` | `care-admin index-guides` | The knowledge pack whose router guides are indexed |
| `CARE_LLM_PROVIDER`, `CARE_LLM_MODEL`, `ANTHROPIC_API_KEY` | orchestrator | See below |
| `MOCK_OSS_HOST`, `MOCK_OSS_PORT`, `MOCK_OSS_DB`, `MOCK_OSS_LINE_TEST_DELAY_MS` | mock gateway | Listen address, store, simulated line-test delay |

### Service credits and approvals

The policy is `data/policies/credit_policy.v0.yaml`, a **synthetic placeholder** (see above): every credit, message and approval names its version. The amount is `monthly fee × billable hours × tier multiplier / 720`, at least the policy minimum, capped per billing cycle, rounded once. Planned maintenance and outages shorter than the minimum earn nothing; a customer who asked is told why.

A credit is proposed under the identity "one per service per outage" together with an approval request that carries a hash of the frozen request. `GET /v1/approvals` shows an agent or supervisor what they may decide, with the working; `POST /v1/approvals/{id}/decision` must quote the hash back, so a decision cannot land on a request the approver did not see. Above the policy limit only a supervisor may decide. `CARE_CREDIT_REVIEW_ALL=true` (the default) keeps a person in the loop for smaller credits too; whether they may be applied without review is an open decision (D3). An approval nobody gives within `CARE_APPROVAL_TTL_SECONDS` ends the credit unapplied and a person follows up.

The executor applies a credit possibly days after it was proposed, and takes nothing from the proposal on trust. Immediately before the write it checks that the stored request still matches the hash the approver saw, works out from the amount which approval the policy requires and looks for exactly that, and, holding a lock on the customer, reads the account: a credit already there for the service and outage means this one is skipped, and the outage credits of the billing cycle must stay within the policy's cap with this one added. Credits that are proposed and not yet applied count towards the cap when the next one is priced. A credit found on the account under the action's own key is the answer to an earlier attempt and is never sent again.

The customer hears the outcome of every credit, in the amount that was frozen: applied (with Billing's reference), already on the account, not applied (a person follows up), or, when Billing stopped answering and the executor gave up, "could not be confirmed" (a person checks the account first; nobody is told it was not applied). This holds when a second outage brought a second credit, and when a person has taken the conversation over in the meantime.

To see a credit locally, start an outage that began five hours ago, contact as a customer on that node, resolve it, and approve as an agent (the smoke run does exactly this):

```bash
curl -s -X POST -H "X-API-Key: $OSS_KEY_ADMIN" -H "Content-Type: application/json" \
  -d '{"node_id":"<node>","cause":"FIBRE_CUT","started_minutes_ago":300}' \
  http://127.0.0.1:8081/admin/v1/outages
```

### Technician visits

A visit is the remedy that costs a journey, and the one most often arranged for nothing: a line that fails in the minutes before the NOC declares an outage is repaired by the NOC, not by a technician at the door. The rule (`domain/dispatch.py`, pure and unit-tested) asks for proof on every point and says what is missing:

| Needed | Otherwise |
|---|---|
| Service active, no visit still to come for it | `SERVICE_INACTIVE`, `VISIT_ALREADY_BOOKED` |
| Node `CONFIRMED_CLEAR` on a live answer taken after the hold and fresh now | `OUTAGE_ACTIVE`, `NETWORK_UNVERIFIED`, `CLEAR_NOT_FRESH` |
| The line failed, the hold has passed since the first failure, and a recent test made after the hold still fails | `LINE_PASSING`, `LINE_FAULT_NOT_PROVEN`, `HOLD_NOT_OVER`, `RETEST_NEEDED` |
| The customer chose a slot | `NO_CONSENT` (checked last, so it means "everything else holds: ask") |

The rule is applied twice. The orchestrator applies it after a fresh diagnosis, offers slots, reads the customer's reply and proposes a `VISIT_BOOK` action tied to the message that made the choice. The executor applies it again immediately before booking, on evidence it reads with its own key (the node's live state and the feed, the line's latest stored test, Field Service's bookings for the service), and ends the action as `SKIPPED` with the reason when it no longer holds. A unique index allows one booking in progress per service. A slot that filled up in the meantime leads to a fresh offer; a booking the customer could not be told about because a person had taken over is told from there.

To see it locally, `make demo`, then make a line fail and contact as that customer:

```bash
curl -s -X POST -H "X-API-Key: $OSS_KEY_ADMIN" -H "Content-Type: application/json" \
  -d '{"service_id":"<service>","fault":true}' http://127.0.0.1:8081/admin/v1/line-faults
```

### Guide retrieval

`care-admin index-guides` (part of `make seed`) reads the twelve router guides, splits them into their sections and stores them in PostgreSQL with a text-search column and a pgvector column. A search is filtered before it is ranked: the customer's router model (from the service record), the role, and the list of sections an automated answer may quote are conditions of the query itself. Ranking then adds four signals: the kind of fault the customer's latest message names (keyword rules in `rag/symptoms.py`: no service, Wi-Fi, slow) points to a section; the customer's words name the section's topic; they occur in its text; and the texts are close in vector space.

The vectors come from a **deterministic hashing embedder**: no model, no download, no network, and the same index on every machine. It is a lexical signal in vector form, not a language model, and the index records it as such. A learned embedding model can replace it behind the same interface; an index built by one embedder refuses queries from another. Retrieval is checked in the tests with ten customer phrasings against each of the twelve guides, and scored by `make eval`: the right section for 81.25% of descriptions written without sight of the retriever, against 99% for the phrases its rules were written from. The gap is what keyword rules cost; they stand in for a model that reads the fault (decision D2).

The index build refuses, as a whole, any document that is not a device guide, a second guide for the same model, and any guide whose quotable steps tell the customer to do something hazardous (reset, open the unit, unplug the fibre).

### Language model

`CARE_LLM_PROVIDER=offline` (the default) uses deterministic keyword rules: no network, no key. It is also the fallback whenever a model call fails or times out.

`CARE_LLM_PROVIDER=anthropic` uses Claude through the official SDK with a JSON-schema output; set `ANTHROPIC_API_KEY`. The default model is `claude-opus-5-5`, and the request opts into the API's server-side refusal fallback. This path is unit-tested against a stand-in client but **has not been run against the live API**: no key was available on the build machine. Which model the team may use for the demo is an open decision (D2 in the plan).

The model only reads the customer's message for intent, on masked text, and only once the customer is identified. It never decides identity, network status, eligibility or amounts.

## How it is put together

```
src/skylink_care/     the agent system
  domain/             pure logic: complaint references, credit engine, network decision
  db/                 pool, SQL migrations, repositories (ledger, inbox, action ledger, audit)
  adapters/           typed clients for the OSS/BSS gateway; every failure is a typed result
  agents/             triage, diagnostics, customer reply and notification templates
  orchestrator/       LangGraph workflow, deterministic routing, worker
  ingest/             NOC event feed consumer: read model, notifications, wake-ups
  executor/           the only process that writes to enterprise systems
  api/                FastAPI app, roles, rate limits
  rag/                guide parsing, embedding and the guide index (retrieval)
  guardrails/ llm/ observability/ pipelines/ web/
src/mock_oss/         stand-in OSS/BSS gateway (never imported by skylink_care)
src/skylink_datagen/  seeded synthetic data and knowledge pack
tests/                unit/ integration/ contract/ e2e/ plus the two stand-ins' own suites
```

The eight design rules the code is held to are in section 3 of the plan. The short version: reference first; evidence decides and language explains; a failed or stale network check is never an all-clear; agents propose and only the executor writes, replaying the identical frozen request when an outcome is unknown; identities are enforced by database constraints; every event is applied exactly once.

Five processes run from one image, each with only its own credentials: the API (no gateway key), the orchestrator (read-only agent keys), the feed consumer (one key that can read the event feed), the executor (the only write key) and, for local use, the mock gateway.

## AI assistance

This repository was written with **Claude Code** (Anthropic), model Claude Fable 5.2. Claude Code produced the execution plan, the code and the tests in this repository. Within that session, sub-agents wrote the synthetic data generator and the mock gateway from written contracts, and other sub-agents reviewed the plan, the architecture and the code (Python, security and silent-failure reviews); their findings were fixed and are covered by tests. For the retrieval evaluation a sub-agent that was shown one router guide and none of the retriever wrote and labelled the independent descriptions, and other sub-agents reviewed the evaluations for whether each report claims only what was measured. The two supplied documents (the brief and the team's action plan) were written by people and are not modified here.
