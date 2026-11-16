# Surge: a tenfold rise in contacts, with and without the Network Monitoring API

**What this proves.** That the platform stays available while contacts rise tenfold during a live outage: every caller gets a complaint reference at once, nobody's first contact is turned away, and the replies keep coming. That this holds with the Network Monitoring API down, without anyone being told that the network is fine. And that when the outage ends, everyone who waited on it hears of it.

**Target.** Contacts offered at 1 a second rising to 10 within 1 minute and held for 1 minute, once with every dependency up and once with the Network Monitoring API down. In both runs: at least 99.5% of the offered contacts accepted, every complaint reference within 120 s, no first contact turned away by a rate limit, every contact answered. When the outage ends, every customer who waited on it is told within 30 minutes.

**Result (met).** Every dependency up: 100.00% (893 of 893) accepted, slowest reference 0.04 s, every target met. Network Monitoring API down: 100.00% (998 of 998) accepted, slowest reference 0.06 s, every target met.

Written by the run of 2026-11-16T18:23:40+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: offline (keyword rules stand in for the language model; none was called).

## Targets

| Target | Wanted | Every dependency up | Network Monitoring API down |
|---|---|---|---|
| Offered contacts accepted | at least 99.5% | 100.00% (893 of 893) (met) | 100.00% (998 of 998) (met) |
| Slowest complaint reference | within 120 s | 0.04 s (met) | 0.06 s (met) |
| First contacts turned away by a rate limit | none | 0 (met) | 0 (met) |
| Contacts answered when the surge was over | all | 893 of 893 (met) | 998 of 998 (met) |
| Waiting customers told that the outage is over | all, within 30 minutes of its end | 625 of 625 (met) | 699 of 699 (met) |
| Messages saying the network is clear while its status could not be read | none | does not apply | 0 (met) |

## What the callers saw

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Contacts offered | 893 | 998 |
| of them on the nodes of the outage | 625 | 699 |
| by the web / through a contact centre | 614 / 279 | 692 / 306 |
| Accepted | 893 | 998 |
| Left without a reference after 120 s | 0 | 0 |
| Turned away by a rate limit | 0 | 0 |
| Reference in hand, from when the contact was due: median / 95th / 99th / slowest | 0.01 / 0.014 / 0.017 / 0.042 s | 0.011 / 0.014 / 0.016 / 0.06 s |
| Sent more than once | 0 | 0 |
| Told that the reply will take longer | 0 | 0 |
| Web callers who stayed for the reply / saw it before leaving | 614 / 614 | 692 / 692 |
| Reply seen on the page, from when the contact was due: median / 95th / 99th / slowest | 1.217 / 2.422 / 2.426 / 2.43 s | 1.217 / 1.222 / 1.224 / 1.268 s |
| Not accepted because | — | — |

## What the platform recorded

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Contacts received | 893 | 998 |
| Answered / not answered when the run was read | 893 / 0 | 998 / 0 |
| First reply after receipt, all contacts: median / 95th / 99th / slowest | 0.179 / 1.421 / 1.662 / 1.756 s | 0.135 / 0.297 / 0.324 / 0.417 s |
| Reference issued, inside the API: median / 99th / slowest | 2 / 4 / 31 ms | 2 / 4 / 5 ms |
| The first reply was | outage_active.v1 625 · guided_steps.v1 203 · out_of_scope.v1 32 · handed_to_human.v1 30 · line_fault_hold.v1 3 | outage_active.v1 699 · network_unknown.v1 251 · handed_to_human.v1 28 · out_of_scope.v1 20 |
| Contacts stood, when read, as | AWAITING_NETWORK 628 · AWAITING_CUSTOMER 235 · HANDOFF 30 | AWAITING_NETWORK 950 · HANDOFF 28 · AWAITING_CUSTOMER 20 |
| Handed to a person for | BILLING 30 | BILLING 28 |
| Messages were read by | rules-v1 893 | rules-v1 998 |
| Messages saying the network is clear | 206 | 0 |
| Contacts whose runs were given up on | 0 | 0 |
| Writes for these contacts | COMPLAINT_REGISTER SUCCEEDED 893 · INCIDENT_LINK_COMPLAINT SUCCEEDED 625 | COMPLAINT_REGISTER SUCCEEDED 998 · INCIDENT_LINK_COMPLAINT SUCCEEDED 699 |
| Notices about these outages to customers | SUCCEEDED 3,454 · SKIPPED 949 | SUCCEEDED 5,109 · SKIPPED 1,458 |
| Items raised for operations staff | — | — |

