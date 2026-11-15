# SkyLink outage-aware customer care agent

Capstone build for **G3 Telco — Outage-Aware Customer Care Agent** (FDE programme). It makes customer care "know what the network knows": every contact gets a complaint reference at once, customers on a failed network node are told about the outage instead of being walked through a reboot script, and no enterprise write happens twice.

- Plan, milestones and decisions still open: [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md)
- Contracts of the stand-in data pack and mock APIs: [`docs/contracts/`](docs/contracts/)

## Status

Milestones M0 (foundations) and M1 (reference-first outage path) are built and tested. M2 onward is not built yet.

| Works today | Not built yet |
|---|---|
| Reference-first intake: contact, complaint reference and wake-up event commit in one transaction before any model or dependency call | Event feed consumer, outage read model, proactive notifications |
| Triage: identity from the signed-in channel, the caller line, or two things the customer knows; intent from masked text | Remote line tests, mapping repair, guided troubleshooting with retrieval and citations |
| Network diagnostics with a three-state decision (`ACTIVE`, `CONFIRMED_CLEAR`, `UNKNOWN`), including the "alarm but no declared outage yet" case | Credit proposals, supervisor approval screen, billing write (the credit **engine** and its tests exist) |
| Outage reply with sourced ETA; one incident per node episode; complaint registered and linked, through an action ledger and a separate executor | Technician dispatch |
| Degraded mode when the Network API is down: reference still issued, no all-clear, handed to a person | Surge test, circuit breaker, dashboards and alerts, evaluation reports |
| Per-contact audit trail, API with roles, per-caller rate limits, demo console | Full console (supervisor queue, simulator controls) |

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
make dev         # migrate, seed, then start the gateway, API, orchestrator and executor
make smoke       # two live journeys over HTTP, with checks
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

`make stop` stops the processes. Logs are in `var/log/`.

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
| `CARE_OSS_BASE_URL` | orchestrator, executor | OSS/BSS gateway |
| `OSS_KEY_TRIAGE`, `OSS_KEY_DIAGNOSTICS`, `OSS_KEY_RESOLUTION`, `OSS_KEY_DISPATCH`, `OSS_KEY_NOTIFICATION` | orchestrator | One read-only key per agent identity |
| `OSS_KEY_EXECUTOR` | executor only | The only key that can write to enterprise systems |
| `OSS_KEY_INGEST`, `OSS_KEY_ADMIN` | gateway; simulator controls | Event feed (M2); scenario and fault injection |
| `CARE_STATUS_MAX_AGE_SECONDS`, `CARE_FEED_MAX_AGE_SECONDS` | orchestrator | How old network evidence may be |
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
  agents/             triage, diagnostics, customer reply templates
  orchestrator/       LangGraph workflow, deterministic routing, worker
  executor/           the only process that writes to enterprise systems
  api/                FastAPI app, roles, rate limits
  guardrails/ llm/ observability/ pipelines/ web/
src/mock_oss/         stand-in OSS/BSS gateway (never imported by skylink_care)
src/skylink_datagen/  seeded synthetic data and knowledge pack
tests/                unit/ integration/ contract/ e2e/ plus the two stand-ins' own suites
```

The eight design rules the code is held to are in section 3 of the plan. The short version: reference first; evidence decides and language explains; a failed or stale network check is never an all-clear; agents propose and only the executor writes, replaying the identical frozen request when an outcome is unknown; identities are enforced by database constraints; every event is applied exactly once.

## AI assistance

This repository was written with **Claude Code** (Anthropic), model Claude Fable 5.2. Claude Code produced the execution plan, the code and the tests in this repository. Within that session, sub-agents wrote the synthetic data generator and the mock gateway from written contracts, and other sub-agents reviewed the plan, the architecture and the code (Python, security and silent-failure reviews); their findings were fixed and are covered by tests. The two supplied documents (the brief and the team's action plan) were written by people and are not modified here.
