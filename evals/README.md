# Evaluations

Four scored runs, each tied to a line of the brief or to a claim this build makes. `make eval`
(PostgreSQL must be up: `make up`) runs them and rewrites the reports in `evals/reports/`, one
Markdown file to read and one JSON file with the same numbers. They are not part of
`make test-all`: a failed evaluation means a target was missed, not that the code is broken.

| Report | Brief metric | How it is measured |
|---|---|---|
| `outage_detection` | Outage detected for affected customers ≥ 95% | Contacts from customers whose true node is in outage are replayed through the real workflow: after the NOC has declared the outage and before, with and without an alarm, broadband and mobile, every kind of mapping defect. Each kind is scored and weighted by how often it occurs in the data pack. |
| `truck_rolls` | Unnecessary truck rolls cut ≥ 50% (simulation) | The three kinds of contact that ended in a technician visit in the data pack are replayed with a customer who asks for a technician, takes one whenever offered, and goes on asking until a person takes over. Visits the workflow books are compared with the baseline's. |
| `credit_policy` | Credits match policy 100% | The credit engine against an independent calculation in exact arithmetic over a grid on every boundary of the policy, and against amounts worked out by hand. |
| `retrieval` | (none in the brief) | Descriptions of a broadband fault put to the Troubleshooting Agent for every router model; the section it answers with is scored against labels made by hand. Three sets, of which one is out-of-sample (below). |

## What they run on

The replays use the same in-process stack as the end-to-end tests: PostgreSQL, the stand-in
gateway (`mock_oss`) with its own model of the network, and the platform with its workers, on a
1,000-customer pack from `skylink_datagen`. The customers' messages are read by the offline
keyword provider, not by a language model; every report names the provider that produced it.

`cases/contact_mix.json` holds what was measured once on the 400,000-customer pack and frozen:
how the 9,000 labelled outage contacts split by service, timing and mapping defect, how long
the NOC took to declare, how often an alarm was up, and which contacts ended in a technician
visit. `cases/credit_anchors.json` is written by hand.

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
