# SkyLink outage-aware customer care agent

Capstone build for **G3 Telco — Outage-Aware Customer Care Agent** (FDE programme). It makes customer care "know what the network knows": every contact gets a complaint reference at once, customers on a failed network node are told about the outage instead of being walked through a reboot script, and no enterprise write happens twice.

- Plan, milestones and decisions still open: [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md)
- Contracts of the stand-in data pack and mock APIs: [`docs/contracts/`](docs/contracts/)

## Status

Milestones M0 (foundations) and M1 (reference-first outage path) are built and tested. M2 is in progress: its first two slices (the NOC event feed and proactive notifications) are built; the rest of M2 and everything after it is not.

| Works today | Not built yet |
|---|---|
| Reference-first intake: contact, complaint reference and wake-up event commit in one transaction before any model or dependency call | Remote line tests, mapping repair, guided troubleshooting with retrieval and citations |
| Triage: identity from the signed-in channel, the caller line, or two things the customer knows; intent from masked text | Credit proposals, supervisor approval screen, billing write (the credit **engine** and its tests exist) |
| Network diagnostics with a three-state decision (`ACTIVE`, `CONFIRMED_CLEAR`, `UNKNOWN`) from two sources: the live status and a read model fed by the NOC event feed. The feed can prove an outage while the Network API is down; neither a silent nor a stale feed is ever an all-clear | Technician dispatch |
| Outage reply with sourced ETA; one incident per node episode; complaint registered and linked, through an action ledger and a separate executor | The "network says outage, customer says device" conflict path; a mobile journey |
| A fault that is suspected but not yet declared (equipment alarm, no outage) is raised once for the node and every complaint is linked to that ticket; when the NOC declares the outage the same ticket is upgraded and the waiting customers are told | Surge test, circuit breaker, dashboards and alerts, evaluation reports |
| Proactive notifications: customers on an affected node are told when an outage is declared and again when it is resolved. Consent is read from the CRM at the moment of sending; one message per customer, outage and kind; nothing personal in the text | Full console (supervisor queue, simulator controls) |
| Restoration: a waiting customer is told when their outage is resolved and the contact closes when they confirm, or after a quiet period | |
| Degraded mode when nothing can be verified: reference still issued, no all-clear, no device steps; the status is re-checked on a timer and a person takes over if it stays unverified | |
| Per-contact audit trail, API with roles, per-caller rate limits, demo console | |

## Two things that are stand-ins

The brief says SkyLink supplies the datasets, the credit policy, the router guides and mock REST APIs. None of them had been received when this was built, so the repository contains replacements that sit behind narrow boundaries:

- **`skylink_datagen`** generates a seeded synthetic data pack at the scale the brief states (400,000 customers, 6 months of events, 20,000 transcripts) with deliberate mapping defects. **`mock_oss`** is a stateful mock of the Network Monitoring, CRM, Billing, Field Service and Incident Management APIs with fault injection. The agent system reaches them only through `skylink_care/adapters` and `skylink_care/pipelines`, so the real ones replace them there.
- **`data/policies/credit_policy.v0.yaml`** is a synthetic placeholder policy. Every number in it is an assumption. It is labelled `v0-placeholder` wherever it is used.

## Run it

Requirements: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Docker with Compose.

```bash
make init        # writes .env with freshly generated local secrets (never overwrites)
make install     # installs the locked dependencies
make up          # PostgreSQL with pgvector, on 127.0.0.1:55432
make data        # synthetic data pack, 40,000 customers (CUSTOMERS=400000 for full scale)
make dev         # migrate, seed, then start the gateway, API, orchestrator, executor and feed consumer
make smoke       # four live journeys over HTTP, with checks
```

Open <http://127.0.0.1:8080/>. Leave the customer ID empty to arrive as an unidentified guest, or enter one; `make smoke` prints an outage being started, and the simulator can find customers for you:

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

## Configuration

All settings come from the environment. `.env.example` lists every variable; `make init` creates `.env` from it. No secret is committed, and no process reads `.env` by itself: the launchers pass each process only what it needs.

