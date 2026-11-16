"""Evaluation: does a customer get the right section of the guide for their own router.

Three sets of descriptions are put to the Troubleshooting Agent for every router model that
has a guide, and the section it answers with is scored against labels made by hand:

* every way a broadband fault is described in the synthetic transcripts, English and Hinglish,
  alone and with the greetings and remarks customers add. The retriever's symptom rules were
  written by someone who had read these: this set is in-sample;
* paraphrases by the same author, written just before the rules: in-sample as well;
* descriptions written and labelled by someone who was shown one router guide and nothing of
  the retriever, after the rules were final. This is the only out-of-sample set.

Next to each figure stands what a constant answer ("always the No internet section") would
score, because most faults are no-service faults and the labels accept that section often.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from itertools import cycle
from pathlib import Path
from typing import Any

from evals.report import Report, frozen, percent
from skylink_care.agents.troubleshooting import TroubleshootingAgent
from skylink_care.db.audit import AuditRepository
from skylink_care.db.database import Database
from skylink_care.observability.audit import Auditor
from skylink_care.rag.embedding import HashingEmbedder
from skylink_care.rag.index import GuideIndex
from skylink_datagen.phrases import BROADBAND_SYMPTOMS, CONNECTIVITY_TAILS, GREETINGS

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "data" / "knowledge"
# The brief names no target for retrieval. The first two are regression floors set from a
# measured run; the third was fixed before the independent set had been scored at all.
MIN_RIGHT_ON_TRANSCRIPT_PHRASES = 98.0
MIN_RIGHT_ON_AUTHOR_PARAPHRASES = 80.0
MIN_RIGHT_ON_INDEPENDENT = 70.0
CONSTANT_ANSWER = "no-internet"


@dataclass
class Tally:
    right: int = 0
    wrong: int = 0
    unanswered: int = 0
    other_model: int = 0
    misses: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.right + self.wrong + self.unanswered

    @property
    def right_percent(self) -> float:
        return percent(self.right, self.total)


def with_remarks(symptom: str) -> list[str]:
    """The symptom on its own, and with each remark customers add, behind a greeting."""
    greetings = cycle(greeting for greeting in GREETINGS if greeting)
    tails = [tail for tail in CONNECTIVITY_TAILS if tail]
    return [symptom, *(f"{next(greetings)}, {symptom} {tail}" for tail in tails)]


async def score(
    agent: TroubleshootingAgent,
    audit: Auditor,
    models: list[str],
    gold: dict[str, list[str]],
    *,
    remarks: bool,
) -> Tally:
    tally = Tally()
    for model in models:
        for symptom, accepted in gold.items():
            for text in with_remarks(symptom) if remarks else [symptom]:
                guidance = await agent.guide(router_model=model, problem=text, audit=audit)
                if guidance is None:
                    tally.unanswered += 1
                    continue
                passage = guidance.passage
                tally.other_model += passage.device_model != model
                if passage.section_id in accepted:
                    tally.right += 1
                else:
                    tally.wrong += 1
                    sent = tally.misses.setdefault(symptom, {})
                    sent[passage.section_id] = sent.get(passage.section_id, 0) + 1
    return tally


def missed_rows(tally: Tally, gold: dict[str, list[str]]) -> list[list[str]]:
    rows = [
        [symptom, ", ".join(gold[symptom]), ", ".join(f"{s} ({n})" for s, n in sent.items())]
        for symptom, sent in sorted(tally.misses.items())
    ]
    return rows or [["none", "", ""]]


def constant_baseline(gold: dict[str, list[str]]) -> float:
    """What answering every description with the same section would score."""
    return percent(sum(CONSTANT_ANSWER in accepted for accepted in gold.values()), len(gold))


def fingerprint(patterns: list[str]) -> str:
    """One hash over the paths and contents of the files the patterns name, in path order."""
    digest = hashlib.sha256()
    for path in sorted(found for pattern in patterns for found in ROOT.glob(pattern)):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def retriever_unchanged(first: dict[str, Any]) -> bool:
    """True while everything that decides the answer (the retriever's code, the guides, the
    router models) is byte for byte what it was when the independent descriptions were first
    scored. The report lists the descriptions that went wrong, so whoever changes any of it
    after that has seen them: from then on the set is in-sample."""
    return fingerprint(first["on"]["files"]) == str(first["on"]["sha256"])


@dataclass(frozen=True)
class Scored:
    title: str
    gold: dict[str, list[str]]
    tally: Tally


@dataclass(frozen=True)
class Measured:
    known: Scored
    own: Scored
    independent: Scored
    # The independent set as first scored, and whether the retriever is still the one it was
    # scored on.
    first: dict[str, Any]
    unseen: bool
    # What has been changed since the first scoring, as recorded with the cases.
    changed_since: tuple[str, ...]
    models: int

    @property
    def sets(self) -> tuple[Scored, Scored, Scored]:
        return (self.known, self.own, self.independent)

    @property
    def other_model(self) -> int:
        return sum(scored.tally.other_model for scored in self.sets)

    @property
    def met(self) -> bool:
        return (
            self.other_model == 0
            and self.known.tally.right_percent >= MIN_RIGHT_ON_TRANSCRIPT_PHRASES
            and self.own.tally.right_percent >= MIN_RIGHT_ON_AUTHOR_PARAPHRASES
            and self.first["right_percent"] >= MIN_RIGHT_ON_INDEPENDENT
            and self.independent.tally.right_percent >= MIN_RIGHT_ON_INDEPENDENT
        )


def headline(measured: Measured) -> str:
    first, now = measured.first, measured.independent.tally
    out_of_sample = (
        f"Independent descriptions, scored once on a retriever built without sight of them:"
        f" right section for {first['right']} of {first['of']} ({first['right_percent']}%; always"
        f" answering '{CONSTANT_ANSWER}' would score"
        f" {constant_baseline(measured.independent.gold)}%)."
    )
    since = (
        ""
        if measured.unseen
        else (
            f" The retriever has been changed since; today the same descriptions score"
            f" {now.right_percent}%, which is no longer an out-of-sample figure."
        )
    )
    return (
        f"{out_of_sample}{since} In-sample: {measured.known.tally.right_percent}% of the"
        f" transcript phrases and {measured.own.tally.right_percent}% of the author's"
        f" paraphrases. Answers from another model's guide: {measured.other_model}."
    )


def assumptions(measured: Measured) -> list[str]:
    return [
        "Only the independent set says anything about descriptions the retriever was not"
        f" built on, and it is {len(measured.independent.gold)} descriptions by one other"
        " author: another model instance, shown one router guide and none of the retriever."
        " Its vocabulary may still be closer to the rule author's than a customer's is. Real"
        " contacts would be the real test.",
        "An out-of-sample figure can be had once. The file of cases records the figure as"
        " first scored and a hash of what it was scored on (the retriever's code, the guides,"
        " the router models); when any of that changes, this report says so and stops calling"
        " the set out-of-sample.",
        *(() if measured.unseen else measured.changed_since),
        "The section is chosen mainly by keyword rules for the kind of fault"
        " (`rag/symptoms.py`), which know only the words they were given; the search"
        " contributes when the rules recognise nothing. The index uses a deterministic"
        " hashing embedder, not a learned model (A5, D2).",
        "The labels accept two sections where a careful agent could send either, and most"
        " faults are no-service faults: the constant-answer column shows how much of each"
        " figure that alone explains.",
        "Each description is put to the agent on its own. In a conversation the agent sees"
        " everything the customer wrote and goes by the latest message that names a fault.",
        "A wrong section is still a set of safe steps from the customer's own router's"
        " guide. If they do not help, the customer says so and a person takes over.",
    ]


def numbers(measured: Measured) -> dict[str, Any]:
    return {
        "router_models": measured.models,
        "answers_from_another_models_guide": measured.other_model,
        "independent_as_first_scored": measured.first,
        "retriever_unchanged_since_first_scored": measured.unseen,
        **{
            scored.title: {
                "descriptions": scored.tally.total,
                "right_section": scored.tally.right,
                "right_percent": scored.tally.right_percent,
                "wrong_section": scored.tally.wrong,
                "no_answer": scored.tally.unanswered,
                "constant_answer_percent": constant_baseline(scored.gold),
            }
            for scored in measured.sets
        },
        "independent_wrong_by_description": measured.independent.tally.misses,
    }


def the_report(measured: Measured) -> Report:
    report = Report(
        name="retrieval",
        title="The right guide section for the customer's own router",
        proves=(
            "A customer whose node is clear and whose line passes its test is sent steps from"
            " the guide for the router model on their service record, and from the section"
            " that fits what they described."
        ),
        target=(
            "No brief target. Every answer from the customer's own router model. Right section"
            f" for at least {MIN_RIGHT_ON_INDEPENDENT}% of the independent descriptions (set"
            " before they were scored), and no regression on the two in-sample sets"
            f" ({MIN_RIGHT_ON_TRANSCRIPT_PHRASES}% and {MIN_RIGHT_ON_AUTHOR_PARAPHRASES}%)."
        ),
        headline=headline(measured),
        met=measured.met,
        numbers=numbers(measured),
        assumptions=assumptions(measured),
    )
    report.table(
        "By set",
        ["Set", "Descriptions x models", "Right section", "Always 'no-internet' would score"],
        [
            [
                scored.title,
                scored.tally.total,
                f"{scored.tally.right} ({scored.tally.right_percent}%)",
                f"{constant_baseline(scored.gold)}%",
            ]
            for scored in measured.sets
        ],
    )
    report.table(
        "Independent descriptions that were sent a section outside the accepted ones",
        ["Description", "Accepted", "Sent instead (times, over all models)"],
        missed_rows(measured.independent.tally, measured.independent.gold),
    )
    report.table(
        "In-sample descriptions that were sent a section outside the accepted ones",
        ["Description", "Accepted", "Sent instead (times, over all models and phrasings)"],
        [
            *missed_rows(measured.known.tally, measured.known.gold),
            *missed_rows(measured.own.tally, measured.own.gold),
        ],
    )
    return report


async def test_customers_get_the_right_section_of_their_own_routers_guide(db: Database) -> None:
    cases = frozen("retrieval_gold.json")
    known: dict[str, list[str]] = cases["symptoms"]
    own: dict[str, list[str]] = cases["author_paraphrases"]["symptoms"]
    independent: dict[str, list[str]] = cases["independent"]["symptoms"]
    assert set(known) == set(BROADBAND_SYMPTOMS), "every symptom of the transcripts is labelled"
    models = [
        item["model_id"]
        for item in json.loads((KNOWLEDGE / "router_models.json").read_text(encoding="utf-8"))
        if (KNOWLEDGE / "router_guides" / f"{item['model_id']}.md").is_file()
    ]
    agent = TroubleshootingAgent(GuideIndex(db, HashingEmbedder()))
    audit = Auditor(AuditRepository(db), contact_id=None, correlation_id="eval-retrieval")
    unseen = retriever_unchanged(cases["independent"]["first_scored"])

    measured = Measured(
        known=Scored(
            "Transcript phrases (in-sample)",
            known,
            await score(agent, audit, models, known, remarks=True),
        ),
        own=Scored(
            "Author's paraphrases (in-sample)",
            own,
            await score(agent, audit, models, own, remarks=False),
        ),
        independent=Scored(
            "Independent descriptions"
            + (" (out-of-sample)" if unseen else " (retriever changed since first scored)"),
            independent,
            await score(agent, audit, models, independent, remarks=False),
        ),
        first=cases["independent"]["first_scored"],
        unseen=unseen,
        changed_since=tuple(cases["independent"].get("changed_since", ())),
        models=len(models),
    )
    report = the_report(measured)
    report.write()

    now = measured.independent.tally
    if unseen:
        # The figure kept with the cases is the one this code computes on these rules.
        assert (now.right, now.total) == (measured.first["right"], measured.first["of"])
    assert measured.other_model == 0
    assert measured.met, report.headline
