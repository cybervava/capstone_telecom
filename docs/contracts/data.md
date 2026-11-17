# Data contract — synthetic data pack and knowledge pack

**Status: stand-in.** The brief says SkyLink supplies these datasets. They have not been received, so
`skylink_datagen` produces a seeded equivalent at the scale the brief states. When the supplied pack
arrives, only the loaders that read these files change (`skylink_care/pipelines`, `mock_oss/seed.py`).

Rules for every file:

- UTF-8, `\n` line endings, CSV with a header row, RFC 4180 quoting.
- Timestamps are ISO 8601 with offset, in `+05:30` (IST). Dates are `YYYY-MM-DD`.
- Booleans are `true` / `false`. Empty string means "no value".
- The same `--seed` and `--customers` produce byte-identical files. No wall-clock, no unordered
  iteration, no library whose output changes between versions (curated name lists, not Faker).
- All people, phone numbers, emails and addresses are synthetic. Emails use `example.com`.

Default output directory: `data/generated/` (ignored by git). History window: `2026-05-01T00:00:00+05:30`
to `2026-10-31T23:59:59+05:30` (six months).

## 1. Reference data

### `nodes.csv`

| Column | Type | Notes |
|---|---|---|
| `node_id` | str | `{CITY}-{TYPE}-{NNNN}`, e.g. `BLR-OLT-0012`, `BLR-TWR-0007`, `BLR-AGG-0003` |
| `node_type` | enum | `AGG` (aggregation), `OLT` (broadband access), `TOWER` (mobile access) |
| `service_type` | enum | `broadband` for OLT, `mobile` for TOWER, empty for AGG |
| `parent_node_id` | str | The AGG node for OLT/TOWER; empty for AGG |
| `city`, `area`, `pincode` | str | 8 cities: BLR, MUM, DEL, HYD, CHN, PUN, KOL, AMD |
| `status` | enum | `ACTIVE` or `DECOMMISSIONED` (about 1% of access nodes) |

Sizing at 400,000 customers: about 250 broadband services per OLT, about 1,500 mobile services per TOWER,
about 12 access nodes per AGG. Scale node counts linearly with `--customers`, minimum 2 AGG per city.

### `addresses.csv`

`address_id` (`A0000001`), `line1`, `area`, `city`, `pincode`. Area and pincode agree with the node the
address is truly served by.

### `customers.csv`

| Column | Type | Notes |
|---|---|---|
| `customer_id` | str | `C0000001` |
| `full_name` | str | From curated first/last name lists |
| `phone` | str | `+91` + 10 digits, first digit 6–9, **unique** |
| `email` | str | `first.last<n>@example.com`, unique |
| `segment` | enum | `consumer` (92%), `sme` (8%) |
| `status` | enum | `active` (97%), `suspended` (3%) |
| `created_on` | date | |
| `consent_outage_notifications` | bool | `true` for about 78% |
| `consent_channel` | enum | `sms`, `push`; `none` when consent is `false` |
| `consent_updated_at` | timestamp | |

### `services.csv`

| Column | Type | Notes |
|---|---|---|
| `service_id` | str | `S0000001` |
| `customer_id` | str | FK |
| `service_type` | enum | `broadband`, `mobile` |
| `plan_name` | str | See plan table |
| `monthly_fee_inr` | decimal(2) | e.g. `799.00` |
| `status` | enum | `active`, `suspended` |
| `address_id` | str | FK; installation address (broadband) or home address (mobile) |
| `router_model` | str | One of the 12 `model_id`s in `router_models.json`; empty for mobile |
| `router_serial` | str | Empty for mobile |
| `activated_on` | date | |
| `billing_cycle_day` | int | 1–28 |

Mix: 45% of customers have broadband, 82% have mobile, at least one service each (so about 27% have both).
Plans — consumer broadband: Fibre 100 `599.00`, Fibre 300 `999.00`, Fibre 500 `1499.00`, Fibre 1G `3999.00`;
SME broadband: Biz Fibre 500 `4999.00`, Biz Fibre 1G `9999.00`, Biz Leased 1G `14999.00`;
mobile: Prepaid 28 `299.00`, Postpaid 499 `499.00`, Postpaid 999 `999.00`. SME customers take SME broadband plans.

