# Mock OSS/BSS API contract

**Status: stand-in.** The brief says SkyLink supplies mock REST APIs for Network Monitoring, CRM, Billing,
Field Service and Incident Management. They have not been received. `mock_oss` implements the contract
below so the agent system can be built and tested; `skylink_care/adapters` is the only code that knows
these paths. The Notification gateway and the `/admin` simulator controls are our additions.

One process, default `http://localhost:8081`. It keeps its own SQLite store and is seeded from the data
pack (`docs/contracts/data.md`). It uses `truth_service_node.csv` for what is *really* connected to what;
the care platform never sees that file.

## Conventions

- **Auth:** header `X-API-Key`. Missing or unknown key → `401 UNAUTHENTICATED`. Key lacks the scope →
  `403 FORBIDDEN`. Key values come from the environment; the client-to-scope table is fixed in code.
- **Correlation:** `X-Request-ID` is echoed on every response (generated if absent) and stored with any write.
- **Errors:** `{"error": {"code": "NODE_NOT_FOUND", "message": "…"}}` with a matching HTTP status. An
  invalid body or query → `422 VALIDATION_ERROR`.
- **Time:** all timestamps are UTC ISO 8601 with `Z`. Money is a decimal string with two places (`"812.50"`).
- **Idempotency (every `POST` that creates something):** header `Idempotency-Key` is required
  (`400 IDEMPOTENCY_KEY_REQUIRED`). Same key and same body → the original response body with the original
  status code and header `Idempotent-Replay: true`. Same key, different body → `409 IDEMPOTENCY_KEY_REUSED`.
  Keys are scoped per endpoint; a key over 200 characters → `400 IDEMPOTENCY_KEY_INVALID`. A
  lookup-by-key endpoint exists for every such resource.

### Clients and scopes

| Client | Env var holding its key | Scopes |
|---|---|---|
| `triage` | `OSS_KEY_TRIAGE` | `crm:profile`, `crm:verify` |
| `diagnostics` | `OSS_KEY_DIAGNOSTICS` | `network:read`, `network:test` |
| `resolution` | `OSS_KEY_RESOLUTION` | `billing:read` |
| `dispatch` | `OSS_KEY_DISPATCH` | `field:read` |
| `ingest` | `OSS_KEY_INGEST` | `network:feed` |
| `executor` | `OSS_KEY_EXECUTOR` | `billing:read`, `billing:write`, `field:read`, `field:write`, `incident:read`, `incident:write`, `notify:write`, `network:read`, `crm:consent` |
| `admin` | `OSS_KEY_ADMIN` | `admin` and every scope above |

## Network Monitoring

### `GET /network/v1/nodes/{node_id}/status` — scope `network:read`

```json
{
  "node_id": "BLR-OLT-0012",
  "status": "OUTAGE",
  "outage": {
    "outage_id": "OUT-20261115-0003", "version": 2, "cause": "FIBRE_CUT", "planned": false,
    "service_types": ["broadband"], "started_at": "2026-11-15T04:10:00Z",
    "eta": "2026-11-15T07:30:00Z", "eta_source": "field_update", "resolved_at": null
  },
  "active_alarms": [
    {"alarm_id": "ALM-7c21e0", "node_id": "BLR-AGG-0003", "alarm_type": "LINK_DOWN",
     "severity": "critical", "raised_at": "2026-11-15T04:10:04Z"}
  ],
  "observed_at": "2026-11-15T04:31:07Z",
  "feed_seq": 4812
}
```

`status` is `OUTAGE` or `OPERATIONAL`; `outage` is `null` when operational. Only outages the NOC has
**detected** are visible here: an outage that has started but whose detection delay has not elapsed
reports `OPERATIONAL`. Unknown node → `404 NODE_NOT_FOUND`. A decommissioned node → `410 NODE_DECOMMISSIONED`.

`active_alarms` lists alarms currently raised on the node **or on its parent AGG** (empty list when
none). Alarms appear as soon as equipment reports them, which is before the NOC declares an outage, so
during the detection delay the response is `OPERATIONAL` with a critical alarm. `feed_seq` is the highest
event sequence in the store when the answer was produced.

### `POST /network/v1/line-tests` — scope `network:test`

Body `{"service_id": "S0001234"}`. Takes 300–1500 ms.

```json
{
  "test_id": "LT-9f2c41d07a", "service_id": "S0001234", "node_id": "BLR-OLT-0012",
  "result": "FAIL",
  "checks": {"link": "DOWN", "optical_rx_dbm": null, "sync": false, "packet_loss_pct": 100.0},
  "tested_at": "2026-11-15T04:31:09Z"
}
```

