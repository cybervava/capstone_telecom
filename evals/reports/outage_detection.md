# Outage detected for the customers it affects

**What this proves.** A customer whose node is in outage when they contact us is told about the outage: at once when the NOC has declared it, or after a hold when it has not yet, and in neither case sent device steps or a technician.

**Target.** At least 95.0% of affected contacts (brief).

**Result (met).** 97.47% of affected contacts, weighted by how often each kind occurs; 140 contacts replayed on 32 nodes, undeclared outages declared 25 to 577 seconds after the contact.

Written by the run of 2026-11-17T01:26:39+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: offline (keyword rules stand in for the language model; none was called).

## By kind of contact (service / timing / mapping defect)

| Kind | In the pack | Replayed | At contact | Held, then told | Missed | Rate |
|---|---|---|---|---|---|---|
| broadband/after_detection/ambiguous | 49 | 2 | 2 | 0 | 0 | 100.0% |
| broadband/after_detection/missing | 89 | 2 | 2 | 0 | 0 | 100.0% |
| broadband/after_detection/none | 5526 | 18 | 18 | 0 | 0 | 100.0% |
| broadband/after_detection/stale | 47 | 1 | 1 | 0 | 0 | 100.0% |
| broadband/before_detection/ambiguous | 18 | 2 | 0 | 2 | 0 | 100.0% |
| broadband/before_detection/missing | 19 | 3 | 0 | 3 | 0 | 100.0% |
| broadband/before_detection/none | 1411 | 36 | 0 | 36 | 0 | 100.0% |
| broadband/before_detection/stale | 8 | 3 | 0 | 3 | 0 | 100.0% |
| mobile/after_detection/ambiguous | 20 | 3 | 0 | 0 | 3 | 0.0% |
| mobile/after_detection/missing | 29 | 2 | 0 | 0 | 2 | 0.0% |
| mobile/after_detection/none | 1429 | 18 | 18 | 0 | 0 | 100.0% |
| mobile/after_detection/stale | 11 | 1 | 0 | 0 | 1 | 0.0% |
| mobile/before_detection/ambiguous | 7 | 7 | 0 | 0 | 7 | 0.0% |
| mobile/before_detection/missing | 4 | 3 | 0 | 0 | 3 | 0.0% |
| mobile/before_detection/none | 332 | 36 | 0 | 18 | 18 | 53.0% |
| mobile/before_detection/stale | 1 | 3 | 0 | 0 | 3 | 0.0% |

## What the missed contacts were told first

| Kind and first message | Contacts |
|---|---|
| mobile/after_detection/ambiguous: network_unknown.v1:MAPPING_UNRESOLVED | 3 |
| mobile/after_detection/missing: network_unknown.v1:MAPPING_UNRESOLVED | 2 |
| mobile/after_detection/stale: network_unknown.v1:MAPPING_UNRESOLVED | 1 |
| mobile/before_detection/ambiguous (alarm on the node): network_unknown.v1:MAPPING_UNRESOLVED | 4 |
| mobile/before_detection/ambiguous (no alarm): network_unknown.v1:MAPPING_UNRESOLVED | 3 |
| mobile/before_detection/missing (alarm on the node): network_unknown.v1:MAPPING_UNRESOLVED | 2 |
| mobile/before_detection/missing (no alarm): network_unknown.v1:MAPPING_UNRESOLVED | 1 |
| mobile/before_detection/none (no alarm): no_outage_reported.v1 | 18 |
| mobile/before_detection/stale (alarm on the node): network_unknown.v1:MAPPING_UNRESOLVED | 1 |
| mobile/before_detection/stale (no alarm): network_unknown.v1:MAPPING_UNRESOLVED | 2 |

## What this rests on

- The weights are the labelled outage contacts of the 400,000-customer synthetic pack: 9,000 contacts, 20.0% of them before the NOC had declared the outage. The replay itself runs on a 1,000-customer pack from the same generator.
- An outage the NOC has not declared is the stand-in gateway's own model: the node status and the feed show nothing, every line on the node fails its test, and in half the cases (as measured in the pack) a critical alarm is up on the node.
- In the pack the NOC declares an outage at most 577 seconds after it starts, and the hold is 600 seconds. A NOC that takes longer than the hold would turn held contacts without an alarm into visit offers: the hold must be set from the real delay.
- 140 contacts were replayed, and some kinds are rare: where the pack has two or three such customers the rate of that kind rests on two or three contacts. The kinds that decide the figure are the large ones.
- Customers are identified by the channel; wordings come from the phrase bank of the synthetic transcripts and are read by the offline keyword rules, not by a language model. A complaint those rules do not recognise is answered as out of scope, and counts as missed; the first run of this evaluation found five such phrases, which the rules now know.
- What is missed is by design of the thin mobile path (A9): a mobile connection has no remote test, so a mobile customer is not held before the NOC declares unless an alarm is up, and a defective tower mapping cannot be repaired.
