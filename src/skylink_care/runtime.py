"""Wiring for the orchestrator process: repositories, read-only adapters and agents.

Nothing in this object can write to an enterprise system. The executor process builds its own,
separate set of clients from its own key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from skylink_care.adapters.gateway import OssGateway
from skylink_care.adapters.shared import NetworkSource, SharedNodeStatus
from skylink_care.agents.diagnostics import DiagnosticsAgent, FeedViewSource, LineTestPolicy
from skylink_care.agents.dispatch import DispatchAgent
from skylink_care.agents.resolution import ResolutionAgent
from skylink_care.agents.triage import TriageAgent
from skylink_care.agents.troubleshooting import TroubleshootingAgent
from skylink_care.clock import Clock, utc_now
from skylink_care.config import Settings
from skylink_care.db.actions import ActionRepository
from skylink_care.db.approvals import ApprovalRepository
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.episodes import EpisodeRepository
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.line_tests import LineTestRepository
from skylink_care.db.messages import MessageRepository
from skylink_care.db.read_model import ReadModelRepository
from skylink_care.db.reference import ReferenceRepository
from skylink_care.db.timers import TimerRepository
from skylink_care.domain.credit import load_policy
from skylink_care.domain.dispatch import VisitLimits
from skylink_care.domain.network import DecisionLimits
from skylink_care.llm.gateway import LLMGateway, build_gateway
from skylink_care.observability.audit import Auditor
from skylink_care.rag.embedding import HashingEmbedder
from skylink_care.rag.index import GuideIndex

__all__ = ["Clock", "OrchestratorRuntime", "build_orchestrator_runtime", "utc_now"]


@dataclass(frozen=True)
class OrchestratorRuntime:
    settings: Settings
    db: Database
    contacts: ContactRepository
    inbox: InboxRepository
    messages: MessageRepository
    actions: ActionRepository
    approvals: ApprovalRepository
    episodes: EpisodeRepository
    handoffs: HandoffRepository
    audit: AuditRepository
    timers: TimerRepository
    read_model: ReadModelRepository
    triage: TriageAgent
    diagnostics: DiagnosticsAgent
    troubleshooting: TroubleshootingAgent
    resolution: ResolutionAgent
    dispatch: DispatchAgent
    now: Clock

    def auditor(self, contact_id: UUID, correlation_id: str) -> Auditor:
        return Auditor(self.audit, contact_id=contact_id, correlation_id=correlation_id)


def decision_limits(settings: Settings) -> DecisionLimits:
    return DecisionLimits(
        status_max_age=timedelta(seconds=settings.status_max_age_seconds),
        feed_max_age=timedelta(seconds=settings.feed_max_age_seconds),
        line_test_cluster_threshold=settings.line_test_cluster_threshold,
    )


def visit_limits(settings: Settings, *, line_test_max_age_seconds: int) -> VisitLimits:
    """The limits of the visit rule. The age a line test may have differs by who asks: the
    workflow proposes on a test made moments ago, the executor books within a longer bound."""
    return VisitLimits(
        hold=timedelta(seconds=settings.dispatch_hold_seconds),
        status_max_age=timedelta(seconds=settings.status_max_age_seconds),
        line_test_max_age=timedelta(seconds=line_test_max_age_seconds),
    )


def build_diagnostics(
    settings: Settings,
    database: Database,
    network: NetworkSource,
    *,
    feed: FeedViewSource | None = None,
    now: Clock = utc_now,
    share_status_reads: bool = False,
) -> DiagnosticsAgent:
    """The network decision, for the orchestrator and for a process that must look at a node
    again before it acts (the executor, before it books a visit).

    The feed view is what the feed consumer has learnt. With no consumer running its heartbeat
    ages and the decision stops trusting it, which is exactly the degraded behaviour wanted.
    """
    return DiagnosticsAgent(
        network,
        ReferenceRepository(database),
        feed or ReadModelRepository(database),
        LineTestRepository(database),
        decision_limits(settings),
        LineTestPolicy(
            cluster_window=timedelta(seconds=settings.line_test_cluster_window_seconds),
            retest_after=timedelta(seconds=settings.line_retest_seconds),
        ),
        now,
        shared=SharedNodeStatus(network) if share_status_reads else None,
    )


def build_orchestrator_runtime(
    settings: Settings,
    database: Database,
    gateway: OssGateway,
    *,
    llm: LLMGateway | None = None,
    feed: FeedViewSource | None = None,
    now: Clock = utc_now,
) -> OrchestratorRuntime:
    return OrchestratorRuntime(
        settings=settings,
        db=database,
        contacts=ContactRepository(database),
        inbox=InboxRepository(database),
        messages=MessageRepository(database),
        actions=ActionRepository(database),
        approvals=ApprovalRepository(database),
        episodes=EpisodeRepository(database),
        handoffs=HandoffRepository(database),
        audit=AuditRepository(database),
        timers=TimerRepository(database),
        read_model=ReadModelRepository(database),
        triage=TriageAgent(gateway.crm_for_triage(), llm or build_gateway(settings)),
        diagnostics=build_diagnostics(
            settings,
            database,
            gateway.network_for_diagnostics(),
            feed=feed,
            now=now,
            # Diagnoses of one node that run at the same moment share one status read. The
            # executor, before it books a visit, reads for itself (build_visit_booker).
            share_status_reads=True,
        ),
        troubleshooting=TroubleshootingAgent(GuideIndex(database, HashingEmbedder())),
        resolution=ResolutionAgent(
            gateway.billing_for_resolution(),
            # Read and validated once, at start-up: a bad policy file must never price a credit.
            load_policy(settings.credit_policy_path),
            review_all=settings.credit_review_all,
            # Credits proposed and not yet settled count towards the billing-cycle cap.
            ledger=ActionRepository(database),
        ),
        dispatch=DispatchAgent(
            gateway.field_for_dispatch(),
            visit_limits(
                settings, line_test_max_age_seconds=settings.visit_line_test_max_age_seconds
            ),
            slots_offered=settings.visit_slots_offered,
            now=now,
        ),
        now=now,
    )
