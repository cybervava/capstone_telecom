# The right guide section for the customer's own router

**What this proves.** A customer whose node is clear and whose line passes its test is sent steps from the guide for the router model on their service record, and from the section that fits what they described.

**Target.** No brief target. Every answer from the customer's own router model. Right section for at least 70.0% of the independent descriptions (set before they were scored), and no regression on the two in-sample sets (98.0% and 80.0%).

**Result (met).** Independent descriptions, scored once on a retriever built without sight of them: right section for 468 of 576 (81.25%; always answering 'no-internet' would score 47.92%). The retriever has been changed since; today the same descriptions score 89.58%, which is no longer an out-of-sample figure. In-sample: 99.07% of the transcript phrases and 96.67% of the author's paraphrases. Answers from another model's guide: 0.

Written by the run of 2026-11-16T01:55:17+00:00. Measured on synthetic stand-in data (assumption A1); not a measurement of a real network or of real customers. Language-model provider: none (no language model takes part in this measurement).

## By set

| Set | Descriptions x models | Right section | Always 'no-internet' would score |
|---|---|---|---|
| Transcript phrases (in-sample) | 5184 | 5136 (99.07%) | 81.48% |
| Author's paraphrases (in-sample) | 360 | 348 (96.67%) | 50.0% |
| Independent descriptions (retriever changed since first scored) | 576 | 516 (89.58%) | 47.92% |

## Independent descriptions that were sent a section outside the accepted ones

| Description | Accepted | Sent instead (times, over all models) |
|---|---|---|
| Net was acting weird so I switched the router off. How long to wait after turning it on? | power-cycle | no-internet (12) |
| Where is the WPS button on this router? My printer needs it to connect. | wifi | no-internet (12) |
| connection is not down, it's just crawling, a 1 GB file shows 4 hours | slow-speed | no-internet (12) |
| new phone says incorrect pasword even though i am typing same as on the sticker | wifi | no-internet (12) |
| video calls keep freezing and my voice breaks in every office meeting | slow-speed | no-internet (12) |

## In-sample descriptions that were sent a section outside the accepted ones

| Description | Accepted | Sent instead (times, over all models and phrasings) |
|---|---|---|
| connection keeps going on and off | no-internet, power-cycle | wifi (24) |
| the router lights are blinking orange | no-internet, power-cycle | wifi (24) |
| streaming is choppy in the evenings | slow-speed | no-internet (12) |

## What this rests on

- Only the independent set says anything about descriptions the retriever was not built on, and it is 48 descriptions by one other author: another model instance, shown one router guide and none of the retriever. Its vocabulary may still be closer to the rule author's than a customer's is. Real contacts would be the real test.
- An out-of-sample figure can be had once. The file of cases records the figure as first scored and a hash of what it was scored on (the retriever's code, the guides, the router models); when any of that changes, this report says so and stops calling the set out-of-sample.
- The symptom rules were reworked after the first scoring, on what three code reviews found by reading them: a fault the customer rules out is not the fault (and is kept from the search as well), a word like 'dead' or 'gone' counts only when said of the service, and the service named as gone outranks a mention of Wi-Fi.
- One change was prompted by this set itself: the Hindi 'lagta hai' (it seems) was read as 'lag'. No word was added to the rules from the descriptions this set got wrong.
- The section is chosen mainly by keyword rules for the kind of fault (`rag/symptoms.py`), which know only the words they were given; the search contributes when the rules recognise nothing. The index uses a deterministic hashing embedder, not a learned model (A5, D2).
- The labels accept two sections where a careful agent could send either, and most faults are no-service faults: the constant-answer column shows how much of each figure that alone explains.
- Each description is put to the agent on its own. In a conversation the agent sees everything the customer wrote and goes by the latest message that names a fault.
- A wrong section is still a set of safe steps from the customer's own router's guide. If they do not help, the customer says so and a person takes over.
