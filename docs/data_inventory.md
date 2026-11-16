# Data inventory

What the care platform holds, why, where it comes from and for how long. It answers the
first two lines of the brief's checklist 3: data listed with purpose, source and retention
period; personal data processed by purpose limitation, minimisation and consent.

Everything in this build is synthetic (assumption A1). The inventory describes what the
platform would hold of real customers, because the tables and the flows are the same.

## What comes in, and from where

| Source | What | How it arrives |
|---|---|---|
| The customer, through a channel | The text of their messages; a session id (guest), or the customer id the channel signed them in as, or the caller's line from telephony | `POST /v1/contacts`, `POST /v1/contacts/{id}/messages` |
| CRM (through the gateway) | The customer's profile: name, phone, email, segment, status, consent, services with plan, fee, router model and address (area, city, pincode) | Read by the Triage Agent with its own key, once the customer is identified. **The name, the phone number and the email address are not stored**: they are used within the turn, to mask the message before a model reads it. The workflow's state keeps the customer id and the service the conversation is about (its id, type, address id, pincode, router model and monthly fee) |
| Network Monitoring | Node status, outages, line-test results | Read by the Diagnostics Agent; the event feed fills the network tables |
| Billing, Field Service | Credits on the account, visit slots and bookings | Read by the Resolution and Dispatch Agents; written by the executor after approval or the customer's choice |
| Staff | Decisions on credits, notes, reasons for a takeover | The staff routes of the API |
| The knowledge pack | Router guides | Indexed at build time; no personal data |

## What is stored

Personal data is marked **P**. "Kept" is the retention period this build proposes; what
enforces it is in the last column.

