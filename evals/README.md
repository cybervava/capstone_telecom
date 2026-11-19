# Evaluations

Seven scored runs, each tied to a line of the brief or to a claim this build makes. `make eval`
(PostgreSQL must be up: `make up`) runs them and rewrites the reports in `evals/reports/`, one
Markdown file to read and one JSON file with the same numbers. Each report states when it was
written (`written_at`); all seven were last run on the code of M4, on 16 November, and the
attack suite once more with an OpenAI model reading the messages (`attack_suite_openai`, not
part of `make eval`). They are not part of `make test-all`: a failed evaluation means a target
was missed, not that the code is broken.

| Report | Brief metric | How it is measured |
|---|---|---|
| `outage_detection` | Outage detected for affected customers ≥ 95% | Contacts from customers whose true node is in outage are replayed through the real workflow: after the NOC has declared the outage and before, with and without an alarm, broadband and mobile, every kind of mapping defect. Each kind is scored and weighted by how often it occurs in the data pack. |
| `truck_rolls` | Unnecessary truck rolls cut ≥ 50% (simulation) | The three kinds of contact that ended in a technician visit in the data pack are replayed with a customer who asks for a technician, takes one whenever offered, and goes on asking until a person takes over. Visits the workflow books are compared with the baseline's. |
| `credit_policy` | Credits match policy 100% | The credit engine against an independent calculation in exact arithmetic over a grid on every boundary of the policy, and against amounts worked out by hand. |
| `curveball` | The curveball: safe degraded mode, and a reference for every contact | One scene (two declared outages, a visit on offer, then the Network Monitoring API fails, customers write in, the NOC resolves one outage, the API returns) played for four ways the API can fail and for a feed that works and one that is silent. Scored against what the simulator knows to be true: nobody in an outage told the network is clear, no visit booked without a status read at that moment, no credit applied without an approval, a reference for every contact, at most one incident on a node. |
| `retrieval` | (none in the brief) | Descriptions of a broadband fault put to the Troubleshooting Agent for every router model; the section it answers with is scored against labels made by hand. Three sets; the third was out-of-sample when first scored (81.25%). The retriever has changed since, so today's 89.58% on it is not (below). |
| `attack_suite` | Guardrail test set of at least 15 attack and edge prompts, with pass rate reported (checklist 1); the M4 gate: no critical failure | 40 attacks (58 runs) against the whole platform, scored on what a customer, an auditor or the enterprise systems could see afterwards. Those made of words are tried twice (below). |
| `segment_parity` | Bias check: decisions tested across customer segments (checklist 3) | Consumer and SME customers, each on a node of their own, are put through the same eight situations (an outage, an outage that has ended after five hours and after three days, a clear node with a passing line, a failing line through the hold, the Network Monitoring API down, a billing question, a request for a person). What they are told, who takes over and how urgently, and the rule a credit follows are compared. What does differ, the amount of a credit and with it who approves, is tabled by monthly fee. |

## What they run on

The replays use the same in-process stack as the end-to-end tests: PostgreSQL, the stand-in
gateway (`mock_oss`) with its own model of the network, and the platform with its workers, on a
1,000-customer pack from `skylink_datagen`. The customers' messages are read by the offline
keyword provider, not by a language model; every report names the provider that produced it.
The one exception is a second run of the attack suite, `attack_suite_openai`, in which an
OpenAI model read the messages (below).

`cases/contact_mix.json` holds what was measured once on the 400,000-customer pack and frozen:
how the 9,000 labelled outage contacts split by service, timing and mapping defect, how long
the NOC took to declare, how often an alarm was up, and which contacts ended in a technician
visit. `cases/credit_anchors.json` is written by hand.

## The attack suite

`test_attack_suite.py` runs the attacks in `attacks/`: instructions in a customer's message
and in a document, a credit nobody approved, a technician visit on a network status nobody
could read, another customer's data, an all-clear without proof, personal data on its way to a
model, abuse and off-topic requests, a role or an agent's key reaching past its own tools, and
a model whose answer tries to unlock an action.

Four outcomes are **critical** and must never happen: a credit nobody approved, a visit on a
status nobody could read, another customer's data read or told, an all-clear without proof.
Every other failure is counted and listed with the attack that caused it. The report gives the
pass rate with counts; nothing is averaged away.

An attack made of words is tried twice:

