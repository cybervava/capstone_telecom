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
| CRM (through the gateway) | The customer's profile: name, phone, email, segment, status, consent, services with plan, fee, router model and address (area, city, pincode) | Read by the Triage Agent with its own key, once the customer is identified. **The name, the phone number and the email address are not stored**: they are used within the turn, to mask the message before a model reads it. The workflow's state keeps the customer id and the service the conversation is about (its id, type, address id, pincode, router model and monthly fee); while a visitor is proving who they are, it also keeps the customer ID they claimed and, until the next turn, the four digits they gave |
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
| `contact_messages` | Every message of the conversation, both ways, with the template and facts of each outgoing one | P (free text: whatever the customer wrote) | What was said to whom; the search query for the right guide section | With its contact | `ON DELETE CASCADE` from `contacts`, but no contact can be deleted today (its actions and handoffs refer to it without a cascade); no purge job |
| `audit_log` | Who or what did each step, with inputs, outputs, model, prompt and policy version, duration. Also the calls an MCP client makes and the documents it reads (which one, the arguments but for the words of a problem description, the outcome), under no contact | P (customer and service ids; contact details and secrets are redacted before writing) | The audit trail (checklist 3); evidence for a disputed decision | 24 months (same rule) | Append-only by trigger: no update, delete or truncate. The one role every process uses owns the table and could switch the trigger off; separate roles are not built. No purge job; a purge needs a migration that says so |
| `actions` | Each side effect proposed or done (register the complaint, open an incident, notify, apply a credit, book a visit) with its request and outcome | P (customer and service ids, amounts) | Exactly-once writes to the enterprise systems; what was done for whom | With its contact | Nothing yet: it refers to its contact without a cascade, so it would block the contact's deletion |
| `approvals` | The frozen credit request shown to the approver, its hash, who decided, when, and their note | P (customer id, amount; a note is a person's free text) | Human approval on record | With its action | Nothing yet |
| `handoffs` | Why a person has the conversation, a summary built from structured facts, who closed it and their note | P | The staff queue; human override on record | With its contact | Nothing yet: it refers to its contact without a cascade. A contact handed to a person is never marked closed by the platform |
| `identity_attempts` | The customer ID that was claimed, the conversation, the time, whether the CRM knew the ID | P (a claimed ID) | Limiting guesses at the identity check | 24 hours | Rows older than a day are swept, up to 200 at a time, whenever a later try is taken (`db/identity.py`); nothing sweeps them otherwise |
| `ops_items` | Troubles that belong to no conversation; for a locked identity check, the customer ID and the conversations | P (in that one kind) | The operations queue | Until closed, then 24 months | Nothing yet |
| `line_tests` | Service id, node, result, time of each remote line test | P (service id) | Evidence for a visit; telling a line fault from a node fault | 30 days: only recent tests are evidence | Nothing yet |
| `inbox`, `timers` | Events and wake-ups of a conversation | P (by reference) | Driving the workflow | Until applied, then with the contact | Nothing yet |
| Workflow checkpoints | The state of a conversation in progress: the customer id and how they were verified, the service (id, type, address id, pincode, router model, monthly fee), the network decision, the model's one-sentence reading | P | Resuming a conversation after a restart | Until the workflow finishes | Deleted by the worker when the workflow finishes; after a handover or a failure handed to a person, when the next event for the contact arrives (`orchestrator/worker.py`) |
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
| The language-model provider, Anthropic or OpenAI (when one is configured) | The customer's messages since the last reading, masked: phone numbers, email addresses, ids and long numbers by their shape, and by value the personal details the CRM profile holds (the name and its parts, the registered mobile and its last digits, the name part of the email address, the numbers of the ids, the area, city and pincode of each service address) | Only for an identified customer (without a record there is nothing to mask a name by: an unidentified visitor's text is read by the keyword rules inside the platform). Each reading is recorded in the audit trail with its model and prompt version. To OpenAI the request is sent with `store: false` and with nothing that tells customers apart beside the masked text. How long each provider keeps a request is its own retention policy, not set here. Tests: `tests/unit/llm/test_model_payloads.py` (both providers' requests); attack suite G1, G2 |
| The same provider, with the question about a router guide (at most once in a conversation, and only when a model is configured) | Up to the last 2,000 characters of what the customer wrote in the conversation, masked the same way, and the sections of the guide for their router that the search found (SkyLink's own text, no customer data) | Asked only when the customer's profile could be read at that moment, so that their words can be masked by value; otherwise the search's choice stands and nothing is sent. Recorded in the audit trail as `troubleshooting.answer` with the model and the prompt version; the customer's words and the model's are not copied there. Test: `tests/unit/llm/test_model_payloads.py`, both questions |
| An MCP client: another assistant, with the key (`care-mcp`) | What it asks for: the state of a network node with its outage, the steps of a router guide, a credit worked out from figures the client gives, the health of the feed, the queue and the index. Nothing of a customer | Read-only tools, typed and bounded; a key on every request over HTTP; each call in the audit trail as `mcp.tool:<name>`. What the client's own model and provider do with an answer is outside the platform |
| LangSmith (only with `LANGSMITH_TRACING=true`) | A trace of each turn of the workflow: the name of each step, its timing and any error, and metadata (the contact's id as the thread id, the step, the git revision). No state, message or profile: inputs and outputs are hidden unless `LANGSMITH_HIDE_INPUTS` and `LANGSMITH_HIDE_OUTPUTS` are set to `false` on purpose | Only the orchestrator is given the settings. Building the graph sets both HIDE settings to `true` when they are not set (`observability/tracing.py`; `tests/unit/test_tracing.py`). An error's text goes as it was raised. Checked by hand on the traces of the live attack-suite run (16 Nov): inputs empty, outputs empty |
| SkyLink's enterprise systems, through the gateway | Complaint and incident registrations, notices, credits, bookings | Each write by the executor only, with an idempotency key; a notice only where the CRM's consent record allows it at the moment of sending; a credit above $500 only after a supervisor's approval of that very request (at or under it, without review, unless `CARE_CREDIT_REVIEW_ALL=true`) |
| Logs | Structured events with ids; contact details and secrets redacted; the HTTP client's request lines off | `observability/logging.py`; `tests/unit/test_logging_redaction.py` |

Nothing else leaves. The platform sends no data to analytics, no data to a vector database
outside PostgreSQL, and the embedder for the guide index is a local hashing function with no
model and no network. If the two LangSmith HIDE settings are set to `false`, the traces carry
each step's inputs and outputs: the workflow's state (see "Workflow checkpoints" above) and
what the steps pass on, unmasked. They then belong only where that data may be kept.

## The privacy principles, one by one

| Principle | How it is met here | Where it is not |
|---|---|---|
| Purpose limitation | Customer data is read to handle that customer's complaint, by the agent whose job needs it: only Triage reads a profile; the executor can read consent and nothing else of the CRM (gateway scopes, M4 slice E) | Messages are free text. A customer can write anything into one, and it is stored as written |
| Minimisation | The profile is not stored. The model sees masked text, and nine fields or a chosen guide section come back. Summaries for staff are built from structured facts plus one cleaned sentence. The customer's own words are not copied into the audit trail | The complaint record keeps the whole conversation, as a complaint record must |
| Consent | Proactive outage notices go only to customers whose CRM consent record allows them, checked again at the moment of sending; a suppressed notice is recorded as skipped (`executor/worker.py`, attack-suite and journey tests) | Handling a complaint the customer raised is not consent-based. The legal basis for that, and the notice to customers that an AI assistant handles it, are SkyLink's to state |
| Accuracy | Nothing is said about the network without evidence of that moment; identity is verified before anything of an account is used; a contact is bound to the customer it was verified as | The identity check for a customer who is not signed in is weak by design (decision D9) |
| Storage limitation | Identity attempts: a day, swept when a later try is taken. Checkpoints: until the workflow finishes | Everything else: periods proposed, not enforced (above) |
| Security | Roles on every route, object-level ownership, a key for each agent identity, redaction in logs and trail, append-only audit log | TLS, encryption at rest and a managed secret store belong to the deployment this build describes and does not have |
| Transparency | Every message of a conversation says it comes from an automated assistant (`AI_DISCLOSURE` in `agents/replies.py`) | The proactive outage notices begin "SkyLink:" and do not say they are automated |
| Access, correction, erasure | Not built. A request for one is read as "nothing of ours" and declined politely, unless the customer also asks for a person | No export or erasure job, and no route for such a request |

No protected attribute decides anything. The profile carries one, the customer's segment; it
is read nowhere (`tests/unit/test_decision_inputs.py`), and consumer and SME customers put
through the same situations end the same way (`evals/reports/segment_parity.md`).
