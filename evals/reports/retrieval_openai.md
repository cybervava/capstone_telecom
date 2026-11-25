# The right guide section, with a language model beside the search

**What this proves.** What changes when a model is shown the customer's words beside the sections the search found and chooses among them: how often the section is the right one, and what became of the passages it pointed to. The steps a customer is sent are the guide's own words either way.

**Target.** No brief target. Every answer from the customer's own router model, right section for at least 70.0% of the independent descriptions, and every question the model was asked answered by the model itself.

**Result (met).** With gpt-5.4-mini-2026-03-17 choosing among the sections found, the right section for 144 of 144 independent descriptions (100.0%); the search and the keyword rules alone: 129 of 144 (89.58%), on the same 3 router models. Answers from another model's guide: 0. Readings by the model: 144 of 144 (served by gpt-5.4-mini-2026-03-17; 1.4 s median, 3.2 s the slowest).

Written by the run of 2026-11-25T18:59:18+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: openai.

## What the model's answers came to

| Outcome | Answers |
|---|---|
| OK | 144 |

## Passages the model pointed to

| What became of it | Customer's words | Guide's words |
|---|---|---|
| NONE | 3 | 0 |
| NOT_A_FAULT_DESCRIPTION | 1 | 0 |
| NOT_FOR_AN_ANSWER | 3 | 0 |
| NOT_IN_SOURCE | 17 | 0 |
| QUOTED | 117 | 144 |
| TOO_SHORT | 3 | 0 |

## Descriptions sent a section outside the accepted ones, with the model

| Description | Accepted | Sent instead (times, over the router models) |
|---|---|---|
| none |  |  |

## Descriptions the model sent to another section than the rules

| Description | Accepted | Rules alone | With the model |  | Router |
|---|---|---|---|---|---|
| Where is the WPS button on this router? My printer needs it to connect. | wifi | no-internet | wifi | right now | AL-AX18 |
| new phone says incorrect pasword even though i am typing same as on the sticker | wifi | no-internet | wifi | right now | AL-AX18 |
| video calls keep freezing and my voice breaks in every office meeting | slow-speed | no-internet | slow-speed | right now | AL-AX18 |
| connection is not down, it's just crawling, a 1 GB file shows 4 hours | slow-speed | no-internet | slow-speed | right now | AL-AX18 |
| Net was acting weird so I switched the router off. How long to wait after turning it on? | power-cycle | no-internet | power-cycle | right now | AL-AX18 |
| Where is the WPS button on this router? My printer needs it to connect. | wifi | no-internet | wifi | right now | AL-FX30 |
| new phone says incorrect pasword even though i am typing same as on the sticker | wifi | no-internet | wifi | right now | AL-FX30 |
| video calls keep freezing and my voice breaks in every office meeting | slow-speed | no-internet | slow-speed | right now | AL-FX30 |
| connection is not down, it's just crawling, a 1 GB file shows 4 hours | slow-speed | no-internet | slow-speed | right now | AL-FX30 |
| Net was acting weird so I switched the router off. How long to wait after turning it on? | power-cycle | no-internet | power-cycle | right now | AL-FX30 |
| Where is the WPS button on this router? My printer needs it to connect. | wifi | no-internet | wifi | right now | AL-R12 |
| new phone says incorrect pasword even though i am typing same as on the sticker | wifi | no-internet | wifi | right now | AL-R12 |
| video calls keep freezing and my voice breaks in every office meeting | slow-speed | no-internet | slow-speed | right now | AL-R12 |
| connection is not down, it's just crawling, a 1 GB file shows 4 hours | slow-speed | no-internet | slow-speed | right now | AL-R12 |
| Net was acting weird so I switched the router off. How long to wait after turning it on? | power-cycle | no-internet | power-cycle | right now | AL-R12 |

## What this rests on

- The independent descriptions are no longer out of sample for the keyword rules (see the report `retrieval`), and they were written by another model instance than the one asked here: neither figure says what real customers' words would score.
- Measured on 3 of the router models that have a guide, to keep the number of calls down (`RETRIEVAL_LLM_ROUTERS`). Each description is put to the agent on its own, with nothing personal in it to mask.
- The model can only choose among the sections the search found: a section it was not shown is not taken from it, and its answer is then counted as refused.
- A passage the model points to is used only if it is found word for word where the model says it is, and says nothing that has no place in an answer. The table counts what was quoted and what was left out, by reason.