| Table | Holds | P | Purpose | Kept | Enforced by |
|---|---|---|---|---|---|
| `contacts` | The complaint: reference, channel, status, timestamps; the session id, the signed-in customer id or the caller's line; once verified, the customer id, service id and node | P | The complaint record the regulator's rule asks for; routing | 24 months after closure (placeholder rule, `complaint_handling_rules.md` §records) | Nothing yet |
| `contact_messages` | Every message of the conversation, both ways, with the template and facts of each outgoing one | P (free text: whatever the customer wrote) | What was said to whom; the search query for the right guide section | With its contact | `ON DELETE CASCADE` from `contacts`; no purge job |
| `audit_log` | Who or what did each step, with inputs, outputs, model, prompt and policy version, duration | P (customer and service ids; contact details and secrets are redacted before writing) | The audit trail (checklist 3); evidence for a disputed decision | 24 months (same rule) | Append-only by trigger: no update, delete or truncate. No purge job; a purge needs a migration that says so |
| `actions` | Each side effect proposed or done (register the complaint, open an incident, notify, apply a credit, book a visit) with its request and outcome | P (customer and service ids, amounts) | Exactly-once writes to the enterprise systems; what was done for whom | With its contact | Nothing yet |
| `approvals` | The frozen credit request shown to the approver, its hash, who decided, when, and their note | P (customer id, amount; a note is a person's free text) | Human approval on record | With its action | Nothing yet |
| `handoffs` | Why a person has the conversation, a summary built from structured facts, who closed it and their note | P | The staff queue; human override on record | With its contact | Nothing yet |
| `identity_attempts` | The customer ID that was claimed, the conversation, the time, whether the CRM knew the ID | P (a claimed ID) | Limiting guesses at the identity check | 24 hours | Swept by the code that counts (`db/identity.py`) |
| `ops_items` | Troubles that belong to no conversation; for a locked identity check, the customer ID and the conversations | P (in that one kind) | The operations queue | Until closed, then 24 months | Nothing yet |
| `line_tests` | Service id, node, result, time of each remote line test | P (service id) | Evidence for a visit; telling a line fault from a node fault | 30 days: only recent tests are evidence | Nothing yet |
| `inbox`, `timers` | Events and wake-ups of a conversation | P (by reference) | Driving the workflow | Until applied, then with the contact | Nothing yet |
| Workflow checkpoints | The state of a conversation in progress: the customer id and how they were verified, the service (id, type, address id, pincode, router model, monthly fee), the network decision, the model's one-sentence reading | P | Resuming a conversation after a restart | Until the conversation ends | Deleted by the worker when the thread finishes (`orchestrator/worker.py`) |
| `service_index`, `address_node_map`, `nodes` | Which service is at which address, which address on which node (reference data from SkyLink) | P (customer id to service to address id; no names) | Finding the node that serves a customer | Replaced at each load | The loader |
| `outages`, `outage_nodes`, `node_alarms`, `node_episodes`, `feed_state` | The network as the NOC reports it | No | Evidence for every claim about the network | 24 months with the complaints they explain | Nothing yet |
| `guide_chunks`, `guide_index` | The router guides, by section | No | Quoted troubleshooting steps | Replaced at each index build | The index build |

**What "nothing yet" means.** The retention periods are proposals on a placeholder rule. No
job deletes or anonymises old complaints in this build. In the target deployment that is a
scheduled job with its own audit entry, and the periods are SkyLink's to set. It is a gap, and
it is listed in the evidence index as one.

## What leaves the platform

| To | What | Under which rule |
|---|---|---|
| The language-model provider (when one is configured) | One customer message per call, masked: phone numbers, email addresses, ids and long numbers by their shape, and by value everything the CRM profile holds of this customer | Only for an identified customer (without a record there is nothing to mask a name by: an unidentified visitor's text is read by the keyword rules inside the platform). Recorded with provider, model and prompt version. Tests: `tests/unit/llm/test_model_payloads.py`; attack suite G1, G2 |
| SkyLink's enterprise systems, through the gateway | Complaint and incident registrations, notices, credits, bookings | Each write by the executor only, with an idempotency key; a notice only where the CRM's consent record allows it at the moment of sending; a credit only after a person's approval of that very request |
| Logs | Structured events with ids; contact details and secrets redacted; the HTTP client's request lines off | `observability/logging.py`; `tests/unit/test_logging_redaction.py` |

Nothing else leaves. The platform sends no data to analytics, no data to a vector database
outside PostgreSQL, and the embedder for the guide index is a local hashing function with no
model and no network.

## The privacy principles, one by one

| Principle | How it is met here | Where it is not |
|---|---|---|
| Purpose limitation | Customer data is read to handle that customer's complaint, by the agent whose job needs it: only Triage reads a profile; the executor can read consent and nothing else of the CRM (gateway scopes, M4 slice E) | Messages are free text. A customer can write anything into one, and it is stored as written |
| Minimisation | The profile is not stored. The model sees masked text and nine fields come back. Summaries for staff are built from structured facts plus one cleaned sentence. The customer's own words are not copied into the audit trail | The complaint record keeps the whole conversation, as a complaint record must |
| Consent | Proactive outage notices go only to customers whose CRM consent record allows them, checked again at the moment of sending; a suppressed notice is recorded as skipped (`executor/worker.py`, attack-suite and journey tests) | Handling a complaint the customer raised is not consent-based. The legal basis for that, and the notice to customers that an AI assistant handles it, are SkyLink's to state |
| Accuracy | Nothing is said about the network without evidence of that moment; identity is verified before anything of an account is used; a contact is bound to the customer it was verified as | The identity check for a customer who is not signed in is weak by design (decision D9) |
| Storage limitation | Identity attempts: a day. Checkpoints: the life of the conversation | Everything else: periods proposed, not enforced (above) |
| Security | Roles on every route, object-level ownership, a key for each agent identity, redaction in logs and trail, append-only audit log | TLS, encryption at rest and a managed secret store belong to the deployment this build describes and does not have |
| Transparency | Every message from the assistant says it is one (`AI_DISCLOSURE` in `agents/replies.py`) | |
| Access, correction, erasure | Not built. A request reaches a person through the handoff queue | No export or erasure job |

No protected attribute decides anything. The profile carries one, the customer's segment; it
is read nowhere (`tests/unit/test_decision_inputs.py`), and consumer and SME customers put
through the same situations end the same way (`evals/reports/segment_parity.md`).