| Variable | Used by | Meaning |
|---|---|---|
| `CARE_ENV` | all | `dev` enables the local token endpoint used by the console. Anything else leaves it off; unset means `prod` |
| `CARE_DB_HOST`, `CARE_DB_PORT`, `CARE_DB_NAME`, `CARE_DB_USER`, `CARE_DB_PASSWORD` | all | PostgreSQL connection |
| `CARE_JWT_SECRET` | API only | Signs and verifies API tokens (at least 32 characters) |
| `CARE_API_HOST`, `CARE_API_PORT` | API | Listen address |
| `CARE_OSS_BASE_URL` | orchestrator, executor, feed consumer | OSS/BSS gateway |
| `OSS_KEY_TRIAGE`, `OSS_KEY_DIAGNOSTICS`, `OSS_KEY_RESOLUTION`, `OSS_KEY_DISPATCH`, `OSS_KEY_NOTIFICATION` | orchestrator | One read-only key per agent identity |
| `OSS_KEY_EXECUTOR` | executor only | The only key that can write to enterprise systems |
| `OSS_KEY_INGEST` | feed consumer only | Reads the NOC event feed and nothing else |
| `OSS_KEY_ADMIN` | simulator controls | Scenario and fault injection. No care process holds it |
| `CARE_STATUS_MAX_AGE_SECONDS`, `CARE_FEED_MAX_AGE_SECONDS` | orchestrator | How old network evidence may be |
| `CARE_NETWORK_RECHECK_SECONDS`, `CARE_NETWORK_RECHECK_LIMIT`, `CARE_SUSPECTED_WAIT_SECONDS` | orchestrator | How often an unverified network status is looked at again, and how long before a person takes over |
| `CARE_OUTAGE_RECHECK_SECONDS` | orchestrator | How often the live status is looked at for a customer waiting on a declared outage, behind the event feed |
| `CARE_CLOSE_QUIET_SECONDS` | orchestrator | How long a contact waits for the customer before it is closed (assumption; default 24 h) |
| `CARE_NOTIFY_MAX_EVENT_AGE_SECONDS` | feed consumer | An outage learnt later than this is recorded but not announced |
| `CARE_CREDIT_POLICY_PATH`, `CARE_CREDIT_REVIEW_ALL` | orchestrator (M2) | Policy file; whether every credit waits for a person |
| `CARE_LLM_PROVIDER`, `CARE_LLM_MODEL`, `ANTHROPIC_API_KEY` | orchestrator | See below |
| `MOCK_OSS_HOST`, `MOCK_OSS_PORT`, `MOCK_OSS_DB`, `MOCK_OSS_LINE_TEST_DELAY_MS` | mock gateway | Listen address, store, simulated line-test delay |

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
  guardrails/ llm/ observability/ pipelines/ web/
src/mock_oss/         stand-in OSS/BSS gateway (never imported by skylink_care)
src/skylink_datagen/  seeded synthetic data and knowledge pack
tests/                unit/ integration/ contract/ e2e/ plus the two stand-ins' own suites
```

The eight design rules the code is held to are in section 3 of the plan. The short version: reference first; evidence decides and language explains; a failed or stale network check is never an all-clear; agents propose and only the executor writes, replaying the identical frozen request when an outcome is unknown; identities are enforced by database constraints; every event is applied exactly once.

Five processes run from one image, each with only its own credentials: the API (no gateway key), the orchestrator (read-only agent keys), the feed consumer (one key that can read the event feed), the executor (the only write key) and, for local use, the mock gateway.

## AI assistance

This repository was written with **Claude Code** (Anthropic), model Claude Fable 5.2. Claude Code produced the execution plan, the code and the tests in this repository. Within that session, sub-agents wrote the synthetic data generator and the mock gateway from written contracts, and other sub-agents reviewed the plan, the architecture and the code (Python, security and silent-failure reviews); their findings were fixed and are covered by tests. The two supplied documents (the brief and the team's action plan) were written by people and are not modified here.
