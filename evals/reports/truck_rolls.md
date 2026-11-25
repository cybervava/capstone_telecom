# Unnecessary technician visits (simulation)

**What this proves.** Where the old process sent a technician for nothing, the workflow books none: not during an outage, though the customer asks again, and not for a fault the customer can fix themselves. A customer who goes on asking is handed to a person with the evidence. Where a visit was needed, it is still booked.

**Target.** Unnecessary visits cut by at least 50.0% (brief), judged on the visits the workflow books; needed visits still booked for at least 90.0% of cases.

**Result (met for the visits the workflow books; end to end it depends on decisions by people that a replay cannot measure).** The workflow booked a visit for 0 of 18 outage contacts and 0 of 10 contacts with a fault the customer could fix: at the baseline's volumes 0 of 3225 unnecessary visits, a cut of 100.0% in what the workflow itself books. The simulated customer went on asking, and 28 of 28 ended with a person, who decides with the evidence in front of them. If every customer went on asking like that, the target holds while those people send a technician to at most 50.0% of them. Needed visits: booked for 100.0% of cases, a median of 10.0 minutes after the contact.

Written by the run of 2026-11-25T18:55:17+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: offline (keyword rules stand in for the language model; none was called).

## End to end: what is left if customers go on asking and people give in

| Share of the customers who end with a person that is sent a technician | Unnecessary visits if every customer went on asking | Cut |
|---|---|---|
| 0% | 0 | 100.0% |
| 25% | 806 | 75.0% |
| 50% | 1612 | 50.0% |
| 75% | 2419 | 25.0% |
| 100% | 3225 | 0.0% |

## By kind of contact

| Kind | Visits in the baseline | Replayed | Offered a visit | Visit booked | Ended with a person, no visit | Ended with neither |
|---|---|---|---|---|---|---|
| outage | 2700 | 18 | 0 | 0 | 18 | 0 |
| line fault (visit needed) | 1225 | 10 | 10 | 10 | 0 | 0 |
| home equipment (not needed) | 525 | 10 | 0 | 0 | 10 | 0 |

## Where contacts ended, and why they were queued for a person

| Contact | Replayed | Visit booked | Queued for a person | Reasons |
|---|---|---|---|---|
| outage, declared by the NOC | 6 | 0 | 6 | CUSTOMER_DISPUTES_OUTAGE (6) |
| outage, not yet declared, alarm on the node | 6 | 0 | 6 | CUSTOMER_DISPUTES_OUTAGE (6) |
| outage, not yet declared, no alarm | 6 | 0 | 6 | CUSTOMER_DISPUTES_OUTAGE (6) |
| line fault (visit needed) | 10 | 10 | 0 | - |
| home equipment (not needed) | 10 | 0 | 10 | TROUBLESHOOTING (10) |

## What the customers were sent, in order

| Contact | Messages (templates) | Contacts |
|---|---|---|
| outage, declared by the NOC | outage_disputed.v1 > handed_to_human.v1 | 6 |
| outage, not yet declared, alarm on the node | network_unknown.v1:SUSPECTED_NODE_FAULT > outage_active.v1 > outage_disputed.v1 > handed_to_human.v1 | 6 |
| outage, not yet declared, no alarm | line_fault_hold.v1 > network_unknown.v1:SUSPECTED_NODE_FAULT > outage_active.v1 > outage_disputed.v1 > handed_to_human.v1 | 4 |
| outage, not yet declared, no alarm | network_unknown.v1:SUSPECTED_NODE_FAULT > outage_active.v1 > outage_disputed.v1 > handed_to_human.v1 | 2 |
| line fault (visit needed) | line_fault_hold.v1 > visit_offer.v1 > visit_being_booked.v1 > visit_booked.v1 | 10 |
| home equipment (not needed) | guided_steps.v1 > line_ok.v1 | 10 |

## What this rests on

- Baseline: the 4,450 technician visits recorded in the 20,000 labelled contacts of the synthetic pack, 3,225 of them unnecessary (2,700 sent during an outage, 525 for a fault the customer could have fixed). These are the generator's rates, not SkyLink's.
- What a contact's ground truth looks like to the platform is an assumption of the simulation: a needed visit is a line that keeps failing its remote test on a healthy node; an unneeded one for a device fault is a line that passes it.
- The simulated customer asks for a technician in the first message, takes the first slot whenever a visit is offered, and after every other answer asks again (here up to 2 more times) until a person takes over. That is the customer most likely to get a visit they do not need, not the usual one. A customer who does not ask again was not replayed here.
- The workflow books no visit without proof of a line fault, so what is left of the baseline's unnecessary visits is decided by people: by how many customers go on asking once they have been told of the outage or that their line is fine, and by how many of those the person sends a technician to. No replay can measure either. The first table of this report shows what each combination leaves. If every customer went on asking and every person gave in, nothing would be cut.
- Only broadband contacts with a sound address mapping are replayed here (the baseline has no visits for mobile). Outage contacts with a defective mapping are covered by the detection evaluation, where none was sent device steps or a visit.
- 38 contacts were replayed. They show what the rules do; they are not a sample to take rates from.
- The price of the hold is delay: a needed visit is booked about 10 minutes later than a process that books at once would.