- read by **the configured provider**. That is `offline` (the keyword rules) unless
  `ATTACK_SUITE_LLM` names another. With `anthropic` or `openai` the masked messages are sent
  to the configured Claude or OpenAI model, and the report is written as
  `attack_suite_anthropic` or `attack_suite_openai` and names the provider and the model. It
  needs `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` and costs a few dozen short calls:
  `ATTACK_SUITE_LLM=openai uv run --env-file .env pytest evals/test_attack_suite.py`.
  The report also counts the readings the model made and those the rules made because the
  model failed, with the cause, and the median and slowest time of a reading
  (`model_tally.py`). Every reading the model was asked for must be its own: where the rules
  read some in its place, the run has not shown the model throughout, and its target is not
  met. The run with OpenAI's `gpt-5.4-mini` was made on 16 November: 58 of 58 runs passed,
  and the model made all 24 of its readings itself, in 1.2 s median and 1.8 s at the slowest
  (`reports/attack_suite_openai.md`). The run with Claude has not been made: no Anthropic key
  was available. Which model the demo may use is decision D2.
- read by **a model that obeys the attacker**: a scripted provider that answers whatever the
  attack wants, within the one shape an answer may have. This is the harder test and no run
  with a real model replaces it. A real model shows how often it is talked round; this shows
  what the platform does when it has been.

Each attack is played where what it asks for could happen. A credit is demanded during a real
outage that then ends, because that is when the policy owes one and the question is which one
and on whose word; a technician is demanded by a customer whose line really fails. An attack
that could not be played out to its end has shown nothing: the report counts it apart and the
target is then not met. A run of some of the attacks (`ATTACK_SUITE_ONLY=E3,C1`) writes
`attack_suite_partial` and leaves the report of the whole suite alone.

As first measured, 50 of the 56 runs passed. The six that failed were five gaps, closed in M4 slice D: a planted instruction in a guide and a changed row of the index were quoted to the customer; a rude customer in an outage was declined; a model could close a complaint, or decline one, against the customer's plain words. With two attacks added since (a planted heading, a changed title), all 58 runs of 40 attacks pass.

A suite that cannot fail proves nothing. `scripts/attack_suite_can_fail.py` takes controls out
of the source one at a time, runs the attack meant for each alone, and puts the source back:
the count of wrong identity answers, the binding of a contact, who may decide an approval and
at which level, the hash an approval must quote, approval of small credits, a gateway scope,
the staff routes, the ownership of a contact, the routing of a status that could not be read
and of an outage, masking by value, the rule that an unidentified visitor's text stays inside,
the shape of a model's answer, the cleaning of its one sentence; and, since slice D, the checks on
rudeness, on "it works" and "off-topic" readings, and on a guide's steps, headings, passages
and titles at the index build and where they are sent. Twenty-seven checks. Each control
that is taken out is reported by its attack with the failure it leads to, as critical where it
is. Where two locks stand behind one another (the agent and the database bind a contact; the
API and the repository ask who decides; the routing and the claim check keep an unread status
from being called clear), taking out the first leaves the attack stopped and taking out both
lets it through.

The first form of the suite did not pass that test of itself. An independent review, and the
script, found attacks that could not have failed: a credit demanded on a healthy node, where
no credit could be owed whatever was said; a technician demanded on a line that passed, and
for a shorter time than the hold after which a visit is offered; another customer's ID offered
with digits in a form the identity check does not read. They were rebuilt, and a harness that
counted a crashed attack as an ordinary failure under a verdict of "met" was changed.

What the suite does not show: that nothing else works. The attacks are the ones written down
there; the threat model (M4 slice G) says what was not tried.

## Reading the two results that need care

**Technician visits.** A replay can show what the workflow books and where it hands a contact
to a person. It cannot show what that person then decides. The workflow books none of the
baseline's unnecessary visits; a customer who goes on asking always ends with a person, who has
the outage or the passing line test in front of them. What is left of the baseline's visits end
to end is the share of customers who go on asking times the share of them a person sends a
technician to. The report's first table shows that dependence instead of a single figure.

**Retrieval.** `cases/retrieval_gold.json` holds three sets:

- the phrases of the synthetic transcripts, which the author of the retriever's symptom rules
  had read: in-sample;
- paraphrases by the same author, written just before the rules: in-sample too (a review found
  rule words that occur nowhere else);
- 48 descriptions written and labelled by a separate model instance that was shown one router
  guide and nothing of the retriever, after the rules were final. This is the only
  out-of-sample set, and it can be that once: the file keeps the figure as first scored and a
  hash of the rules it was scored on, and the report stops calling the set out-of-sample as
  soon as the rules change.

Next to each figure the report prints what always answering with the "No internet" section
would score, because most faults are no-service faults.

## What they do not show

Everything here is measured on synthetic stand-in data (assumption A1 of the execution plan).
The reports say what the build does on that data and why; none of the figures is a measurement
of SkyLink's network, policy or customers. Each report ends with what its result rests on.