`result` is `PASS`, `FAIL` or `INCONCLUSIVE`. Truth model: `FAIL` when the service's **true** node has an
outage in progress (detected or not) or the service has an individual line fault; `INCONCLUSIVE` only when
injected, and an injected `INCONCLUSIVE` wins even during an outage; otherwise `PASS`. About 1.2% of broadband
services (chosen by `mock-oss seed --seed`) test `FAIL` from the start, as faults of their own. A passing line: (`link: UP`, `optical_rx_dbm` between −24 and −14, `sync: true`,
`packet_loss_pct` below 1). `node_id` is the true node. Unknown service → `404 SERVICE_NOT_FOUND`.

### `GET /network/v1/events?after_seq=0&limit=200&wait_seconds=0` — scope `network:feed`

Ordered change feed of NOC events (schema of `network_events.jsonl` in the data contract, timestamps in UTC).
The pack's history is not loaded: after a seed the feed is empty, and every event comes from the simulator
controls (`/admin/v1`).

```json
{"events": [ {"seq": 4813, "event_id": "evt_…", "type": "OUTAGE_UPDATED", "…": "…"} ],
 "last_seq": 4813, "generated_at": "2026-11-15T04:31:10Z"}
```

Returns events with `seq > after_seq`, at most `limit` (≤ 500). If there are none it waits up to
`wait_seconds` (≤ 25) for one, then returns an empty list. `generated_at` is the feed heartbeat: a consumer
that cannot obtain a response has no evidence about the network. `last_seq` is the highest sequence in the
store, not in the page.

## CRM — scopes `crm:profile`, `crm:verify`, `crm:consent`

A scope for each thing that is read. `crm:profile`: the lookup and the profile. `crm:verify`: the check of what a caller knows. `crm:consent`: the one fact the executor needs before it sends a notice. Only `triage` holds the first two.

| Endpoint | Returns |
|---|---|
| `GET /crm/v1/customers/lookup?phone=` or `?customer_id=` or `?email=` | `{"matches": [{"customer_id", "name_masked", "phone_masked", "status"}]}` — zero, one or more; exactly one query parameter (`400 INVALID_LOOKUP` otherwise). Phone accepts `+91XXXXXXXXXX` or the bare 10 digits |
| `GET /crm/v1/customers/{customer_id}` | Full profile, below. `404 CUSTOMER_NOT_FOUND` |
| `GET /crm/v1/customers/{customer_id}/consent` | `{"outage_notifications": true, "channel": "sms", "updated_at": "…"}` — cheap send-time check |
| `POST /crm/v1/customers/{customer_id}/verify` | Body `{"phone_last4": "4821"}` → `{"verified": true}` |

```json
{
  "customer_id": "C0001234", "full_name": "Asha Menon", "phone": "+919845012345",
  "email": "asha.menon17@example.com", "segment": "consumer", "status": "active",
  "consent": {"outage_notifications": true, "channel": "sms", "updated_at": "2026-08-02T10:00:00Z"},
  "services": [
    {"service_id": "S0001234", "service_type": "broadband", "plan_name": "Fibre 300",
     "monthly_fee_inr": "999.00", "status": "active",
     "address": {"address_id": "A0001201", "area": "Indiranagar", "city": "BLR", "pincode": "560038"},
     "router_model": "AL-AX18", "activated_on": "2024-03-11", "billing_cycle_day": 5}
  ]
}
```

CRM does not return a network node. The care platform derives it from `address_node_map.csv`.

## Billing

| Endpoint | Scope | Behaviour |
|---|---|---|
| `GET /billing/v1/accounts/{customer_id}/credits?since=YYYY-MM-DD` | `billing:read` | `{"credits": [credit…]}` newest first. `since` is a day in IST: credits created from 00:00 IST that day |
| `POST /billing/v1/credits` | `billing:write` | Idempotent create. Body `{"customer_id", "service_id", "amount_inr", "reason_code", "outage_id", "policy_version", "complaint_ref", "approved_by"}` (`outage_id`, `approved_by` nullable). `201` with the credit. `422 INVALID_AMOUNT` if not `0 < amount ≤ 5000.00`; `404` for unknown customer or service; `409 ACCOUNT_SUSPENDED` |
| `GET /billing/v1/credits/by-key/{idempotency_key}` | `billing:read` | The credit or `404 NOT_FOUND` |

Credit: `{"credit_id": "CR-…", "customer_id", "service_id", "amount_inr": "812.50", "reason_code",
"outage_id", "policy_version", "complaint_ref", "approved_by", "status": "APPLIED", "created_at",
"idempotency_key"}`.

## Field Service

