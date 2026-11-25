"""What an MCP client may ask of the platform: four questions, each answered by the rule the
platform's own agents go by.

A client of the Model Context Protocol is somebody else's assistant: a staff member's desktop
assistant, an IDE, another agent. It is given no way to act and nothing of a customer:

* ``check_network_status`` is the three-state network decision for one node, on a status read
  now through the same circuit breaker. A status that cannot be read is unknown, and the answer
  says in words that this is not an all-clear;
* ``search_router_guide`` is the retrieval of the Troubleshooting Agent: the sections of one
  router's guide that a customer may follow, with their steps word for word and their source;
* ``quote_service_credit`` is the credit engine on figures the client gives. It quotes and
  applies nothing: only the executor writes to Billing, after the approval an amount needs;
* ``platform_status`` says how the platform itself is doing: the NOC feed, the queue for the
  orchestrator, the guide index.

Every argument is typed and bounded where the server declares the tool (``server.py``), all
callers together are limited to so many calls a minute, and every call that reaches a tool is
in the audit trail with what was asked (never the words of a problem description) and how it
ended. Of the calls refused for the limit, the first of a minute is.

This module knows nothing of the protocol. It can be called and tested as plain Python; the
server module wraps it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict

from skylink_care.adapters.gateway import OssGateway
from skylink_care.agents.diagnostics import DiagnosticsAgent
from skylink_care.agents.troubleshooting import TroubleshootingAgent
from skylink_care.api.ratelimit import RateLimiter, per_minute
from skylink_care.clock import Clock, utc_now
from skylink_care.config import MissingSecretError, Settings
from skylink_care.db.audit import AuditRepository
from skylink_care.db.database import Database
from skylink_care.db.read_model import ReadModelRepository
from skylink_care.db.reference import ReferenceRepository
from skylink_care.domain.credit import (
    CreditInput,
    CreditPolicy,
    approval_level,
    evaluate_credit,
    load_policy,
)
from skylink_care.domain.models import NetworkState, OutageInfo, ServiceType
from skylink_care.domain.network import NetworkDecision
from skylink_care.observability.audit import Auditor
from skylink_care.rag.embedding import HashingEmbedder
from skylink_care.rag.index import GuideIndex
from skylink_care.surge import BacklogUnknownError, SurgeGauge, build_gauge

log = structlog.get_logger(__name__)
# Who a call is recorded under. There is one key for all clients, so one name.
ACTOR = "mcp:client"
# The limiter counts every client together: the key says so.
ALL_CLIENTS = "all"
MEANING = {
    NetworkState.ACTIVE: (
        "An outage is confirmed on this node. Restarting or replacing a customer's equipment,"
        " or a technician visit, would not help while it lasts."
    ),
    NetworkState.CONFIRMED_CLEAR: (
        "No outage has been reported for this node as of the evidence time. That says nothing"
        " about one customer's own line, which only a line test can."
    ),
    NetworkState.UNKNOWN: (
        "The status of this node could not be verified. This is not an all-clear: nobody may"
        " be told that the network is fine on it, and neither steps for a customer's equipment"
        " nor a technician visit rest on it."
    ),
}
QUOTE_NOTE = (
    "A quotation under the policy, on the caller's figures. It assumes an active service on an"
    " affected node and no earlier credit for this outage, and it does not see a credit that is"
    " approved and not yet applied. Nothing is applied: a credit is proposed by the platform"
    " when an outage a customer waited on is resolved, on Billing's own record of the service,"
    " and written by the executor after the approval its amount needs."
)


class Refused(Exception):  # noqa: N818 - an answer to the client, not a fault of ours
    """The question cannot be answered, and the client is told why in these words."""

    def __init__(self, why: str, *, outcome: str = "REFUSED") -> None:
        super().__init__(why)
        self.outcome = outcome


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True)


class OutageView(_Answer):
    outage_id: str
    cause: str
    planned: bool
    started_at: datetime
    eta: datetime | None
    eta_source: str | None


class NetworkStatus(_Answer):
    node_id: str
    state: Literal["ACTIVE", "CONFIRMED_CLEAR", "UNKNOWN"]
    reason: str
    source: str
    decided_at: datetime
    evidence_at: datetime | None
    outage: OutageView | None
    last_known_outage: OutageView | None
    # What may and may not be said on this evidence.
    meaning: str


class GuideSectionFound(_Answer):
    citation: str
    guide: str
    section: str
    steps: list[str]
    score: float


class GuideSearch(_Answer):
    router_model: str
    index_version: str
    # Best first. Steps are the guide's own words: quote them, with the citation.
    sections: list[GuideSectionFound]


class CreditWorking(_Answer):
    outage_minutes: int | None
    billable_hours: int | None
    multiplier: Decimal | None
    computed_usd: Decimal | None
    cap_remaining_usd: Decimal | None
    floor_applied: bool
    cap_applied: bool


class CreditQuote(_Answer):
    eligible: bool
    reason: str
    amount_usd: Decimal
    currency: str
    approval: Literal["none", "agent", "supervisor"]
    policy_id: str
    policy_version: str
    working: CreditWorking
    note: str


class FeedHealth(_Answer):
    applied_seq: int
    source_last_seq: int
    caught_up: bool
    # False while an event is known to have been lost: the feed's view is then not evidence.
    intact: bool
    heartbeat_at: datetime | None
    last_error: str | None


class BacklogView(_Answer):
    contacts_waiting: int
    oldest_seconds: float
    # Surge mode: callers are told of a delay, and the rules read messages instead of a model.
    behind: bool


class GuideIndexView(_Answer):
    index_version: str
    embedder: str
    documents: int
    chunks: int
    built_at: datetime


class PlatformStatus(_Answer):
    feed: FeedHealth
    # None when the queue could not be counted just now.
    backlog: BacklogView | None
    guide_index: GuideIndexView | None
    open_outages: int
    active_alarms: int


class CareTools:
    def __init__(
        self,
        *,
        diagnostics: DiagnosticsAgent | None,
        reference: ReferenceRepository,
        troubleshooting: TroubleshootingAgent,
        index: GuideIndex,
        policy: CreditPolicy,
        policy_text: str,
        review_all: bool,
        read_model: ReadModelRepository,
        gauge: SurgeGauge,
        audit: AuditRepository,
        limiter: RateLimiter,
        now: Clock = utc_now,
    ) -> None:
        # None when this process holds no key for Network Monitoring.
        self._diagnostics = diagnostics
        self._reference = reference
        self._troubleshooting = troubleshooting
        self._index = index
        self._policy = policy
        # The file as it was when the engine read it: what a client is shown is what prices.
        self._policy_text = policy_text
        self._review_all = review_all
        self._read_model = read_model
        self._gauge = gauge
        self._audit = audit
        self._limiter = limiter
        # How often a refusal for the limit is written to the trail.
        self._limit_on_record = per_minute(1)
        self._now = now

    async def aclose(self) -> None:
        await self._gauge.aclose()

    # --- the four questions ---------------------------------------------------------------

    async def check_network_status(self, node_id: str) -> NetworkStatus:
        async def answer(audit: Auditor) -> tuple[NetworkStatus, dict[str, Any]]:
            if self._diagnostics is None:
                raise Refused(
                    "this server holds no key for Network Monitoring (OSS_KEY_MCP is not set),"
                    " so it cannot read the status of a node",
                    outcome="NOT_CONFIGURED",
                )
            node = await self._reference.get_node(node_id)
            if node is None or node.service_type is None:
                raise Refused(f"{node_id} is not a node the platform knows to serve customers")
            decision = await self._diagnostics.node_state(node_id, node.service_type, audit=audit)
            status = _status_of(node_id, decision)
            return status, {"state": status.state, "reason": status.reason}

        return await self._asked("check_network_status", {"node_id": node_id}, answer)

    async def search_router_guide(self, router_model: str, problem: str) -> GuideSearch:
        async def answer(_: Auditor) -> tuple[GuideSearch, dict[str, Any]]:
            retrieved = await self._troubleshooting.retrieve(
                router_model=router_model, problem=problem
            )
            if retrieved.outcome == "NO_GUIDE_FOR_MODEL":
                raise Refused(
                    f"there is no guide for router model {router_model} in the index",
                    outcome=retrieved.outcome,
                )
            if retrieved.outcome != "OK":
                # No index, or a passage that may not be quoted: nothing is served from it.
                raise Refused(
                    "the guide index cannot be answered from just now", outcome=retrieved.outcome
                )
            found = GuideSearch(
                router_model=router_model,
                index_version=retrieved.index_version,
                sections=[
                    GuideSectionFound(
                        citation=passage.citation,
                        guide=passage.title.split(":")[0],
                        section=passage.title.split(":", 1)[-1].strip(),
                        steps=list(passage.steps),
                        score=round(passage.score, 4),
                    )
                    for passage in retrieved.offered
                ],
            )
            return found, {"citations": [section.citation for section in found.sections]}

        # The words of the problem are somebody's description of a fault, perhaps a
        # customer's own. Their length is kept, not the words.
        asked = {"router_model": router_model, "problem_chars": len(problem)}
        return await self._asked("search_router_guide", asked, answer)

    async def quote_service_credit(
        self,
        *,
        service_type: str,
        monthly_fee_usd: Decimal,
        outage_minutes: int,
        planned: bool = False,
        credited_this_cycle_usd: Decimal = Decimal(0),
    ) -> CreditQuote:
        async def answer(_: Auditor) -> tuple[CreditQuote, dict[str, Any]]:
            resolved_at = self._now()
            decision = evaluate_credit(
                self._policy,
                CreditInput(
                    service_type=ServiceType(service_type),
                    monthly_fee_usd=monthly_fee_usd,
                    service_active=True,
                    affected=True,
                    planned=planned,
                    started_at=resolved_at - timedelta(minutes=outage_minutes),
                    resolved_at=resolved_at,
                    prior_outage_credits_in_cycle_usd=credited_this_cycle_usd,
                    already_credited_for_outage=False,
                ),
            )
            level = approval_level(decision, review_all=self._review_all)
            working = decision.calculation
            quote = CreditQuote(
                eligible=decision.eligible,
                reason=decision.reason.value,
                amount_usd=decision.amount_usd,
                currency=self._policy.currency,
                approval=level.value,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
                working=CreditWorking(
                    outage_minutes=working.outage_minutes,
                    billable_hours=working.billable_hours,
                    multiplier=working.multiplier,
                    computed_usd=working.computed_usd,
                    cap_remaining_usd=working.cap_remaining_usd,
                    floor_applied=working.floor_applied,
                    cap_applied=working.cap_applied,
                ),
                note=QUOTE_NOTE,
            )
            return quote, {"eligible": quote.eligible, "amount_usd": str(quote.amount_usd)}

        asked = {
            "service_type": service_type,
            "monthly_fee_usd": str(monthly_fee_usd),
            "outage_minutes": outage_minutes,
            "planned": planned,
            "credited_this_cycle_usd": str(credited_this_cycle_usd),
        }
        return await self._asked("quote_service_credit", asked, answer)

    async def platform_status(self) -> PlatformStatus:
        async def answer(_: Auditor) -> tuple[PlatformStatus, dict[str, Any]]:
            feed, counts = await self._read_model.feed_state(), await self._read_model.stats()
            info = await self._index.info()
            status = PlatformStatus(
                feed=FeedHealth(
                    applied_seq=feed.applied_seq,
                    source_last_seq=feed.source_last_seq,
                    caught_up=feed.caught_up,
                    intact=feed.intact,
                    heartbeat_at=feed.heartbeat_at,
                    last_error=feed.last_error,
                ),
                backlog=await self._backlog(),
                guide_index=(
                    GuideIndexView(
                        index_version=info.index_version,
                        embedder=info.embedder,
                        documents=info.documents,
                        chunks=info.chunks,
                        built_at=info.built_at,
                    )
                    if info
                    else None
                ),
                open_outages=int(counts.get("open_outages", 0)),
                active_alarms=int(counts.get("active_alarms", 0)),
            )
            return status, {"feed_intact": feed.intact, "feed_caught_up": feed.caught_up}

        return await self._asked("platform_status", {}, answer)

    # --- two documents a client may read --------------------------------------------------

    async def credit_policy_text(self) -> str:
        """The policy file the engine executes, as it was read when this process started."""

        async def answer(_: Auditor) -> tuple[str, dict[str, Any]]:
            return self._policy_text, {"policy_version": self._policy.version}

        return await self._asked("credit-policy", {}, answer, kind="resource")

    async def router_models(self) -> list[dict[str, Any]]:
        """The router models that have a guide in the index."""

        async def answer(_: Auditor) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            models = [
                {
                    "device_model": guide.device_model,
                    "guide": guide.guide,
                    "doc_id": guide.doc_id,
                    "version": guide.doc_version,
                    "sections": guide.sections,
                }
                for guide in await self._index.guides()
            ]
            return models, {"models": len(models)}

        return await self._asked("router-models", {}, answer, kind="resource")

    # --- every call: counted, timed and recorded -------------------------------------------

    async def _asked[Answer](
        self,
        name: str,
        asked: dict[str, Any],
        answer: Callable[[Auditor], Awaitable[tuple[Answer, dict[str, Any]]]],
        *,
        kind: Literal["tool", "resource"] = "tool",
    ) -> Answer:
        """One call of a tool, or one reading of a document: counted against the limit, then
        answered, then written to the trail.

        The record is part of the answer. If it cannot be written, the caller gets a fault and
        not the answer: nothing is served that the trail does not show.
        """
        auditor = Auditor(self._audit, contact_id=None, correlation_id=f"mcp-{uuid4().hex}")
        began = time.monotonic()

        async def note(outcome: str, outputs: dict[str, Any]) -> None:
            await auditor.record(
                actor=ACTOR,
                step=f"mcp.{kind}:{name}",
                outcome=outcome,
                inputs=asked,
                outputs=outputs,
                duration_ms=int((time.monotonic() - began) * 1000),
            )

        if not self._limiter.allow(ALL_CLIENTS):
            # The first refusal of a minute is on record, the rest are in the log: a client
            # that keeps asking must not be able to make every refusal cost a write.
            if self._limit_on_record.allow(ALL_CLIENTS):
                await note("RATE_LIMITED", {})
            log.warning("mcp_rate_limited", asked=f"{kind}:{name}")
            raise Refused("too many calls: try again in a moment", outcome="RATE_LIMITED")
        try:
            result, summary = await answer(auditor)
        except Refused as refused:
            await note(refused.outcome, {"why": str(refused)})
            raise
        except asyncio.CancelledError:
            # The client went away, or the server is stopping. The call was made all the same.
            await asyncio.shield(note("CANCELLED", {}))
            raise
        except Exception as error:
            await note("ERROR", {"error": type(error).__name__})
            raise
        await note("OK", summary)
        return result

    async def _backlog(self) -> BacklogView | None:
        try:
            backlog = await self._gauge.figures()
        except BacklogUnknownError:
            return None
        return BacklogView(
            contacts_waiting=backlog.contacts,
            oldest_seconds=round(backlog.oldest_seconds, 1),
            behind=self._gauge.behind(),
        )


def _outage(outage: OutageInfo | None) -> OutageView | None:
    if outage is None:
        return None
    return OutageView(
        outage_id=outage.outage_id,
        cause=outage.cause,
        planned=outage.planned,
        started_at=outage.started_at,
        eta=outage.eta,
        eta_source=outage.eta_source,
    )


def _status_of(node_id: str, decision: NetworkDecision) -> NetworkStatus:
    return NetworkStatus(
        node_id=node_id,
        state=decision.state.value,
        reason=decision.reason.value,
        source=decision.source.value,
        decided_at=decision.decided_at,
        evidence_at=decision.evidence_at,
        outage=_outage(decision.outage),
        last_known_outage=_outage(decision.last_known_outage),
        meaning=MEANING[decision.state],
    )


def build_tools(
    settings: Settings,
    database: Database,
    gateway: OssGateway,
    *,
    now: Clock = utc_now,
    limiter: RateLimiter | None = None,
) -> CareTools:
    """The tools of one MCP process, on its own read-only identity at the gateway."""
    # Imported here: the runtime module wires the orchestrator and is not needed otherwise.
    from skylink_care.runtime import build_diagnostics

    try:
        network = gateway.network_for_mcp()
    except MissingSecretError:
        # No key for Network Monitoring was given to this process. The other tools need none.
        diagnostics = None
    else:
        diagnostics = build_diagnostics(settings, database, network, now=now)
    index = GuideIndex(database, HashingEmbedder())
    return CareTools(
        diagnostics=diagnostics,
        reference=ReferenceRepository(database),
        troubleshooting=TroubleshootingAgent(index),
        index=index,
        policy=load_policy(settings.credit_policy_path),
        policy_text=settings.credit_policy_path.read_text(encoding="utf-8"),
        review_all=settings.credit_review_all,
        read_model=ReadModelRepository(database),
        gauge=build_gauge(settings, database),
        audit=AuditRepository(database),
        limiter=limiter or per_minute(settings.mcp_calls_per_minute),
        now=now,
    )
