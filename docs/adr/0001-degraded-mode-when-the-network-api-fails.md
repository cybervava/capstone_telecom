# ADR 0001: What the platform does when the Network Monitoring API fails

Status: accepted, 16 November 2026 (milestone M3). Stand-in data and a simulated gateway; see assumption A1 of the execution plan.

## Context

The brief's curveball:

> The Network Monitoring API goes down during a live outage, and Regulatory Affairs adds that every outage contact must get a complaint reference number within 2 minutes. Groups must show safe degraded mode (no false "all clear" messages, no truck dispatch on stale data) and meet the reference-number rule.

It changes two things the happy path takes for granted. The state of the network is not always knowable when a customer writes in. And the complaint reference has a deadline that holds for every contact, at the moment contacts are most numerous.

## Decision

The platform does less when it knows less, and says so. It never fills the gap with a guess.

1. **The reference waits for nothing.** Intake commits the contact, its reference and its wake-up in one transaction and answers. It calls no model and no enterprise system, has its own database connections, and never rate-limits a first contact (a contact centre's callers are counted by the line the centre names, so a surge through one channel is not one caller flooding). Design rule 1.
2. **A status that cannot be read is unknown, never clear.** Refused, late, erroring and merely old answers are all `UNKNOWN`. The customer is told that the live status cannot be verified, that the complaint is registered, and that the platform will look again. Design rule 3.
3. **The event feed can still prove an outage. Nothing but a live read can prove its absence.** With the API down and the NOC feed working, a customer on a node with a declared outage is still told of it and attached to its incident. A customer on any other node is told that the status cannot be verified. With the feed silent as well, everyone is.
4. **Nothing is done on a status nobody could read.** No device steps and no technician visit are offered without a node proven clear at that moment. The executor reads the node again before it books a visit a customer chose earlier, and books nothing without that read. A credit is worked out only from the NOC's own record of the outage's end and applied only with its approval. Design rules 4 and 5.
5. **What may be said is checked where messages are stored.** Each message template declares what it claims about the network; a message whose claim the turn's evidence does not back is not stored, is audited, and the contact goes to a person.
6. **A failing dependency is rested.** After five failures in a row a dependency is not called for ten seconds, then tried once. Callers are told "not attempted", which every reader treats as "not verified". An outage of the API costs one call per rest period, not one per contact.
7. **Contacts wait, then go to a person.** A contact with an unverified status is looked at again every minute; after five looks without an answer it is handed to a person with the reason. When the API returns before that, the customer is told what is true and no person is involved.
8. **Failures that belong to no contact reach staff.** A write that fails with no conversation waiting on it, and a feed that cannot go on by itself, are items on the operations queue.
9. **Under load the platform sheds cost, not safety.** While contacts queue for the orchestrator, callers are told at once that the reply will take longer, and messages are read by the rules instead of the language model. What may be said and done needs the same proof as without load.

## What was decided against

| Alternative | Why not |
|---|---|
| Show the last known status with its age | "No outage reported, as of 20 minutes ago" is read as "no outage". It is exactly the stale all-clear the brief forbids, and the first minutes of an outage are when it is most wrong |
| Treat an unreadable status as clear and carry on with device steps | A customer in an outage is walked through a router reset, and a technician may be sent to a fault that is in the network |
| Take the feed's silence as proof that nothing is wrong | A feed that says nothing and a feed that is not being read look the same from here |
| Stop taking contacts, or queue every contact for a person, until the API is back | The reference rule is broken at once in the first case. In the second the queue grows by ten contacts a second for a failure that software can sit out |
| Keep trying the API for every contact | Every turn waits out a timeout, the orchestrator falls behind, and the failing system is hammered while it tries to recover |

## What it costs

- While the API is down and the feed is silent, a customer in a real outage is told only that the status cannot be verified. They hear of the outage when either source returns.
- A customer on a healthy node gets no help with their own equipment until the node can be read. After about five minutes they are with a person.
- A slot the customer picks while the status cannot be read is not booked. They are told that the status cannot be verified, the offer is withdrawn, and when the node can be read again and the line still fails, a visit is offered afresh: the customer chooses a second time.
- With the feed silent as well, a customer who waits on an outage that has ended hears of it from the slow watch on the live status, up to fifteen minutes after the API returns. With the feed working they hear at once.
- A long failure of the API fills the human queue. Every contact on a node with no declared outage reaches a person after five looks, a minute apart; in the load test's half hour with the API down that was 2,693 of 3,361 such contacts. How long they should wait is decision D11.
- For up to ten seconds after the API returns, a process that rested it still answers as if it were down.
- When an outage ends, every waiting line is tested before its customer is told. After a large outage that is thousands of line tests in a row, and the news travels at their pace (see the load test's report). Testing a sample of lines per node instead is decision D10.

## Not built

- A circuit's state is held by each process for itself and is seen only in its logs.
- Nothing alerts on the operations queue; a person has to look at the staff desk (M5).
- Rate limits live in one API process. With several replicas they belong at the edge (M4).
- A proactive notice is built from a recent feed event and is not checked against the network again when it is sent.

## How it is shown

| Claim | Evidence |
|---|---|
| Safe in every way the API can fail, with the feed working and silent | `evals/reports/curveball.md` (`make eval`): the scene of the brief played for four kinds of failure and two states of the feed, scored against what the simulator knows to be true. It also shows what customers were told instead, what they heard once the API was back, and what became of a visit chosen meanwhile |
| The reference rule under a tenfold surge, with the API up and down; every contact answered; no all-clear; everyone told when the outage ends | `loadtest/reports/surge-brief-before-fix.md` (the half-hour profile, run before the fix to the rule for failing lines) and `surge-quick.md` (two minutes a run, on the code as it is); `make loadtest` |
| Each rule on its own | `tests/e2e/test_edges_and_degraded_mode.py`, `test_resting_dependencies.py`, `test_message_claims.py`, `test_visits.py`, `test_surge_mode.py`; `tests/unit/guardrails/` |