| Endpoint | Scope | Behaviour |
|---|---|---|
| `GET /field/v1/slots?pincode=560038&from_date=YYYY-MM-DD` | `field:read` | `{"slots": [{"slot_id": "SL-560038-20261116-AM", "start", "end", "remaining": 3}]}` — next 5 days, AM and PM |
| `POST /field/v1/appointments` | `field:write` | Idempotent create. Body `{"customer_id", "service_id", "slot_id", "reason", "complaint_ref"}`. `201` `{"appointment_id": "AP-…", "slot_id", "start", "end", "status": "BOOKED", …}`. `409 SLOT_FULL`, `404 SLOT_NOT_FOUND` |
| `GET /field/v1/appointments/by-key/{idempotency_key}` | `field:read` | The appointment or `404` |
| `GET /field/v1/appointments?service_id=&status=BOOKED` | `field:read` | `{"appointments": […]}` |
| `POST /field/v1/appointments/{appointment_id}/cancel` | `field:write` | Returns the appointment, `status: CANCELLED`; cancelling twice is a no-op; unknown → `404 APPOINTMENT_NOT_FOUND` |

## Incident Management

| Endpoint | Scope | Behaviour |
|---|---|---|
| `POST /incident/v1/incidents` | `incident:write` | Idempotent create. Body `{"node_id", "service_type", "kind", "outage_id", "summary"}`; `kind` is `NETWORK_OUTAGE` or `SUSPECTED_NODE_FAULT`; `outage_id` nullable. `201` `{"incident_id": "INC-…", "status": "OPEN", …}` |
| `GET /incident/v1/incidents?node_id=&status=OPEN` | `incident:read` | `{"incidents": […]}` |
| `GET /incident/v1/incidents/{incident_id}` | `incident:read` | Incident with `"complaint_refs": […]` |
| `GET /incident/v1/incidents/by-key/{idempotency_key}` | `incident:read` | The incident or `404` |
| `PATCH /incident/v1/incidents/{incident_id}` | `incident:write` | Body with any of `{"kind", "outage_id", "status", "summary"}`; `status` is `OPEN` or `RESOLVED`. Returns the updated incident. Naturally idempotent (no `Idempotency-Key`). `404 INCIDENT_NOT_FOUND`; `422` for an unknown enum value or an empty body |
| `POST /incident/v1/incidents/{incident_id}/complaints` | `incident:write` | Idempotent link. Body `{"complaint_ref", "contact_id", "customer_id"}` (`customer_id` nullable). `201`. Linking the same reference twice with a new key → `200`, no duplicate |
| `POST /incident/v1/complaints` | `incident:write` | Idempotent registration of a complaint reference. Body `{"complaint_ref", "received_at", "channel", "category", "customer_id"}` (`customer_id` nullable) → `201` `{"complaint_ref", "registered_at"}`. A different body for an already registered reference → `409 COMPLAINT_REF_CONFLICT` |
| `GET /incident/v1/complaints/{complaint_ref}` | `incident:read` | The registration or `404` |
| `GET /incident/v1/complaints/by-key/{idempotency_key}` | `incident:read` | The registration or `404` |
| `GET /incident/v1/incidents/{incident_id}/complaints/by-key/{idempotency_key}` | `incident:read` | The link or `404` |

The mock deliberately does **not** deduplicate incidents by node: two creates with different keys make two
incidents. Preventing that is the care platform's job, and the tests count on being able to observe a failure.

## Notification gateway (simulated SMS / push)

