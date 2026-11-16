# Surge: a tenfold rise in contacts, with and without the Network Monitoring API

**What this proves.** That the platform stays available while contacts rise tenfold during a live outage: every caller gets a complaint reference at once, nobody's first contact is turned away, and the replies keep coming. And that this holds with the Network Monitoring API down, without anyone being told that the network is fine.

**Target.** Contacts offered at 1 a second rising to 10 within 15 minutes and held for 15 minutes, once with every dependency up and once with the Network Monitoring API down. In both runs: at least 99.5% of the offered contacts accepted, every complaint reference within 120 s, no first contact turned away by a rate limit.

**Result (met).** Every dependency up: 100.00% (13,941 of 13,941) accepted, slowest reference 0.25 s, every target met. Network Monitoring API down: 100.00% (14,041 of 14,041) accepted, slowest reference 0.11 s, every target met.

Written by the run of 2026-11-16T07:37:36+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: offline (keyword rules stand in for the language model; none was called).

## Targets

| Target | Wanted | Every dependency up | Network Monitoring API down |
|---|---|---|---|
| Offered contacts accepted | at least 99.5% | 100.00% (13,941 of 13,941) (met) | 100.00% (14,041 of 14,041) (met) |
| Slowest complaint reference | within 120 s | 0.25 s (met) | 0.11 s (met) |
| First contacts turned away by a rate limit | none | 0 (met) | 0 (met) |
| Messages saying the network is clear while its status could not be read | none | does not apply | 0 (met) |

## What the callers saw

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Contacts offered | 13,941 | 14,041 |
| of them on the nodes of the outage | 9,759 | 9,829 |
| by the web / through a contact centre | 9,753 / 4,188 | 9,707 / 4,334 |
| Accepted | 13,941 | 14,041 |
| Left without a reference after 120 s | 0 | 0 |
| Turned away by a rate limit | 0 | 0 |
| Reference in hand, from when the contact was due: median / 95th / 99th / slowest | 0.009 / 0.013 / 0.015 / 0.253 s | 0.009 / 0.012 / 0.014 / 0.11 s |
| Sent more than once | 0 | 0 |
| Told that the reply will take longer | 0 | 0 |
| Web callers who stayed for the reply / saw it before leaving | 9,753 / 9,753 | 9,707 / 9,707 |
| Reply seen on the page, from when the contact was due: median / 95th / 99th / slowest | 1.215 / 2.42 / 2.424 / 2.497 s | 1.214 / 1.218 / 1.221 / 1.353 s |
| Not accepted because | — | — |

## What the platform recorded

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Contacts received | 13,941 | 14,041 |
| Answered / not answered when the run was read | 13,941 / 0 | 14,041 / 0 |
| First reply after receipt, all contacts: median / 95th / 99th / slowest | 0.177 / 1.409 / 1.63 / 1.83 s | 0.14 / 0.3 / 0.326 / 0.437 s |
| Reference issued, inside the API: median / 99th / slowest | 1 / 3 / 51 ms | 1 / 3 / 27 ms |
| The first reply was | outage_active.v1 9,759 · guided_steps.v1 3,308 · handed_to_human.v1 418 · out_of_scope.v1 417 · line_fault_hold.v1 39 | outage_active.v1 9,829 · network_unknown.v1 3,361 · handed_to_human.v1 437 · out_of_scope.v1 414 |
| Contacts stood, when read, as | AWAITING_NETWORK 9,773 · AWAITING_CUSTOMER 3,750 · HANDOFF 418 | AWAITING_NETWORK 10,497 · HANDOFF 3,130 · AWAITING_CUSTOMER 414 |
| Handed to a person for | BILLING 418 | NETWORK_STATUS_UNVERIFIED 2,693 · BILLING 437 |
| Messages were read by | rules-v1 13,941 | rules-v1 14,041 |
| Messages saying the network is clear | 3,372 | 0 |
| Contacts whose runs were given up on | 0 | 0 |
| Writes for these contacts | COMPLAINT_REGISTER SUCCEEDED 13,941 · INCIDENT_LINK_COMPLAINT SUCCEEDED 9,759 | COMPLAINT_REGISTER SUCCEEDED 14,041 · INCIDENT_LINK_COMPLAINT SUCCEEDED 9,829 |
| Notices about these outages to customers | SUCCEEDED 42,845 · SKIPPED 12,390 | SUCCEEDED 46,613 · SKIPPED 13,303 |
| Items raised for operations staff | — | — |

## The backlog as staff saw it

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Readings taken / missed | 364 / 0 | 364 / 0 |
| Latest the generator offered a contact after it was due (inside every time above) | 0.236 s | 0.082 s |
| Most contacts waiting for a turn at once | 11 | 14 |
| Longest wait for a turn seen | 2 s | 1 s |
| Time spent behind (surge mode on) | 0 s | 0 s |
| Keeping up again after the last caller left | after 0 s | after 0 s |

## After the surge: the outage ends

|  | Every dependency up | Network Monitoring API down |
|---|---|---|
| Contacts waiting to hear that the outage is over | 9,759 | 9,829 |
| Told that it is over | 9,515 | 9,626 |
| All of them told after | not within the time allowed | not within the time allowed |
| Most contacts waiting for a turn meanwhile | 9,718 | 10,073 |
| Longest wait for a turn meanwhile | 1154 s | 1209 s |

## Where the time of a turn goes (Every dependency up)

| Step | Times | Median ms | 95th ms | Slowest ms |
|---|---|---|---|---|
| tool:network.line_test | 3,501 | 914 | 1447 | 1508 |
| tool:network.node_status | 16,885 | 4 | 9 | 53 |
| tool:crm.customer | 13,941 | 4 | 9 | 55 |
| tool:crm.lookup | 4,188 | 4 | 8 | 53 |
| tool:field.booked_for_service | 25 | 3 | 4 | 6 |
| tool:field.slots | 25 | 3 | 5 | 6 |
| triage.read_contact | 13,941 | 0 | 0 | 2 |

## Where the time of a turn goes (Network Monitoring API down)

| Step | Times | Median ms | 95th ms | Slowest ms |
|---|---|---|---|---|
| tool:crm.customer | 14,041 | 4 | 9 | 55 |
| tool:crm.lookup | 4,334 | 4 | 9 | 54 |
| tool:network.node_status | 31,421 | 0 | 1 | 153 |
| tool:network.line_test | 18,168 | 0 | 0 | 3 |
| triage.read_contact | 14,041 | 0 | 0 | 0 |

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
