"""Orchestrator worker: turns inbox events into workflow runs, one contact at a time.

For each contact with pending events the worker takes the per-contact database lock, looks at
where the workflow thread stands, and either starts it or resumes it with a wake signal. The
graph itself reads the events from the inbox; the worker never passes event content in, so a
redelivered or batched wake-up cannot apply anything twice.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any
from uuid import UUID

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from skylink_care.adapters.gateway import OssGateway
from skylink_care.agents import replies
from skylink_care.config import get_oss_keys, get_settings
from skylink_care.db.contacts import Contact
from skylink_care.db.database import Database
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.observability.logging import configure_logging
from skylink_care.orchestrator.graph import RECURSION_LIMIT, ContactGraph, build_graph
from skylink_care.orchestrator.routing import CUSTOMER_EVENTS
from skylink_care.runtime import OrchestratorRuntime, build_orchestrator_runtime

log = structlog.get_logger(__name__)

WAKE = "wake"
# Statuses in which the automated workflow no longer acts on a contact.
BOT_DONE = frozenset({"HANDOFF", "RESOLVED", "CLOSED"})
WORKFLOW_ERROR = "WORKFLOW_ERROR"
CUSTOMER_FOLLOW_UP = "CUSTOMER_FOLLOW_UP"
DEFAULT_CONCURRENCY = 8
# Query connections beyond one per concurrent run (checkpointer, scheduling, bursts).
POOL_HEADROOM = 4


class OrchestratorWorker:
    def __init__(
        self,
        runtime: OrchestratorRuntime,
        graph: ContactGraph,
        checkpointer: AsyncPostgresSaver,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        poll_seconds: float = 0.25,
        max_attempts: int = 5,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self._rt = runtime
        self._graph = graph
        self._checkpointer = checkpointer
        self._concurrency = concurrency
        self._poll_seconds = poll_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                progressed = await self.run_once()
            except Exception:
                # Usually the database. Log, wait, try again: the loop must outlive the outage.
                log.exception("orchestrator_loop_error")
                progressed = 0
            if progressed == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue

    async def run_once(self) -> int:
        """Process the contacts that are due right now. Returns how many runs completed."""
        # Timers first: a timer that has come due becomes an inbox event like any other.
        try:
            await self._rt.timers.fire_due(self._rt.now())
        except Exception:
            # A timer problem must not hold up the contacts that are ready to run.
            log.exception("timer_pump_failed")
        pending = await self._rt.inbox.pending_contacts(self._concurrency * 4)
        if not pending:
            return 0
        gate = asyncio.Semaphore(self._concurrency)

        async def guarded(contact_id: UUID) -> bool:
            async with gate:
                return await self.process_contact(contact_id)

        results = await asyncio.gather(
            *(guarded(contact_id) for contact_id in pending), return_exceptions=True
        )
        for contact_id, result in zip(pending, results, strict=True):
            if isinstance(result, BaseException):
                log.error(
                    "contact_processing_error", contact_id=str(contact_id), error=repr(result)
                )
        return sum(1 for result in results if result is True)

    async def process_contact(self, contact_id: UUID) -> bool:
        """Run one contact's workflow under its lock.

        True when a run completed. False when another worker holds the contact, or when the run
        failed and was scheduled for a later retry.
        """
        async with self._rt.db.contact_lock(contact_id) as locked:
            if not locked:
                return False
            try:
                await self._advance(contact_id)
            except Exception as exc:
                log.exception("workflow_run_failed", contact_id=str(contact_id))
                await self._after_failure(contact_id, exc)
                return False
            return True

    async def _advance(self, contact_id: UUID) -> None:
        contact = await self._rt.contacts.get(contact_id)
        if contact is None:
            log.warning("events_for_unknown_contact", contact_id=str(contact_id))
            await self._mark_everything_applied(contact_id)
            return
        if contact.status in BOT_DONE:
            await self._note_follow_up(contact)
            return
        config: Any = {
            "configurable": {"thread_id": str(contact_id)},
            "recursion_limit": RECURSION_LIMIT,
        }
        snapshot = await self._graph.aget_state(config)
        if snapshot.created_at is None:
            run_input: Any = {"contact_id": str(contact_id), "inbox_cursor": 0, "turn": 0}
        elif not snapshot.next:
            # The thread ran to its end earlier; nothing will consume these events.
            await self._note_follow_up(contact)
            return
        elif snapshot.interrupts:
            # Parked at await_event. The resume value is only a wake signal: the graph reads the
            # events itself, from its own cursor.
            run_input = Command(resume=WAKE)
        else:
            # An earlier run stopped part-way (a node raised). Its completed steps are
            # checkpointed; continue from the step that failed.
            run_input = None
        # One checkpoint per run, written when the run ends or fails. If the process dies before
        # that, the next run repeats the turn from the previous checkpoint; both paths are safe
        # because every side effect in the nodes is idempotent.
        result = await self._graph.ainvoke(run_input, config, durability="exit")
        await self._rt.inbox.mark_applied(contact_id, int(result.get("inbox_cursor", 0)))
        if "__interrupt__" not in result:
            # The thread finished. Its state is on the contact row; the checkpoints can go.
            await self._checkpointer.adelete_thread(str(contact_id))

    async def _after_failure(self, contact_id: UUID, error: Exception) -> None:
        """Schedule a retry with back-off, or give the contact to a person when retries run out.

        Dead-lettering, the handoff, the status change and the message to the customer commit
        together: a contact is never left with its events discarded and nobody told.
        """
        async with self._rt.db.transaction() as conn:
            outcome = await self._rt.inbox.record_failure(
                contact_id,
                # An exception can quote the data it failed on; it is masked before it is stored.
                str(redact_for_log(repr(error))),
                max_attempts=self._max_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                conn=conn,
            )
            if not outcome.dead_lettered:
                return
            await self._rt.handoffs.open(
                contact_id,
                reason=WORKFLOW_ERROR,
                summary=(
                    f"Reason: {WORKFLOW_ERROR}. The automated workflow failed"
                    f" {outcome.attempts} times ({type(error).__name__}). Nothing further was"
                    " done automatically; see the audit trail and the conversation."
                ),
                priority=2,
                conn=conn,
            )
            await self._rt.contacts.update(contact_id, conn=conn, status="HANDOFF")
            contact = await self._rt.contacts.get(contact_id, conn=conn)
            if contact is not None:
                apology = replies.handed_to_human(
                    contact.complaint_ref, "I ran into a problem while handling your request."
                )
                await self._rt.messages.add(
                    contact_id,
                    direction="outbound",
                    author="system",
                    body=apology.text,
                    dedupe_key="workflow-error",
                    meta={"template_id": apology.template_id},
                    conn=conn,
                )
        log.error("contact_dead_lettered", contact_id=str(contact_id), attempts=outcome.attempts)

    async def _note_follow_up(self, contact: Contact) -> None:
        """Events arrived for a contact the workflow no longer handles: tell its owner."""
        events = await self._rt.inbox.fetch_unapplied(contact.contact_id)
        if not events:
            return
        unseen = [event for event in events if event.event_type in CUSTOMER_EVENTS]
        async with self._rt.db.transaction() as conn:
            if unseen:
                # A customer message must never fall on the floor. After a handoff its owner is
                # told; after a close the contact is opened again for a person.
                after = "handed to a person" if contact.status == "HANDOFF" else "closed"
                await self._rt.handoffs.open(
                    contact.contact_id,
                    reason=CUSTOMER_FOLLOW_UP,
                    summary=(
                        f"Reason: {CUSTOMER_FOLLOW_UP}. The customer wrote again after this"
                        f" contact was {after}. Read the conversation before replying."
                    ),
                    priority=3,
                    conn=conn,
                )
                if contact.status != "HANDOFF":
                    await self._rt.contacts.update(
                        contact.contact_id, conn=conn, status="HANDOFF", closed_at=None
                    )
            await self._rt.inbox.mark_applied(contact.contact_id, events[-1].seq, conn=conn)
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor="system:orchestrator",
            step="events_after_workflow_end",
            outcome=contact.status,
            outputs={"events": [event.event_type for event in events]},
        )

    async def _mark_everything_applied(self, contact_id: UUID) -> None:
        events = await self._rt.inbox.fetch_unapplied(contact_id)
        if events:
            await self._rt.inbox.mark_applied(contact_id, events[-1].seq)


async def _main() -> None:
    settings = get_settings()
    configure_logging()
    database = await Database.open(
        settings.database_conninfo(),
        max_size=max(settings.db_pool_max, DEFAULT_CONCURRENCY + POOL_HEADROOM),
        lock_connections=DEFAULT_CONCURRENCY,
    )
    gateway = OssGateway(settings.oss_base_url, get_oss_keys())
    try:
        runtime = build_orchestrator_runtime(settings, database, gateway)
        checkpointer = AsyncPostgresSaver(database.pool)
        worker = OrchestratorWorker(runtime, build_graph(runtime, checkpointer), checkpointer)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        log.info("orchestrator_started", llm_provider=settings.llm_provider)
        await worker.run_forever(stop)
    finally:
        await gateway.aclose()
        await database.close()


def run() -> None:
    asyncio.run(_main())
