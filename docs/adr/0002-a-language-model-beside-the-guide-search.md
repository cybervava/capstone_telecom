# ADR 0002: A language model beside the guide search, and what it may add to an answer

Status: accepted, 25 November 2026, after the project review. Stand-in data and synthetic router guides; see assumption A1 of the execution plan.

## Context

The brief's third agent is the "Troubleshooting Agent (RAG) — device-specific steps only when there is no known outage". As built until now it was retrieval without generation: a search over the indexed guides found the section for the customer's own router, and the reply quoted its steps word for word with their source. No language model took part. That was deliberate (a model that writes repair steps can write wrong ones), and the project review asked the fair question whether a retrieval flow without a model call is making use of what a model is good at.

It was not. The measurement was already in the repository. Which section is found rests mainly on keyword rules for the kind of fault (`rag/symptoms.py`), and on 48 descriptions written without sight of them they chose the right section for 89.58%. The five they missed are sentences any reader places at once: "video calls keep freezing and my voice breaks in every office meeting" is slow speed, and the rules, who know none of those words, send the section for a line that is down.

## Decision

A model reads; it does not write. Retrieval stays what it was, a model is asked one question about what retrieval found, and code checks the answer before anything is said.

1. **Retrieve.** The search hands on up to four sections of the guide for the router on the customer's service record: the role filter, the list of sections a customer may follow and the check of each passage are conditions as before. Today four is every such section of a guide.
2. **Ask.** The model is shown the customer's words, masked by shape and by what the CRM knows to be theirs, and those sections, each wrapped as data. It answers in a closed shape of three strings: the id of one section, the customer's words that describe the fault, and the sentence of the section that says what it is for.
3. **The model can only choose among what was found.** A section id that was not offered is not taken from it; the search's own choice stands and the answer is recorded as refused. It cannot reach another router's guide, the factory reset, or anything that is not in the index.
4. **The model points; the platform quotes.** Each of the two passages is looked for in the text it claims to come from. What is used is that text's own clause or sentence, whole and in its own characters, so a model cannot cut "working fine" out of "not working fine" or an instruction out of the sentence that forbids it. A passage that is not there is left out.
5. **What is said back is checked for what it says.** A customer's clause is repeated only if it is made of plain characters (no quotation mark that would end the quotation early, no markup, no look-alike letter), and names no personal data, money, promise, request, hazard or state of the network. A guide's sentence is checked like a step is.
6. **The steps are never the model's.** The reply is the template it was: steps word for word from the index, guide, section, version, and now up to two quotations before them.
7. **Without a model, nothing changes.** With the offline provider (tests, CI, anyone without a key), when the model fails or is slow, when the profile needed for masking cannot be read, and while the assistant is behind, the search and the rules choose as before. The question is asked at most once in a conversation, because steps are given once.
8. **It is on the record.** The audit trail has what the search found with its scores, who chose and what the rules alone would have chosen, the model and the prompt version, and a flag whenever something the model offered was left out. Neither the customer's words nor the model's are copied into it.

## What was decided against

| Alternative | Why not |
|---|---|
| Let the model write the answer from the retrieved section (retrieval-augmented generation in the usual sense) | The answer tells a customer what to do to equipment in their home. A paraphrase can drop the "do not" of "do not unplug the fibre cable", and no check of a free sentence against its source is exact. Every claim would need a second model to judge it, and that judge can be talked round too |
| Let the model write one free sentence of its own beside the quoted steps | A list of forbidden words does not make a sentence true. A check that every word of it occurs in a source rejects most honest sentences, and still passes one that is built from the source's own words to say the opposite |
| Give the model the search as a tool and let it look for itself | It would choose the router model and the query. Which guide is searched must come from the service record, and a model with a tool is a model whose mistakes act |
| Replace the keyword rules with the model | The rules cost nothing, need no key and are the fallback whenever the model does not answer. They also give the search its first choice, which the model is measured against |
| A learned embedding model instead | It improves which sections are found, not which one is chosen among four, and it needs a fresh out-of-sample set and a key on every machine that builds the index. The index still records its embedder and refuses a query from another |

## What it costs

- One more model call in a conversation that reaches guide steps: about 1.4 s at the median with `gpt-5.4-mini` (limit 8 s), and one more read of the customer's profile, so that their words can be masked by value. Messages already cost one call each.
- Up to the last 2,000 characters of what the customer wrote go to the model provider with this question, masked. What no masking can know (a relative's name, a street the CRM was never told) can be in them, as in every message the model reads.
- A wrong choice by the model is still a set of safe steps from the customer's own guide. If they do not help, the customer says so and a person takes over, as before.
- A customer's clause that is said back is still words a customer chose. The checks are lists of what has no place in an answer, not a proof; what passes is shown in quotation marks, as theirs, to the customer who wrote it.
- When a turn runs twice, the model is not asked again: what was sent is read back from the stored message. A model's choice is therefore not repeatable, and it is not replayed.

## Not built

- The model does not choose among guides, only among sections of one. Nothing asks it about the credit policy or the complaint rules: those documents are in the knowledge pack and are not indexed.
- Groundedness is checked in code for every answer and counted in the audit trail; it is not on a dashboard (M5).
- The measurement with a live model covers three of the twelve router models and one author's descriptions.

## How it is shown

| Claim | Evidence |
|---|---|
| The model chooses better than the rules | `evals/reports/retrieval_openai.md` (25 Nov, `gpt-5.4-mini-2026-03-17`): the right section for 144 of 144 independent descriptions on three router models, against 129 of 144 (89.58%) by the search and the rules alone; 1.4 s at the median, 3.2 s at the slowest. All 144 guide sentences it pointed at were found and quoted, and 117 of its 144 pointers into the customer's words; the rest were left out, each with its reason |
| Nothing a model writes is sent; a passage is a whole clause or sentence of its source | `tests/unit/guardrails/test_grounding.py`; `tests/unit/agents/test_troubleshooting.py` |
| A model that is talked round achieves nothing | The attack suite's family K (`evals/reports/attack_suite.md`): sentences of its own as quotations, a planted sentence said back, a section it was not shown. `scripts/attack_suite_can_fail.py` takes the lookup and the checks out and the attacks report it |
| No detail of the customer reaches the model with this question | `tests/unit/llm/test_model_payloads.py` (300 customers, both providers, both questions); attack G1 |
| A failed or slow model costs the customer nothing; a turn that runs twice sends one message | `tests/e2e/test_guided_troubleshooting.py`; `tests/unit/llm/test_guide_answers.py` |