### `address_node_map.csv` — the operational mapping, with defects

This is what the care platform is allowed to read. It is deliberately imperfect.

| Column | Type | Notes |
|---|---|---|
| `address_id` | str | FK |
| `service_type` | enum | `broadband`, `mobile` |
| `node_id` | str | Access node (OLT or TOWER) |
| `source` | enum | `inventory`, `gis`, `manual` |
| `valid_from` | date | |
| `valid_to` | date | Empty = currently valid |
| `map_version` | str | `2026-11-01` |

Defects, applied per (address, service type) and recorded in the truth file:

| Defect | Default rate | Shape |
|---|---|---|
| `missing` | 1.5% | No row at all |
| `ambiguous` | 1.0% | Two currently valid rows with different `node_id`s (one is the true node) |
| `stale` | 0.7% | One currently valid row pointing at a `DECOMMISSIONED` node; the true node is a different, active node |

Rates are CLI options (each at most 0.25). A correct mapping may also carry closed historical rows
(`valid_to` set). `--customers` accepts 1,000 to 5,000,000.

### `truth_service_node.csv` — simulator ground truth

`service_id`, `true_node_id`, `mapping_defect` (`none`, `missing`, `ambiguous`, `stale`).

Read only by `mock_oss` (to decide what a line test or an outage really affects), by `evals/`
(to score) and by `loadtest/` (to pick callers on the failed nodes). **`skylink_care` must never read
this file.**

## 2. Network history

### `outages.csv`

| Column | Type | Notes |
|---|---|---|
| `outage_id` | str | `OUT-YYYYMMDD-NNNN` |
| `root_node_id` | str | AGG (fibre cut, power) or an access node (equipment, tower fault) |
| `affected_node_ids` | str | Pipe-separated access nodes; all children when the root is an AGG |
| `service_types` | str | Pipe-separated |
| `cause` | enum | `FIBRE_CUT`, `POWER_FAILURE`, `EQUIPMENT_FAULT`, `TOWER_FAULT`, `PLANNED_MAINTENANCE` |
| `planned` | bool | `true` only for `PLANNED_MAINTENANCE` (about 10% of outages) |
| `started_at` | timestamp | When service actually dropped |
| `detected_at` | timestamp | When the NOC registered it: 1–10 minutes after `started_at`; equal for planned work |
| `initial_eta` | timestamp | First restoration estimate |
| `resolved_at` | timestamp | |
| `duration_minutes` | int | `resolved_at - started_at` |

About 10 outages per day at full scale. Durations are log-normal with a median near 120 minutes and a
tail beyond 48 hours, clamped to between 15 minutes and 6 days; planned work lasts 60–240 minutes and
happens between 01:00 and 05:00, rooted at an AGG node 30% of the time, an OLT 50%, a tower 20%. At
least 40 outages are generated whatever the size.

### `network_events.jsonl`

One JSON object per line, ordered by `seq` (1-based, gap-free), which follows `emitted_at`.

Common fields: `seq`, `event_id` (`evt_` + 16 hex, unique), `type`, `emitted_at`, `node_ids` (list).

- Outage lifecycle — `OUTAGE_STARTED` (emitted at `detected_at`), `OUTAGE_UPDATED` (0–3 per outage, changes
  `eta`), `OUTAGE_RESOLVED`. Extra fields: `outage_id`, `version` (1, 2, … per outage, strictly increasing),
  `service_types`, `cause`, `planned`, `started_at`, `eta`, `eta_source` (`noc_estimate` or `field_update`),
  `resolved_at` (null until resolved).
- Alarms — `ALARM_RAISED`, `ALARM_CLEARED`. Extra fields: `alarm_id`, `alarm_type` (`LOS`, `LINK_DOWN`,
  `HIGH_BER`, `POWER`, `HIGH_TEMP`), `severity` (`critical`, `major`, `minor`). Every outage is preceded by at
  least one critical alarm on its root node, and 30% of outages add a `major` alarm on an affected node;
about three times as many alarms never become outages (these are critical, major or minor 15/40/45% of
the time, and clear within 90 minutes).

