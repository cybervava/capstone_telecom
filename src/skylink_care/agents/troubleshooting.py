"""Troubleshooting Agent: the right steps from the right guide, quoted and cited.

It only speaks when the evidence allows device guidance at all (the node is confirmed clear and
the customer's line passed its test; routing decides that, not this agent). What it says is
taken verbatim from the guide for the customer's own router model, never generated, so every
step can be traced to a guide, a version and a section. The model is a hard filter taken from
the service record: what the customer writes can change which section is found, never which
guide is used.

Only the sections listed in ``rag.documents.SELF_SERVICE_SECTIONS`` are ever quoted, and the
index build refuses a guide whose quotable steps tell the customer to do something hazardous.
"""

from __future__ import annotations

from dataclasses import dataclass

from skylink_care.guardrails.pii import mask_pii
from skylink_care.observability.audit import Auditor
from skylink_care.rag.documents import SELF_SERVICE_SECTIONS
from skylink_care.rag.index import GuideIndex, GuideIndexUnavailableError, Passage

ACTOR = "agent:troubleshooting"
CUSTOMER_ROLE = "customer"


@dataclass(frozen=True)
class Guidance:
    passage: Passage
    index_version: str


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
        try:
            passages = await self._index.search(
                query,
                device_model=router_model,
                role=CUSTOMER_ROLE,
                sections=SELF_SERVICE_SECTIONS,
                limit=1,
            )
            info = await self._index.info()
        except GuideIndexUnavailableError as exc:
            await self._note(audit, "INDEX_UNAVAILABLE", router_model, None, error=str(exc))
            return None
        usable = [passage for passage in passages if passage.steps]
        if not usable or info is None:
            await self._note(audit, "NO_GUIDE_FOR_MODEL", router_model, None)
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