## The backlog as staff saw it

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Readings taken / missed | 29 / 0 | 29 / 0 |
| Latest the generator offered a contact after it was due (inside every time above) | 0.03 s | 0.047 s |
| Most contacts waiting for a turn at once | 8 | 6 |
| Longest wait for a turn seen | 2 s | 0 s |
| Time spent behind (surge mode on) | 0 s | 0 s |
| Keeping up again after the last caller left | after 0 s | after 0 s |

## After the surge: the outage ends

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Contacts waiting to hear that the outage is over | 625 | 699 |
| Told that it is over | 625 | 699 |
| All of them told after | 80 s | 88 s |
| Those not told stood at | — | — |
| Told meanwhile that the status cannot be verified | 0 | 0 |
| Their lines tested / found failing | 625 / 3 | 699 / 6 |
| Tickets raised for a suspected fault on the restored nodes | 0 | 0 |
| Most contacts waiting for a turn meanwhile | 597 | 696 |
| Longest wait for a turn meanwhile | 74 s | 83 s |

## Where the time of a turn goes (Every dependency up)

| Step | Times | Median ms | 95th ms | Slowest ms |
|---|---|---|---|---|
| tool:network.line_test | 206 | 942 | 1428 | 1497 |
| tool:crm.customer | 893 | 4 | 8 | 16 |
| tool:network.node_status | 832 | 4 | 8 | 24 |
| tool:crm.lookup | 279 | 4 | 9 | 16 |
| triage.read_contact | 893 | 0 | 0 | 3 |

## Where the time of a turn goes (Network Monitoring API down)

| Step | Times | Median ms | 95th ms | Slowest ms |
|---|---|---|---|---|
| tool:crm.customer | 998 | 4 | 10 | 51 |
| tool:network.node_status | 1,049 | 0 | 1 | 158 |
| tool:crm.lookup | 306 | 5 | 10 | 19 |
| tool:network.line_test | 350 | 0 | 0 | 1 |
| triage.read_contact | 998 | 0 | 0 | 0 |

## What this rests on

- Run on one Linux machine with 16 CPU threads, which also ran the load generator, the simulator and PostgreSQL: the figures say that the design holds at this rate, not what a production deployment can carry.
- The stack was the local processes of `make dev`: one process of each kind, the API with a pool of 8 database connections, the orchestrator running 8 contacts at a time.
- Data: the synthetic pack of 400,000 customers. The OSS/BSS gateway is the simulator, which answers in a few milliseconds; real systems will be slower, and the time of a turn is mostly theirs.
- No language model took part, so surge mode had no model to spare: these runs show what the platform carries when a turn costs what the rules cost. What surge mode does when a model's seconds are on every turn is shown by a run with the simulated model (`SIMULATED_MODEL_SECONDS=1.5 make dev`), reported separately.
- Contacts were offered on a schedule fixed in advance (arrivals at random at the profile's rate, seed 42), each by a different customer. Every time is measured from the instant the contact was due, so the generator's own lag is inside it.
- About seven callers in ten were customers on the failed nodes and the rest were elsewhere with an ordinary mix of problems. About seven in ten came by the web with their own sign-in and stayed on the page for the reply, asking as the page does; the others were relayed by a contact centre's integration, which states the caller's line. The first table has the counts.
- A request that failed was sent again with the same request id until two minutes after the contact was due. A contact whose reference was not in the caller's hands by then counts as not accepted, also when the API issued one later.
- With the Network Monitoring API down (down in the simulator) nothing else was made to fail. That the event feed went on working is seen in the first replies: an outage can be told to a customer only on proof, and with the status unreadable the feed is the only proof there is.
- Not covered: several replicas of a process, a real language model, real enterprise systems, a network between the parts.
