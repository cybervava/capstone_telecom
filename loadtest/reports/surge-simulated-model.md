# Surge: a tenfold rise in contacts, with and without the Network Monitoring API

**What this proves.** That the platform stays available while contacts rise tenfold during a live outage: every caller gets a complaint reference at once, nobody's first contact is turned away, and the replies keep coming. That this holds with the Network Monitoring API down, without anyone being told that the network is fine. And that when the outage ends, everyone who waited on it hears of it.

**Target.** Contacts offered at 1 a second rising to 10 within 1 minute and held for 1 minute, once with every dependency up and once with the Network Monitoring API down. In both runs: at least 99.5% of the offered contacts accepted, every complaint reference within 120 s, no first contact turned away by a rate limit, every contact answered. When the outage ends, every customer who waited on it is told within 30 minutes.

**Result (met).** Every dependency up: 100.00% (893 of 893) accepted, slowest reference 0.11 s, every target met.

Written by the run of 2026-11-16T14:30:49+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: as recorded for each message read: simulated-1.5s 511 · rules-v1 (without the model: load_shed) 382. No model was called: `simulated` is the rules' answer after the time a model call takes.

## Targets

| Target | Wanted | Every dependency up |
|---|---|---|
| Offered contacts accepted | at least 99.5% | 100.00% (893 of 893) (met) |
| Slowest complaint reference | within 120 s | 0.11 s (met) |
| First contacts turned away by a rate limit | none | 0 (met) |
| Contacts answered when the surge was over | all | 893 of 893 (met) |
| Waiting customers told that the outage is over | all, within 30 minutes of its end | 625 of 625 (met) |

## What the callers saw

|  | Every dependency up |
|---|---|
| Contacts offered | 893 |
| of them on the nodes of the outage | 625 |
| by the web / through a contact centre | 614 / 279 |
| Accepted | 893 |
| Left without a reference after 120 s | 0 |
| Turned away by a rate limit | 0 |
| Reference in hand, from when the contact was due: median / 95th / 99th / slowest | 0.015 / 0.02 / 0.026 / 0.105 s |
| Sent more than once | 0 |
| Told that the reply will take longer | 448 |
| Web callers who stayed for the reply / saw it before leaving | 614 / 614 |
| Reply seen on the page, from when the contact was due: median / 95th / 99th / slowest | 13.468 / 18.498 / 20.768 / 21.921 s |
| Not accepted because | — |

## What the platform recorded

|  | Every dependency up |
|---|---|
| Contacts received | 893 |
| Answered / not answered when the run was read | 893 / 0 |
| First reply after receipt, all contacts: median / 95th / 99th / slowest | 11.659 / 15.688 / 16.85 / 18.033 s |
| Reference issued, inside the API: median / 99th / slowest | 3 / 9 / 39 ms |
| The first reply was | outage_active.v1 625 · guided_steps.v1 203 · handed_to_human.v1 45 · out_of_scope.v1 17 · line_fault_hold.v1 3 |
| Contacts stood, when read, as | AWAITING_NETWORK 628 · AWAITING_CUSTOMER 220 · HANDOFF 45 |
| Handed to a person for | BILLING 30 · LOW_CONFIDENCE 15 |
| Messages were read by | simulated-1.5s 511 · rules-v1 (without the model: load_shed) 382 |
| Messages saying the network is clear | 206 |
| Contacts whose runs were given up on | 0 |
| Writes for these contacts | COMPLAINT_REGISTER SUCCEEDED 893 · INCIDENT_LINK_COMPLAINT SUCCEEDED 625 |
| Notices about these outages to customers | SUCCEEDED 3,454 · SKIPPED 949 |
| Items raised for operations staff | — |

## The backlog as staff saw it

|  | Every dependency up |
|---|---|
| Readings taken / missed | 33 / 0 |
| Latest the generator offered a contact after it was due (inside every time above) | 0.072 s |
| Most contacts waiting for a turn at once | 158 |
| Longest wait for a turn seen | 16 s |
| Time spent behind (surge mode on) | 10 s |
| Keeping up again after the last caller left | after 2 s |

## After the surge: the outage ends

|  | Every dependency up |
|---|---|
| Contacts waiting to hear that the outage is over | 625 |
| Told that it is over | 625 |
| All of them told after | 78 s |
| Those not told stood at | — |
| Told meanwhile that the status cannot be verified | 0 |
| Their lines tested / found failing | 625 / 3 |
| Tickets raised for a suspected fault on the restored nodes | 0 |
| Most contacts waiting for a turn meanwhile | 622 |
| Longest wait for a turn meanwhile | 71 s |

## Where the time of a turn goes (Every dependency up)

| Step | Times | Median ms | 95th ms | Slowest ms |
|---|---|---|---|---|
| triage.read_contact | 893 | 1500 | 1501 | 1504 |
| tool:network.line_test | 206 | 887 | 1475 | 1511 |
| tool:network.node_status | 833 | 7 | 11 | 35 |
| tool:crm.customer | 893 | 5 | 9 | 27 |
| tool:crm.lookup | 279 | 5 | 9 | 20 |

## What this rests on

- Run on one Linux machine with 16 CPU threads, which also ran the load generator, the simulator and PostgreSQL: the figures say that the design holds at this rate, not what a production deployment can carry.
- The stack was the local processes of `make dev`: one process of each kind, the API with a pool of 8 database connections, the orchestrator running 8 contacts at a time.
- Data: the synthetic pack of 400,000 customers. The OSS/BSS gateway is the simulator, which answers in a few milliseconds; real systems will be slower, and the time of a turn is mostly theirs.
- Contacts were offered on a schedule fixed in advance (arrivals at random at the profile's rate, seed 42), each by a different customer. Every time is measured from the instant the contact was due, so the generator's own lag is inside it.
- About seven callers in ten were customers on the failed nodes and the rest were elsewhere with an ordinary mix of problems. About seven in ten came by the web with their own sign-in and stayed on the page for the reply, asking as the page does; the others were relayed by a contact centre's integration, which states the caller's line. The first table has the counts.
- A request that failed was sent again with the same request id until two minutes after the contact was due. A contact whose reference was not in the caller's hands by then counts as not accepted, also when the API issued one later.
- With the Network Monitoring API down (down in the simulator) nothing else was made to fail. That the event feed went on working is seen in the first replies: an outage can be told to a customer only on proof, and with the status unreadable the feed is the only proof there is.
- Not covered: several replicas of a process, a real language model, real enterprise systems, a network between the parts.
