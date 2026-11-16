"""Troubleshooting Agent: the right steps from the right guide, quoted and cited.

It only speaks when the evidence allows device guidance at all (the node is confirmed clear and
the customer's line passed its test; routing decides that, not this agent). What it says is
taken verbatim from the guide for the customer's own router model, never generated, so every
step can be traced to a guide, a version and a section. The model is a hard filter taken from
the service record: what the customer writes can change which section is found, never which
guide is used. Which section is found rests first on the kind of fault the customer's words
describe (``rag.symptoms``), then on the words they share with the guide.

Only the sections listed in ``rag.documents.SELF_SERVICE_SECTIONS`` are ever quoted, and the
index build refuses a guide whose quotable steps tell the customer to do something hazardous.
"""

from __future__ import annotations

from dataclasses import dataclass

from skylink_care.guardrails.pii import mask_pii
from skylink_care.observability.audit import Auditor
from skylink_care.rag.documents import SELF_SERVICE_SECTIONS, not_a_step, not_a_title
from skylink_care.rag.index import GuideIndex, GuideIndexUnavailableError, Passage
from skylink_care.rag.symptoms import GENERAL_SECTION, latest_hint, without_what_is_ruled_out

ACTOR = "agent:troubleshooting"
CUSTOMER_ROLE = "customer"
# How much the kind of fault read from the customer's words counts against shared words
# (a section whose title the words name scores 1): decisive when a kind was recognised, a
# nudge towards the general section when none was.
HINT_WEIGHT = 3.0
DEFAULT_WEIGHT = 0.5


@dataclass(frozen=True)
class Guidance:
    passage: Passage
    index_version: str


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
    def __init__(self, index: GuideIndex) -> None:
        self._index = index

    async def guide(
        self, *, router_model: str | None, problem: str, audit: Auditor
    ) -> Guidance | None:
        """The guide section that fits the customer's problem, or None when there is none.

        None is an answer, not an error: no router on record, no index, or no section with
        steps. The caller then hands over instead of improvising.
        """
        if not router_model:
            await self._note(audit, "NO_ROUTER_MODEL", router_model, None)
            return None
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
                limit=1,
                prefer=hint or GENERAL_SECTION,
                preference=HINT_WEIGHT if hint else DEFAULT_WEIGHT,
            )
            info = await self._index.info()
        except GuideIndexUnavailableError as exc:
            await self._note(audit, "INDEX_UNAVAILABLE", router_model, None, error=str(exc))
            return None
        usable = [passage for passage in passages if passage.steps]
        if not usable or info is None:
            await self._note(audit, "NO_GUIDE_FOR_MODEL", router_model, None)
            return None
        refused = _not_quotable(usable[0], router_model)
        if refused is not None:
            # What the index holds is not taken on trust. A person takes over, and the trail
            # says which passage was stopped and why.
            await self._note(audit, "PASSAGE_REFUSED", router_model, usable[0], error=refused)
            return None
        await self._note(audit, "OK", router_model, usable[0], index_version=info.index_version)
        return Guidance(usable[0], info.index_version)

    @staticmethod
    async def _note(
        audit: Auditor,
        outcome: str,
        router_model: str | None,
        passage: Passage | None,
        **details: str,
    ) -> None:
        await audit.record(
            actor=ACTOR,
            step="troubleshooting.retrieve",
            outcome=outcome,
            # The customer's words are not copied into the trail; what was retrieved is.
            inputs={"router_model": router_model, "sections": list(SELF_SERVICE_SECTIONS)},
            outputs={
                "citation": passage.citation if passage else None,
                "score": round(passage.score, 4) if passage else None,
                **details,
            },
        )
