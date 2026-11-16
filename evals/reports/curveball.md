# The curveball: the Network Monitoring API fails during a live outage

**What this proves.** That the degraded mode is safe however the API fails, with the event feed working and without it: every contact has its complaint reference, nobody is told that the network is clear, no technician is sent on a status nobody could read, no credit is applied without an approval and no node gets a second incident.

**Target.** In every playing of the scene: zero contacts without a reference within two minutes, zero messages saying the network is clear to a customer in an outage (and none at all while the status cannot be read), zero visits booked without a status read at that moment, zero credits applied without an approval, at most one incident on a node.

**Result (met).** In all 8 playings (72 contacts) every rule held: every contact had its reference, nobody in an outage was told the network is clear, no visit was booked without a status read, no credit was applied and no node got a second incident. Of the customers on an outage node who wrote in while the API was failing, 20 were told of the outage on the evidence there was (the feed, or a status read that succeeded) and 12 were told that the status cannot be verified.

Written by the run of 2026-11-16T14:20:37+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: offline (keyword rules stand in for the language model; none was called).

## The rules of the brief, while the API was failing

| The API | The feed | Contacts | Without a reference in 2 min | Told the network is clear (to a customer in an outage) | Visits booked (without a status read) | Credits applied | Most incidents on a node |
|---|---|---|---|---|---|---|---|
| refuses every call (503) | working | 9 | 0 | 0 (0) | 0 (0) | 0 | 1 |
| refuses every call (503) | silent since before the outage | 9 | 0 | 0 (0) | 0 (0) | 0 | 1 |
| answers too late (504 after a hold) | working | 9 | 0 | 0 (0) | 0 (0) | 0 | 1 |
| answers too late (504 after a hold) | silent since before the outage | 9 | 0 | 0 (0) | 0 (0) | 0 | 1 |
| fails every call (500) | working | 9 | 0 | 0 (0) | 0 (0) | 0 | 1 |
| fails every call (500) | silent since before the outage | 9 | 0 | 0 (0) | 0 (0) | 0 | 1 |
| fails half of its calls, at random (500) | working | 9 | 0 | 4 (0) | 1 (0) | 0 | 1 |
| fails half of its calls, at random (500) | silent since before the outage | 9 | 0 | 2 (0) | 0 (0) | 0 | 1 |

## What customers were told instead, and once the API was back

| The API | The feed | On an outage node, while it failed: told of the outage / told it cannot be verified | On a healthy node, while it failed: told it cannot be verified | After it was back: told of the outage that lasts | After it was back: told the other is over | The visit chosen while it failed: told then / in the end | Left without an answer | With a person |
|---|---|---|---|---|---|---|---|---|
| refuses every call (503) | working | 4 / 0 of 4 | 2 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 0 |
| refuses every call (503) | silent since before the outage | 0 / 4 of 4 | 2 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 0 |
| answers too late (504 after a hold) | working | 4 / 0 of 4 | 2 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 0 |
| answers too late (504 after a hold) | silent since before the outage | 0 / 4 of 4 | 2 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 0 |
| fails every call (500) | working | 4 / 0 of 4 | 2 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 0 |
| fails every call (500) | silent since before the outage | 0 / 4 of 4 | 2 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 0 |
| fails half of its calls, at random (500) | working | 4 / 0 of 4 | 0 of 2 | 2 of 2 | 1 of 1 | visit_being_booked.v1 · visit_being_booked.v1 · visit_booked.v1 / visit_booked.v1 (booked) | 0 | 1 |
| fails half of its calls, at random (500) | silent since before the outage | 4 / 0 of 4 | 0 of 2 | 2 of 2 | 1 of 1 | network_unknown.v1 / visit_offer.v1 | 0 | 1 |

## What this rests on

- Replayed in process on the 1,000-customer pack: the real workflow, orchestrator, executor and feed consumer against the stand-in gateway, whose fault injection makes the Network Monitoring API fail. The scene is described at the top of `evals/test_curveball.py`.
- Scored against what the simulator knows to be true (which nodes were in an outage and when), and against the platform's own ledger and audit trail for writes.
- The reference is issued by the intake before anything is asked of any other system, so its time does not depend on the failure; the time counted is the one the platform recorded for each contact. Under load it is measured by the load test (`loadtest/`).
- With the API failing half of its calls, a read that succeeds is evidence: a customer on a healthy node may then be told that no outage is reported, and a visit may be booked on a status read at that moment. Neither is counted against the run. Telling a customer in an outage that the network is clear would be. Which calls fail is left to chance, so these two playings differ from run to run in what customers are told; the rules must hold whichever calls fail.
- After the API is back the scene runs for the slow watch's interval (15 minutes). A customer told of an outage normally hears of its end through the feed; with the feed silent, that watch on the live status is what tells them.
- A silent feed is one whose consumer has read nothing since before the outage: what it last said is stale and is not used as evidence.
