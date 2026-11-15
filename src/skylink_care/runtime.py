"""Wiring for the orchestrator process: repositories, read-only adapters and agents.

Nothing in this object can write to an enterprise system. The executor process builds its own,
separate set of clients from its own key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from skylink_care.adapters.gateway import OssGateway
from skylink_care.agents.diagnostics import DiagnosticsAgent, FeedViewSource, LineTestPolicy
from skylink_care.agents.triage import TriageAgent
from skylink_care.clock import Clock, utc_now
from skylink_care.config import Settings
from skylink_care.db.actions import ActionRepository
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
from skylink_care.domain.network import DecisionLimits
from skylink_care.llm.gateway import LLMGateway, build_gateway
from skylink_care.observability.audit import Auditor

__all__ = ["Clock", "OrchestratorRuntime", "build_orchestrator_runtime", "utc_now"]


@dataclass(frozen=True)
class OrchestratorRuntime:
    settings: Settings
    db: Database
    contacts: ContactRepository
    inbox: InboxRepository
    messages: MessageRepository
    actions: ActionRepository
    episodes: EpisodeRepository
    handoffs: HandoffRepository
    audit: AuditRepository
    timers: TimerRepository
    read_model: ReadModelRepository
    triage: TriageAgent
    diagnostics: DiagnosticsAgent
    now: Clock

    def auditor(self, contact_id: UUID, correlation_id: str) -> Auditor:
        return Auditor(self.audit, contact_id=contact_id, correlation_id=correlation_id)


def decision_limits(settings: Settings) -> DecisionLimits:
    return DecisionLimits(
        status_max_age=timedelta(seconds=settings.status_max_age_seconds),
        feed_max_age=timedelta(seconds=settings.feed_max_age_seconds),
        line_test_cluster_threshold=settings.line_test_cluster_threshold,
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
    reference = ReferenceRepository(database)
    read_model = ReadModelRepository(database)
    return OrchestratorRuntime(
        settings=settings,
        db=database,
        contacts=ContactRepository(database),
        inbox=InboxRepository(database),
        messages=MessageRepository(database),
        actions=ActionRepository(database),
        episodes=EpisodeRepository(database),
        handoffs=HandoffRepository(database),
        audit=AuditRepository(database),
        timers=TimerRepository(database),
        read_model=read_model,
        triage=TriageAgent(gateway.crm_for_triage(), llm or build_gateway(settings)),
        diagnostics=DiagnosticsAgent(
            gateway.network_for_diagnostics(),
            reference,
            # What the feed consumer has learnt. With no consumer running its heartbeat ages and
            # the decision stops trusting it, which is exactly the degraded behaviour wanted.
            feed or read_model,
            LineTestRepository(database),
            decision_limits(settings),
            LineTestPolicy(
                cluster_window=timedelta(seconds=settings.line_test_cluster_window_seconds),
                retest_after=timedelta(seconds=settings.line_retest_seconds),
            ),
            now,
        ),
        now=now,
    )
