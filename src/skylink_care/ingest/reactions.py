"""What the platform does when the NOC declares or resolves an outage, or an alarm clears.

Everything here is a write to the platform's own database, made in the same transaction that
advances the feed cursor. A feed event therefore has its consequences exactly once: either the
event, its notifications, its wake-ups and the cursor all commit, or none of them do.

Nothing here talks to a customer or to an enterprise system. Notifications and ticket changes are
proposed in the action ledger for the executor; waiting conversations are woken through their
inbox and decide for themselves, from fresh evidence, what to say.

Whatever is deliberately not done (a node nobody knows, an outage learnt too late to announce)
is logged, because an event that produces nothing looks the same as a healthy one otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog

from skylink_care.agents import notifications
from skylink_care.db.actions import (
    INCIDENT_LANE,
    INCIDENT_UPDATE,
    NOTIFICATION_LANE,
    NOTIFY_SEND,
    ActionRepository,
    ActionStatus,
    NewAction,
)
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Connection
from skylink_care.db.episodes import Episode, EpisodeRepository
from skylink_care.db.inbox import OUTAGE_DECLARED_EVENT, OUTAGE_RESOLVED_EVENT, append_event
from skylink_care.db.read_model import Change, OutageChange, ReadModelRepository
from skylink_care.db.reference import NodeRecord, ReferenceRepository
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import MAX_CLOCK_SKEW

log = structlog.get_logger(__name__)

REQUESTED_BY = "system:ingest"
# IN_PROGRESS as well: a turn that is running right now may be about to tell the customer what
# this very event has just made untrue. The event waits in its inbox for the next pass, and
# routing ignores it if the conversation turns out not to be waiting on the network.
WAITING_STATUSES = ("AWAITING_NETWORK", "IN_PROGRESS")
# "Restored" is owed only to a customer whose "started" message was sent or still will be.
_DELIVERABLE = frozenset({ActionStatus.READY, ActionStatus.IN_FLIGHT, ActionStatus.SUCCEEDED})
_LOGGED_NODE_IDS = 20


@dataclass(frozen=True)
class ReactionSummary:
    notifications: int = 0
    contacts_woken: int = 0
    incidents_updated: int = 0


@dataclass(frozen=True)
class _Affected:
    service_types: tuple[str, ...]
    area: str


class OutageReactions:
    def __init__(
        self,
        reference: ReferenceRepository,
        actions: ActionRepository,
        episodes: EpisodeRepository,
        contacts: ContactRepository,
        read_model: ReadModelRepository,
        *,
        announce_within: timedelta,
    ) -> None:
        self._reference = reference
        self._actions = actions
        self._episodes = episodes
        self._contacts = contacts
        self._read_model = read_model
        self._announce_within = announce_within

    async def on_change(
        self, conn: Connection, change: OutageChange, *, correlation_id: str, now: datetime
    ) -> ReactionSummary:
        if change.change is Change.ALARM_CLEARED:
            return await self._alarm_cleared(conn, change.node_ids, correlation_id)
        if change.outage is None:
            return ReactionSummary()
        if change.change is Change.RESOLVED:
            return await self._resolved(
                conn,
                change.outage,
                change.node_ids,
                correlation_id,
                announce=self._is_fresh(change.emitted_at, now),
            )
        if change.change not in {Change.DECLARED, Change.UPDATED}:
            return ReactionSummary()
        if not change.new_node_ids:
            if change.change is Change.DECLARED:
                log.warning("outage_declared_without_nodes", outage_id=change.outage.outage_id)
            return ReactionSummary()  # an update that only changes the estimate
        # A new outage, or an update that widens one: news for the people on these nodes.
        return await self._declared(
            conn,
            change.outage,
            change.new_node_ids,
            correlation_id,
            announce=self._is_fresh(change.emitted_at, now),
            emitted_at=change.emitted_at,
        )

    def _is_fresh(self, emitted_at: datetime | None, now: datetime) -> bool:
        """An event that reaches us late (the consumer was down, or is catching up) still updates
        what we know, but nobody is sent "an outage has started" long after it did. An event
        stamped in the future is not trusted to be current either."""
        if emitted_at is None:
            return False
        return -MAX_CLOCK_SKEW <= now - emitted_at <= self._announce_within

    async def _declared(
        self,
        conn: Connection,
        outage: OutageInfo,
        node_ids: tuple[str, ...],
        correlation_id: str,
        *,
        announce: bool,
        emitted_at: datetime | None,
    ) -> ReactionSummary:
        nodes = await self._access_nodes(conn, node_ids, outage)
        access_ids = tuple(node.node_id for node in nodes)
        updated = 0
        for node in nodes:
            if node.service_type is None:
                continue
            # A ticket opened on suspicion now learns which outage it belongs to.
            episode = await self._episodes.attach_outage_to_open(
                conn, node.node_id, node.service_type, outage.outage_id
            )
            if episode is not None:
                updated += await self._update_incident(
                    conn,
                    episode,
                    key=f"incident-update:{episode.episode_id}:{outage.outage_id}",
                    body={"kind": "NETWORK_OUTAGE", "outage_id": outage.outage_id},
                    correlation_id=correlation_id,
                )
        sent = 0
        if announce:
            proposals = [
                _notify(
                    notifications.outage_started(
                        outage, service_types=affected.service_types, area=affected.area
                    ),
                    customer_id,
                    outage,
                    correlation_id,
                )
                for customer_id, affected in (await self._affected(conn, access_ids)).items()
            ]
            sent = await self._actions.propose_many(conn, proposals)
        else:
            log.warning(
                "outage_announcement_withheld",
                outage_id=outage.outage_id,
                emitted_at=emitted_at.isoformat() if emitted_at else None,
                reason="event is not current",
            )
        woken = await self._wake(
            conn, access_ids, OUTAGE_DECLARED_EVENT, outage, not_told_about=outage.outage_id
        )
        log.info(
            "outage_declared",
            outage_id=outage.outage_id,
            nodes=len(access_ids),
            announced=announce,
            notifications=sent,
            contacts_woken=woken,
            incidents_updated=updated,
        )
        return ReactionSummary(notifications=sent, contacts_woken=woken, incidents_updated=updated)

    async def _resolved(
        self,
        conn: Connection,
        outage: OutageInfo,
        node_ids: tuple[str, ...],
        correlation_id: str,
        *,
        announce: bool,
    ) -> ReactionSummary:
        """Close the ticket, wake who waits and tell those who were told it started. The last
        only while the news is current: a resolution that reaches us long after it happened
        is not announced, for by then nobody here knows what the node is doing."""
        updated = 0
        for episode in await self._episodes.close_for_outage(outage.outage_id, conn=conn):
            updated += await self._resolve_incident(conn, episode, correlation_id)
        nodes = await self._access_nodes(conn, node_ids, outage)
        access_ids = tuple(node.node_id for node in nodes)
        affected = await self._affected(conn, access_ids)
        started_keys = {
            customer_id: notifications.action_key(
                customer_id, outage.outage_id, notifications.OUTAGE_STARTED
            )
            for customer_id in affected
        }
        started = await self._actions.lookup_keys(conn, list(started_keys.values()))
        proposals: list[NewAction] = []
        if not announce:
            log.warning("outage_resolution_notice_withheld", outage_id=outage.outage_id)
        for customer_id, details in affected.items() if announce else ():
            first = started.get(started_keys[customer_id])
            if first is None or first.status not in _DELIVERABLE:
                continue
            proposals.append(
                _notify(
                    notifications.outage_resolved(outage, service_types=details.service_types),
                    customer_id,
                    outage,
                    correlation_id,
                    # Never before the "started" message, and not at all if that one is skipped.
                    depends_on=first.action_id,
                )
            )
        sent = await self._actions.propose_many(conn, proposals)
        woken = await self._wake(conn, access_ids, OUTAGE_RESOLVED_EVENT, outage)
        log.info(
            "outage_resolved",
            outage_id=outage.outage_id,
            notifications=sent,
            not_told_it_started=len(affected) - len(proposals),
            contacts_woken=woken,
            incidents_updated=updated,
        )
        return ReactionSummary(notifications=sent, contacts_woken=woken, incidents_updated=updated)

    async def _alarm_cleared(
        self, conn: Connection, node_ids: tuple[str, ...], correlation_id: str
    ) -> ReactionSummary:
        """A suspected fault whose alarm ended without an outage being declared is over.

        Conversations waiting on it find out at their next re-check; here only the node's
        episode and its ticket are closed, so that later trouble on the node starts a new one.
        """
        updated = 0
        for alarm_node in node_ids:
            for node in await self._reference.access_nodes(alarm_node, conn=conn):
                if node.service_type is None:
                    continue
                if await self._read_model.has_critical_alarm(node.node_id, conn=conn):
                    continue  # another alarm still stands on this node or above it
                episode = await self._episodes.close_suspected(
                    conn, node.node_id, node.service_type
                )
                if episode is not None:
                    updated += await self._resolve_incident(conn, episode, correlation_id)
        if updated:
            log.info("suspected_fault_cleared", nodes=list(node_ids), incidents_updated=updated)
        return ReactionSummary(incidents_updated=updated)

    async def _access_nodes(
        self, conn: Connection, node_ids: tuple[str, ...], outage: OutageInfo
    ) -> list[NodeRecord]:
        """The customer-facing nodes behind the node ids the NOC named.

        The NOC may name an access node or the aggregation node above several of them. An id that
        matches nothing in the reference data reaches no customer: that is reported, because it
        means either the reference data or the event is wrong.
        """
        found: dict[str, NodeRecord] = {}
        unknown: list[str] = []
        for node_id in node_ids:
            access = await self._reference.access_nodes(node_id, conn=conn)
            if not access:
                unknown.append(node_id)
            for node in access:
                found[node.node_id] = node
        if unknown:
            log.warning(
                "outage_nodes_unknown",
                outage_id=outage.outage_id,
                count=len(unknown),
                nodes=unknown[:_LOGGED_NODE_IDS],
            )
        return list(found.values())

    async def _affected(self, conn: Connection, node_ids: tuple[str, ...]) -> dict[str, _Affected]:
        """Each affected customer once, with every service of theirs that is affected."""
        grouped: dict[str, tuple[set[str], str]] = {}
        for service in await self._reference.services_on_nodes(node_ids, conn=conn):
            types, _area = grouped.setdefault(service.customer_id, (set(), service.area))
            types.add(service.service_type)
        return {
            customer_id: _Affected(tuple(sorted(types)), area)
            for customer_id, (types, area) in grouped.items()
        }

    async def _wake(
        self,
        conn: Connection,
        node_ids: tuple[str, ...],
        event_type: str,
        outage: OutageInfo,
        *,
        not_told_about: str | None = None,
    ) -> int:
        """Tell the workflows of customers who are already waiting on these nodes.

        Returns how many were newly told: a contact already holding this event is not counted.
        """
        waiting = await self._contacts.waiting_on_nodes(
            conn, node_ids, statuses=WAITING_STATUSES, not_told_about=not_told_about
        )
        woken = 0
        for contact_id in waiting:
            woken += await append_event(
                conn,
                contact_id,
                event_type,
                dedupe_key=f"{event_type}:{outage.outage_id}",
                payload={"outage_id": outage.outage_id},
            )
        return woken

    async def _resolve_incident(
        self, conn: Connection, episode: Episode, correlation_id: str
    ) -> int:
        return await self._update_incident(
            conn,
            episode,
            key=f"incident-resolve:{episode.episode_id}",
            body={"status": "RESOLVED"},
            correlation_id=correlation_id,
        )

    async def _update_incident(
        self,
        conn: Connection,
        episode: Episode,
        *,
        key: str,
        body: dict[str, str],
        correlation_id: str,
    ) -> int:
        """Propose a change to the episode's incident ticket, if one was ever raised."""
        opened_key = f"incident:{episode.episode_id}"
        opened = (await self._actions.lookup_keys(conn, [opened_key])).get(opened_key)
        if opened is None:
            return 0  # nobody reported on this node, so no ticket exists to change
        return await self._actions.propose_many(
            conn,
            [
                NewAction(
                    action_key=key,
                    action_type=INCIDENT_UPDATE,
                    lane=INCIDENT_LANE,
                    requested_by=REQUESTED_BY,
                    episode_id=episode.episode_id,
                    depends_on=opened.action_id,
                    correlation_id=correlation_id,
                    request_body=dict(body),
                )
            ],
        )


def _notify(
    message: notifications.Notification,
    customer_id: str,
    outage: OutageInfo,
    correlation_id: str,
    *,
    depends_on: UUID | None = None,
) -> NewAction:
    return NewAction(
        action_key=notifications.action_key(customer_id, outage.outage_id, message.template_id),
        action_type=NOTIFY_SEND,
        lane=NOTIFICATION_LANE,
        requested_by=notifications.REQUESTED_BY,
        depends_on=depends_on,
        correlation_id=correlation_id,
        request_body=notifications.request_body(
            message, customer_id=customer_id, outage_id=outage.outage_id
        ),
    )