## 3. Contact history

### `transcripts.jsonl`

20,000 records at full scale (scale linearly, minimum 500).

| Field | Type | Notes |
|---|---|---|
| `contact_id` | str | `H0000001` |
| `ts` | timestamp | |
| `channel` | enum | `chat` (40%), `call` (60%) |
| `customer_id`, `service_id` | str | |
| `label` | enum | `outage` (45%), `device` (35%), `billing` (20%) — the true cause |
| `outage_id` | str / null | Set when `label` is `outage` |
| `first_customer_message` | str | The opening utterance; used as agent input in replays |
| `transcript` | str | 4–10 turns, lines prefixed `Customer:` / `Agent:` |
| `legacy_resolution` | enum | `reboot_script`, `technician_visit`, `resolved_on_call`, `billing_adjustment`, `escalated` |
| `technician_visit` | bool | |
| `visit_necessary` | bool / null | Null when no visit |
| `repeat_within_7d` | bool | A later contact by the same customer within 7 days |

Constraints the generator must satisfy and its tests must assert:

- `label = outage` ⇒ `ts` lies inside `[started_at, resolved_at]` of an outage whose `affected_node_ids`
  contains the service's **true** node. About 20% of these fall before `detected_at` (the NOC does not know yet).
- `label = outage` ⇒ `technician_visit` is `true` for 30% (±1 point at full scale) and all of those have
  `visit_necessary = false`. This encodes the brief's "30% of these calls end in an unnecessary technician visit".
- `label = device` ⇒ the true node has no outage at `ts`; `technician_visit` is `true` for about 25%, of
  which about 70% are necessary.
- `label = billing` ⇒ no visit. Visits fall only on broadband services, and `legacy_resolution` is
  `technician_visit` exactly when a visit happened.
- Mobile services make up 20% of outage contacts, 15% of device and 45% of billing contacts.
- Outage and device openings overlap heavily in wording ("internet not working", "red light on the router"):
  text alone must not separate them. Billing is distinguishable.
- About 30% of transcripts state an identifier in the text (phone, customer id or email) so masking can be tested.
- `repeat_within_7d` is `true` for about 25% of outage contacts, 12% of device, 6% of billing.

## 4. `manifest.json`

`generator_version`, `seed`, `customers`, per-file row counts and SHA-256, defect rates, history window.

## 5. Knowledge pack (committed under `data/knowledge/`)

Generated by `skylink-datagen knowledge` and committed, so retrieval ground truth is stable.

```
data/knowledge/
├── router_models.json                  the 12 models and their checkable facts
├── router_guides/<model_id>.md         12 files
├── policies/service_credit_policy.md   written by hand, mirrors data/policies/credit_policy.v0.yaml
├── regulatory/complaint_handling_rules.md   written by hand
└── quarantine/untrusted_forum_post.md  deliberately hostile document for guardrail tests
```

Every Markdown document starts with YAML front matter:

```yaml
---
doc_id: guide-al-ax18            # stable, unique
doc_type: device_guide           # device_guide | credit_policy | complaint_rules | untrusted
title: Aerolink AX1800 troubleshooting guide
device_model: AL-AX18            # device guides only
vendor: Aerolink
version: "1.2"
effective_from: 2026-04-01
access_roles: [customer, agent, supervisor]
---
```

Sections are level-2 headings with a stable id: `## Power cycle {#power-cycle}`. Every router guide has
exactly these section ids, in this order: `overview`, `leds`, `power-cycle`, `no-internet`, `wifi`,
`slow-speed`, `factory-reset`, `escalation`. Steps inside a section are a numbered list.

`router_models.json` is a list of objects with `model_id`, `vendor`, `name`, `type` (`ont_router` or
`router`), and `facts`: `admin_url`, `default_ssid_pattern`, `power_led`, `internet_led_name`,
`internet_led_ok`, `internet_led_fault`, `los_led_name` (null if none), `reset_hold_seconds`,
`reboot_wait_seconds`, `wps_button`, `firmware`. Any two models must differ on at least five facts, and each
guide must state every one of its facts in the matching section (so a wrong-model answer is detectable).

Vendors and models are fictional. No real trademarks.