| Endpoint | Scope | Behaviour |
|---|---|---|
| `POST /notify/v1/messages` | `notify:write` | Idempotent. Body `{"customer_id", "channel", "template_id", "body", "outage_id"}`; `channel` is `sms`, `push` or `preferred` (the gateway uses the channel on the customer's consent record; `409 NO_PREFERRED_CHANNEL` when there is none); `outage_id` nullable. `202` `{"message_id": "MSG-…", "status": "ACCEPTED", "created_at"}`. Nothing is sent anywhere. The gateway does **not** check consent: that is the caller's job, at send time |
| `GET /notify/v1/messages?customer_id=&outage_id=&limit=50` | `notify:write` | `{"messages": […]}` newest first; at least one filter required |
| `GET /notify/v1/messages/by-key/{idempotency_key}` | `notify:write` | The message or `404`. The care platform's executor reads it to settle a send whose answer was lost |

## Simulator controls — scope `admin`

Not part of any client system. Used by tests, drills, evaluations and the demo console.

| Endpoint | Behaviour |
|---|---|
| `POST /admin/v1/outages` | Start an outage now. Body `{"node_id", "cause", "planned": false, "eta_minutes": 180, "detect_after_seconds": 0}`. An AGG node affects all its active children. Service drops immediately and, unless `planned`, a critical `ALARM_RAISED` event is emitted on the root node at once (`POWER` for `POWER_FAILURE`, otherwise `LINK_DOWN`) and shows in `active_alarms`. `OUTAGE_STARTED` is emitted and the status endpoint changes only after `detect_after_seconds`. → `201` `{"outage_id", "alarm_id", "affected_node_ids": […]}` (`alarm_id` null for planned work) |
| `POST /admin/v1/outages/{outage_id}/update` | Body `{"eta_minutes": 60, "eta_source": "field_update"}` → emits `OUTAGE_UPDATED` with `version + 1`. An outage not yet detected is detected first (emitting `OUTAGE_STARTED`); updating a resolved one → `409 OUTAGE_RESOLVED` |
| `POST /admin/v1/outages` (backdating) | Optional `"started_minutes_ago"` (0 to 86,400) makes the outage begin in the past, so that scenarios which depend on its duration (service credits) need no waiting. Its alarm and `OUTAGE_STARTED` events carry the past time, so a feed consumer sees an event that is already old |
| `POST /admin/v1/outages/{outage_id}/resolve` | Emits `OUTAGE_RESOLVED` and `ALARM_CLEARED` for the outage's alarm; idempotent. An outage not yet detected is detected first (emitting `OUTAGE_STARTED`) |
| `POST /admin/v1/alarms` | Raise an alarm that is not (yet) an outage. Body `{"node_id", "alarm_type", "severity"}` → emits `ALARM_RAISED`; `201` `{"alarm_id"}`. `POST /admin/v1/alarms/{alarm_id}/clear` emits `ALARM_CLEARED`; idempotent |
| `GET /admin/v1/outages?active=true` | Current simulated outages, including undetected ones |
| `POST /admin/v1/line-faults` | Body `{"service_id", "fault": true, "result": "FAIL"}`; `result` may be `FAIL` or `INCONCLUSIVE`; `fault: false` clears |
| `POST /admin/v1/consent` | Body `{"customer_id", "outage_notifications": false, "channel": "sms"}`. `channel` (`sms` or `push`) is optional, default `sms`; opting out sets it to `none`, so a `preferred` send then gets `409` |
| `POST /admin/v1/chaos` | Body `{"target", "mode", "latency_ms", "error_rate", "hold_seconds", "ttl_seconds"}` — see below. `GET /admin/v1/chaos` lists active rules; `DELETE /admin/v1/chaos` clears all |
| `GET /admin/v1/customers/search` | Fixture finder using ground truth. Filters: `node_id`, `service_type`, `router_model`, `segment`, `consent` (`true`/`false`), `mapping_defect`, `status`, `min_monthly_fee`, `limit` (≤ 50). Returns `{"customers": [{"customer_id", "service_id", "true_node_id", "mapped_node_ids": […], "router_model", "monthly_fee_inr", "consent", "mapping_defect", "phone"}]}` |
| `GET /admin/v1/state` | Counters for assertions: rows per write table (`credits`, `appointments`, `incidents`, `incident_complaints`, `complaints`, `messages`) and request counts per API and outcome |
| `POST /admin/v1/reset` | Clears runtime state (outages, alarms, faults injected at runtime, writes, chaos, request counters, consent set through `/admin/v1/consent`, and all idempotency keys). Reference data stays. Declared outages and active alarms are closed on the feed first (`OUTAGE_RESOLVED`, `ALARM_CLEARED`) and the feed's history and sequence are kept, so a feed consumer stays consistent across a reset. An outage the NOC had not detected yet is forgotten without an event |

### Fault injection

`target` is one of `network` (status and line test), `network_feed` (events), `crm`, `billing`, `field`,
`incident`, `notify`. `ttl_seconds` is optional; without it the rule stays until cleared.

| `mode` | Effect on matching requests |
|---|---|
| `ok` | Removes the rule for that target |
| `down` | `503 UPSTREAM_UNAVAILABLE` immediately |
| `timeout` | Holds the connection for `hold_seconds` (default 30), then `504` |
| `latency` | Adds `latency_ms` before normal handling |
| `error_rate` | `500 INTERNAL_ERROR` with probability `error_rate` (0–1) |
| `response_loss` | Write endpoints only: the write is committed, then the response is replaced by `504 GATEWAY_TIMEOUT`. Read endpoints behave as `timeout` |

`/admin` and `/health` are never affected by fault injection.

## `GET /health`

No auth. `{"status": "ok", "seeded": true, "customers": 400000, "last_event_seq": 4813}`.
