"""Troubleshooting Agent: the right steps from the right guide, quoted and cited.

It only speaks when the evidence allows device guidance at all (the node is confirmed clear and
the customer's line passed its test; routing decides that, not this agent). What it says is
taken verbatim from the guide for the customer's own router model, never generated, so every
step can be traced to a guide, a version and a section. The model is a hard filter taken from
the service record: what the customer writes can change which section is found, never which
guide is used.

Which section is found is retrieval and then, where a language model is configured, a reading:

1. The search (``rag.index``) finds the sections of that guide a customer may follow, ranked
   by the kind of fault the customer's words describe (``rag.symptoms``) and by the words they
   share with the guide. Its first section is the answer when no model is asked.
2. A model is shown the customer's masked words beside the sections found, and names the one
   that fits. It can only choose among what was found: a section it was not shown is not
   taken from it. It also points to the customer's own description of the fault and to the
   sentence of the section that says what it is for; each is used only if it is found, word
   for word, where the model says it is (``guardrails.grounding``).

Only the sections listed in ``rag.documents.SELF_SERVICE_SECTIONS`` are ever quoted, and the
index build refuses a guide whose quotable steps tell the customer to do something hazardous.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from skylink_care.guardrails.grounding import Quoted, quote_from_customer, quote_from_guide
from skylink_care.guardrails.pii import mask_pii
from skylink_care.llm.base import CallRecord, GuideSection
from skylink_care.llm.gateway import LLMGateway
from skylink_care.observability.audit import Auditor
from skylink_care.rag.documents import SELF_SERVICE_SECTIONS, not_a_step, not_a_title
from skylink_care.rag.index import GuideIndex, GuideIndexUnavailableError, Passage
from skylink_care.rag.symptoms import GENERAL_SECTION, latest_hint, without_what_is_ruled_out

ACTOR = "agent:troubleshooting"
GUARDRAIL = "guardrail:output"
CUSTOMER_ROLE = "customer"
# How many sections the search hands on: the rules' own choice first, then the next best. A
# model chooses among these and no others. Today a guide has four sections a customer may
# follow, so a model sees all of them, ranked; the number bounds the question as guides grow.
CANDIDATES = 4
# Who chose the section that is quoted.
RULES, MODEL = "rules", "model"
# How much the kind of fault read from the customer's words counts against shared words
# (a section whose title the words name scores 1): decisive when a kind was recognised, a
# nudge towards the general section when none was.
HINT_WEIGHT = 3.0
DEFAULT_WEIGHT = 0.5

# What is to be masked by value before a model reads the customer's words, read when it is
# needed. None: it could not be read, and the words stay inside.
KnownValues = Callable[[], Awaitable[Mapping[str, str] | None]]


@dataclass(frozen=True)
class Guidance:
    passage: Passage
    index_version: str
    # Who chose the section among those the search found.
    chosen_by: str = RULES
    # What a model pointed to, each found word for word in its source: the customer's own
    # description of the fault, and the section's sentence that says what it is for.
    customer_words: str | None = None
    guide_words: str | None = None


@dataclass(frozen=True)
class Retrieved:
    """What the search found for a router and a problem, before anything is said.

    ``outcome`` is ``OK`` when there is something to quote. Otherwise it says why not, in the
    word the audit trail records: ``NO_ROUTER_MODEL``, ``INDEX_UNAVAILABLE``,
    ``NO_GUIDE_FOR_MODEL`` (nothing with steps came first) or ``PASSAGE_REFUSED`` (what came
    first may not be quoted).
    """

    outcome: str
    # Every section found that has steps, best first.
    found: tuple[Passage, ...] = ()
    # Of those, the ones that may be quoted. The first is the choice of the search and the
    # rules; a model, or a person, chooses among these and no others.
    offered: tuple[Passage, ...] = ()
    index_version: str = ""
    error: str = ""


def _not_quotable(passage: Passage, router_model: str) -> str | None:
    """Why this passage may not be quoted to the customer, or None.

    The search already asks for the customer's own router and the sections a customer may
    follow, and the index build already refused unsafe steps. This is the second lock, at the
    point where the words leave: a fault in the search, or a row changed after the build,
    must not reach a customer as the guide's own advice.
    """
    if passage.device_model != router_model:
        return f"it is from the guide of {passage.device_model}, not of the customer's router"
    if passage.section_id not in SELF_SERVICE_SECTIONS:
        return f"section {passage.section_id!r} is not for a customer to follow alone"
    # The answer names the guide and the section: they are sent like the steps are.
    named = not_a_title(passage.title.replace(":", " ", 1))
    if named is not None:
        return f"its title {named}"
    for number, step in enumerate(passage.steps, start=1):
        problem = not_a_step(step)
        if problem is not None:
            return f"step {number} {problem}"
    return None


class TroubleshootingAgent:
    def __init__(
        self,
        index: GuideIndex,
        llm: LLMGateway | None = None,
        *,
        behind: Callable[[], bool] | None = None,
    ) -> None:
        self._index = index
        # The door to a language model, when one may be asked which section fits.
        self._llm = llm
        # Whether the orchestrator is behind (surge mode): the model is then not asked.
        self._behind = behind

    async def guide(
        self,
        *,
        router_model: str | None,
        problem: str,
        audit: Auditor,
        known_values: KnownValues | None = None,
    ) -> Guidance | None:
        """The guide section that fits the customer's problem, or None when there is none.

        None is an answer, not an error: no router on record, no index, or no section with
        steps. The caller then hands over instead of improvising.

        ``known_values`` reads what must be masked by value before a model sees the
        customer's words. Without it no model is asked.
        """
        retrieved = await self.retrieve(router_model=router_model, problem=problem)
        if retrieved.outcome != "OK":
            # What the index holds is not taken on trust, and a missing answer is not made
            # up. A person takes over, and the trail says what was stopped and why.
            stopped = retrieved.found[0] if retrieved.found else None
            await self._note(audit, retrieved.outcome, router_model, stopped, error=retrieved.error)
            return None
        first = retrieved.offered[0]
        await self._note(
            audit,
            "OK",
            router_model,
            first,
            found=retrieved.found,
            index_version=retrieved.index_version,
        )
        by_the_rules = Guidance(first, retrieved.index_version)
        if self._llm is None or known_values is None or not self._llm.answers_from_guides:
            return by_the_rules
        return await self._ask_a_model(
            self._llm, by_the_rules, retrieved.offered, problem, audit, known_values
        )

    async def retrieve(self, *, router_model: str | None, problem: str) -> Retrieved:
        """Search the guide of this router for the sections that fit the problem.

        The retrieval on its own, for whoever needs what was found and not a conversation's
        answer: ``guide`` above, and the tool that lets an MCP client search a guide. It
        records nothing; the caller says in its own trail what it asked and what it got.
        """
        if not router_model:
            return Retrieved("NO_ROUTER_MODEL")
        query = mask_pii(problem).text
        # The kind of fault the customer's latest words describe decides more than shared
        # words do; with none recognised, the section that tells line from router is preferred.
        hint = latest_hint(query)
        try:
            passages = await self._index.search(
                without_what_is_ruled_out(query),
                device_model=router_model,
                role=CUSTOMER_ROLE,
                sections=SELF_SERVICE_SECTIONS,
                limit=CANDIDATES,
                prefer=hint or GENERAL_SECTION,
                preference=HINT_WEIGHT if hint else DEFAULT_WEIGHT,
            )
            info = await self._index.info()
        except GuideIndexUnavailableError as exc:
            return Retrieved("INDEX_UNAVAILABLE", error=str(exc))
        if not passages or not passages[0].steps or info is None:
            # The best section the search found has nothing to follow: there is no answer
            # here, and the next best is not passed off as one.
            return Retrieved("NO_GUIDE_FOR_MODEL")
        first, usable = passages[0], tuple(passage for passage in passages if passage.steps)
        refused = _not_quotable(first, router_model)
        if refused is not None:
            return Retrieved("PASSAGE_REFUSED", found=(first,), error=refused)
        # Only what could be quoted is handed on: nobody can be led to a passage that the
        # second lock would stop.
        offered = (first, *(p for p in usable[1:] if _not_quotable(p, router_model) is None))
        return Retrieved("OK", usable, offered, info.index_version)

    async def _ask_a_model(
        self,
        llm: LLMGateway,
        by_the_rules: Guidance,
        offered: Sequence[Passage],
        problem: str,
        audit: Auditor,
        known_values: KnownValues,
    ) -> Guidance:
        """What a model makes of the sections that were found. Whatever it answers, the
        customer gets steps from one of them; the rules' choice stands unless the model names
        another that was offered."""
        behind = self._behind() if self._behind else False
        # While the platform is behind the model is not asked, so nothing is read for it.
        values = {} if behind else await known_values()
        if values is None:
            await self._note_answer(
                audit, "NOT_ASKED", by_the_rules, by_the_rules, offered, why="PROFILE_UNAVAILABLE"
            )
            return by_the_rules
        reading = await llm.answer_from_guide(
            problem,
            [GuideSection(p.section_id, _section_title(p), p.body) for p in offered],
            known_values=values,
            shed_load=behind,
            correlation_id=audit.correlation_id,
        )
        if reading.call is None:
            return by_the_rules
        if reading.answer is None:
            await self._note_answer(
                audit, "DEGRADED", by_the_rules, by_the_rules, offered, call=reading.call
            )
            return by_the_rules
        chosen = next((p for p in offered if p.section_id == reading.answer.section_id), None)
        if chosen is None:
            # A section the model was not shown: of another guide, of no guide, or one that
            # is not for a customer to follow. Nothing of this answer is used.
            await self._note_answer(
                audit, "REFUSED", by_the_rules, by_the_rules, offered, call=reading.call
            )
            await self._flag(audit, reading.call, len(offered), "NOT_OFFERED", None, None)
            return by_the_rules
        customer = quote_from_customer(reading.shown, reading.answer.customer_words)
        guide = quote_from_guide(chosen.body, reading.answer.guide_words)
        answered = Guidance(chosen, by_the_rules.index_version, MODEL, customer.text, guide.text)
        await self._note_answer(
            audit,
            "OK",
            answered,
            by_the_rules,
            offered,
            call=reading.call,
            customer=customer,
            guide=guide,
        )
        if customer.refused or guide.refused:
            await self._flag(audit, reading.call, len(offered), "OFFERED", customer, guide)
        return answered

    @staticmethod
    async def _note(
        audit: Auditor,
        outcome: str,
        router_model: str | None,
        passage: Passage | None,
        *,
        found: Sequence[Passage] = (),
        index_version: str = "",
        error: str = "",
    ) -> None:
        details = {"index_version": index_version, "error": error}
        await audit.record(
            actor=ACTOR,
            step="troubleshooting.retrieve",
            outcome=outcome,
            # The customer's words are not copied into the trail; what was retrieved is.
            inputs={"router_model": router_model, "sections": list(SELF_SERVICE_SECTIONS)},
            outputs={
                "citation": passage.citation if passage else None,
                "score": round(passage.score, 4) if passage else None,
                # Everything the search found, best first: what a model then chooses among.
                "found": [
                    {"citation": each.citation, "score": round(each.score, 4)} for each in found
                ],
                **{name: value for name, value in details.items() if value},
            },
        )

    @staticmethod
    async def _note_answer(
        audit: Auditor,
        outcome: str,
        used: Guidance,
        by_the_rules: Guidance,
        offered: Sequence[Passage],
        *,
        call: CallRecord | None = None,
        why: str | None = None,
        customer: Quoted | None = None,
        guide: Quoted | None = None,
    ) -> None:
        """Who chose the section, among which, and what became of the words a model pointed
        to. Neither the customer's words nor the model's are copied into the trail."""
        await audit.record(
            actor=ACTOR,
            step="troubleshooting.answer",
            outcome=outcome,
            inputs={
                "sections_shown": [each.citation for each in offered],
                "provider_error": call.error if call else why,
            },
            outputs={
                "section": used.passage.citation,
                "chosen_by": used.chosen_by,
                "rules_chose": by_the_rules.passage.citation,
                "customer_words": _what_became_of(customer),
                "guide_words": _what_became_of(guide),
            },
            call=call,
        )

    @staticmethod
    async def _flag(
        audit: Auditor,
        call: CallRecord,
        shown: int,
        section: str,
        customer: Quoted | None,
        guide: Quoted | None,
    ) -> None:
        """An answer, or a part of one, that its sources do not bear out. It was not sent;
        that it was offered is counted."""
        await audit.record(
            actor=GUARDRAIL,
            step="guardrail.output",
            outcome="UNGROUNDED",
            inputs={"read_by": call.provider, "sections_shown": shown},
            outputs={
                "section": section,
                "customer_words": _what_became_of(customer),
                "guide_words": _what_became_of(guide),
            },
        )


def _section_title(passage: Passage) -> str:
    """The section's own title: the index keeps "<guide>: <section>" as one."""
    return passage.title.split(":", 1)[-1].strip()


def _what_became_of(quoted: Quoted | None) -> str:
    if quoted is None:
        return "NOT_USED"
    if quoted.text is not None:
        return "QUOTED"
    return quoted.refused or "NONE"
