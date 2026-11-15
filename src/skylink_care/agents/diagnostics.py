"""Network Diagnostics Agent: find the customer's node and decide what is known about it.

It may read the mapping and call Network Monitoring, and it writes nothing. The decision itself
is made by ``domain.network.decide_network_state``; this agent only gathers the evidence, so the
rule that a failed call is never an all-clear lives in one tested place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from skylink_care.adapters.clients import NetworkReader
from skylink_care.adapters.convert import to_live_status
from skylink_care.adapters.result import Err, ErrorKind
from skylink_care.db.reference import ReferenceRepository
from skylink_care.domain.models import MappingStatus, NodeMapping
from skylink_care.domain.network import (
    DecisionLimits,
    FeedView,
    LiveStatus,
    NetworkDecision,
    decide_network_state,
)
from skylink_care.observability.audit import Auditor

ACTOR = "agent:diagnostics"


class FeedViewSource(Protocol):
    async def view(self, node_id: str, service_type: str) -> FeedView: ...


class NoFeed:
    """Stands in until the event read model exists: there is simply no feed evidence."""

    async def view(self, node_id: str, service_type: str) -> FeedView:
        return FeedView(heartbeat_at=None, active_outage=None)


@dataclass(frozen=True)
class Diagnosis:
    mapping: NodeMapping
    decision: NetworkDecision


class DiagnosticsAgent:
    def __init__(
        self,
        network: NetworkReader,
        reference: ReferenceRepository,
        feed: FeedViewSource,
        limits: DecisionLimits,
        now: Callable[[], datetime],
    ) -> None:
        self._network = network
        self._reference = reference
        self._feed = feed
        self._limits = limits
        self._now = now

    async def run(self, *, address_id: str, service_type: str, audit: Auditor) -> Diagnosis:
        mapping = await self._reference.resolve_mapping(address_id, service_type)
        live = LiveStatus.unavailable("NOT_ASKED")
        feed = FeedView(heartbeat_at=None, active_outage=None)
        node_id = mapping.node_id if mapping.is_resolved else None
        if node_id is not None:
            result = await audit.tool(
                actor=ACTOR,
                tool="network.node_status",
                inputs={"node_id": node_id},
                call=self._network.node_status(node_id, correlation_id=audit.correlation_id),
            )
            if isinstance(result, Err) and result.kind is ErrorKind.GONE:
                # The mapping points at retired equipment. That is a stale mapping, not a
                # healthy network.
                mapping = replace(mapping, status=MappingStatus.STALE, node_id=None)
            else:
                live = to_live_status(result)
                feed = await self._feed.view(node_id, service_type)
        decision = decide_network_state(
            mapping=mapping, live=live, feed=feed, now=self._now(), limits=self._limits
        )
        await audit.record(
            actor=ACTOR,
            step="diagnostics.decide",
            outcome=decision.state.value,
            inputs={
                "address_id": address_id,
                "service_type": service_type,
                "mapping_status": mapping.status.value,
                "map_version": mapping.map_version,
                "live_available": live.available,
                "live_error": live.error,
                "feed_heartbeat_at": _iso(feed.heartbeat_at),
            },
            outputs={
                "state": decision.state.value,
                "reason": decision.reason.value,
                "source": decision.source.value,
                "node_id": decision.node_id,
                "outage_id": decision.outage.outage_id if decision.outage else None,
                "evidence_at": _iso(decision.evidence_at),
            },
        )
        return Diagnosis(mapping=mapping, decision=decision)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
