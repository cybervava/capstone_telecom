"""Orchestrator worker: turns inbox events into workflow runs, one contact at a time.

For each contact with pending events the worker takes the per-contact database lock, looks at
where the workflow thread stands, and either starts it or resumes it with a wake signal. The
graph itself reads the events from the inbox; the worker never passes event content in, so a
redelivered or batched wake-up cannot apply anything twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from skylink_care.adapters.gateway import open_gateway
from skylink_care.agents import replies
from skylink_care.config import get_oss_keys, get_settings
from skylink_care.db.actions import ACTION_FINISHED_EVENT, TERMINAL, Action
from skylink_care.db.contacts import Contact
from skylink_care.db.database import Database
from skylink_care.db.inbox import InboxEvent
from skylink_care.guardrails.claims import UnbackedClaimError
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.observability.logging import configure_logging
from skylink_care.orchestrator.graph import RECURSION_LIMIT, ContactGraph, build_graph
from skylink_care.orchestrator.late_outcomes import OWED_TO_THE_CUSTOMER, tell_late_outcome
from skylink_care.orchestrator.outgoing import store_reply
from skylink_care.orchestrator.routing import CUSTOMER_EVENTS
from skylink_care.runtime import OrchestratorRuntime, build_orchestrator_runtime

log = structlog.get_logger(__name__)

WAKE = "wake"
# Statuses in which the automated workflow no longer acts on a contact.
BOT_DONE = frozenset({"HANDOFF", "RESOLVED", "CLOSED"})
WORKFLOW_ERROR = "WORKFLOW_ERROR"
# A message to the customer was stopped for what it claimed about the network.
MESSAGE_BLOCKED = "MESSAGE_BLOCKED"
# What the customer is told when the workflow lets go of their contact for either reason.
APOLOGY = "I ran into a problem while handling your request."
CUSTOMER_FOLLOW_UP = "CUSTOMER_FOLLOW_UP"
DEFAULT_CONCURRENCY = 8
# Query connections beyond one per concurrent run (checkpointer, scheduling, bursts).
POOL_HEADROOM = 4
# How many due contacts are asked for at a time, in slots' worth, and at the very most.
PAGE_OF_SLOTS = 4
MAX_PAGE = 1000


@dataclass
class _Slots:
    """What the worker's loop has in hand."""

    running: dict[UUID, asyncio.Task[bool]] = field(default_factory=dict)
    # Due contacts read from the inbox and not started yet, oldest first, and when they were
    # read (the loop's clock).
    due: deque[UUID] = field(default_factory=deque)
    due_read_at: float = 0.0
    # Contacts not to ask for again before the time given.
    resting: dict[UUID, float] = field(default_factory=dict)

    def free_to_start(self, contact_id: UUID) -> bool:
        return contact_id not in self.running and contact_id not in self.resting


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
        """Keep every slot busy for as long as contacts are due, until asked to stop.

        A contact is started the moment a slot is free. A turn that takes long (a line test, a
        language model) holds its own slot and nothing else: the contacts behind it go through
        the other slots, where a batch would make them wait for its slowest member.
        """
        slots = _Slots()
        loop = asyncio.get_running_loop()
        timers_due = 0.0
        try:
            while not stop.is_set():
                try:
                    if loop.time() >= timers_due:
                        timers_due = loop.time() + self._poll_seconds
                        await self._fire_timers()
                    await self._fill(stop, slots)
                except Exception:
                    # Usually the database. Log, wait, try again: the loop must outlive it.
                    log.exception("orchestrator_loop_error")
                await self._until_a_slot_frees(stop, slots)
        except asyncio.CancelledError:
            # Cancelled outright, not asked to stop: the turns go with the loop, and each runs
            # again later from where it stood.
            for task in slots.running.values():
                task.cancel()
            raise
        finally:
            # Asked to stop, a turn in progress is finished, not dropped. Dropped, it would run
            # again from its checkpoint, which is safe; finished, its customer has the answer.
            ended = await asyncio.gather(*slots.running.values(), return_exceptions=True)
            for contact_id, outcome in zip(slots.running, ended, strict=True):
                if isinstance(outcome, Exception):
                    _log_failed_turn(contact_id, outcome)

    async def _fill(self, stop: asyncio.Event, slots: _Slots) -> None:
        """Start due contacts in the free slots, the longest-waiting first."""
        loop = asyncio.get_running_loop()
        for contact_id in [one for one, until in slots.resting.items() if until <= loop.time()]:
            del slots.resting[contact_id]
        if loop.time() - slots.due_read_at >= self._poll_seconds:
            # Read while every slot was busy, a poll interval or more ago. Another worker may
            # have run these contacts since, or put one off after a failure: read again.
            slots.due.clear()
        while len(slots.running) < self._concurrency and not stop.is_set():
            if not slots.due:
                # A contact in a worker's hands stays due until its turn ends. The page is
                # made longer by those this worker knows to be in hand, so that it looks past
                # them to one it can start.
                page = self._concurrency * PAGE_OF_SLOTS + len(slots.running) + len(slots.resting)
                found = await self._rt.inbox.pending_contacts(min(page, MAX_PAGE))
                slots.due.extend(one for one in found if slots.free_to_start(one))
                slots.due_read_at = loop.time()
                if not slots.due:
                    return
                continue  # a stop asked for while the query was out starts nothing
            contact_id = slots.due.popleft()
            if slots.free_to_start(contact_id):
                slots.running[contact_id] = loop.create_task(self.process_contact(contact_id))

    async def _until_a_slot_frees(self, stop: asyncio.Event, slots: _Slots) -> None:
        """Wait for a turn to end, or for one poll interval, and clear away what ended."""
        if not slots.running:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
            return
        done, _ = await asyncio.wait(
            slots.running.values(),
            timeout=self._poll_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        now = asyncio.get_running_loop().time()
        for contact_id in [one for one, task in slots.running.items() if task in done]:
            task = slots.running.pop(contact_id)
            error = None if task.cancelled() else task.exception()
            if error is not None:
                # Not even the failure could be recorded (the contact's lock or the inbox:
                # the database), so nothing in the inbox puts the contact off. It is put off
                # here for as long as a recorded failure would be.
                _log_failed_turn(contact_id, error)
                slots.resting[contact_id] = now + self._retry_backoff_seconds
            elif task.cancelled() or task.result() is not True:
                # Held by another worker, or failed and put off: not asked for again at once.
                slots.resting[contact_id] = now + self._poll_seconds

    async def _fire_timers(self) -> None:
        """A timer that has come due becomes an inbox event like any other."""
        try:
            await self._rt.timers.fire_due(self._rt.now())
        except Exception:
            # A timer problem must not hold up the contacts that are ready to run.
            log.exception("timer_pump_failed")

    async def run_once(self) -> int:
        """Process the contacts that are due right now, as one batch. Returns how many runs
        completed. For tests and evaluations, which drive the platform a pass at a time; the
        process runs ``run_forever``."""
        await self._fire_timers()
        pending = await self._rt.inbox.pending_contacts(self._concurrency * PAGE_OF_SLOTS)
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
                _log_failed_turn(contact_id, result)
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
            # A person took the conversation while the workflow was parked in it: the state
            # it was parked with is of no further use.
            await self._checkpointer.adelete_thread(str(contact_id))
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
        blocked = isinstance(error, UnbackedClaimError)
        async with self._rt.db.transaction() as conn:
            outcome = await self._rt.inbox.record_failure(
                contact_id,
                # An exception can quote the data it failed on; it is masked before it is stored.
                str(redact_for_log(repr(error))),
                # A message stopped for what it claimed would be stopped again: the turn would
                # find the same evidence. No second try.
                max_attempts=1 if blocked else self._max_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                conn=conn,
            )
            if not outcome.dead_lettered:
                return
            reason = MESSAGE_BLOCKED if blocked else WORKFLOW_ERROR
            await self._rt.handoffs.open(
                contact_id,
                reason=reason,
                summary=_why_a_person(error, outcome.attempts),
                priority=2,
                conn=conn,
            )
            await self._rt.contacts.update(contact_id, conn=conn, status="HANDOFF")
            contact = await self._rt.contacts.get(contact_id, conn=conn)
            if contact is not None:
                apology = replies.handed_to_human(contact.complaint_ref, APOLOGY)
                try:
                    await store_reply(
                        self._rt,
                        contact,
                        apology,
                        author="system",
                        dedupe_key="workflow-error",
                        conn=conn,
                    )
                except UnbackedClaimError:
                    # The apology claims nothing, and a test holds it to that. Should it ever
                    # be stopped, the handoff must stand: a person is told, whatever else.
                    log.exception("apology_blocked", contact_id=str(contact_id))
        log.error(
            "contact_dead_lettered",
            contact_id=str(contact_id),
            attempts=outcome.attempts,
            reason=reason,
        )

    async def _note_follow_up(self, contact: Contact) -> None:
        """Events arrived for a contact the workflow no longer handles: tell its owner.

        The outcome of a credit or of a visit booking is owed to the customer whoever holds the
        contact by then, so it is told from here, and one that did not happen reaches a person.
        """
        events = await self._rt.inbox.fetch_unapplied(contact.contact_id)
        if not events:
            return
        unseen = [event for event in events if event.event_type in CUSTOMER_EVENTS]
        ended = await self._ended_actions(contact, events)
        async with self._rt.db.transaction() as conn:
            for action in ended:
                await tell_late_outcome(self._rt, contact, action, conn)
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
        outputs: dict[str, Any] = {"events": [event.event_type for event in events]}
        if ended:
            outputs["outcomes"] = {action.action_key: action.status.value for action in ended}
        await self._rt.auditor(contact.contact_id, contact.correlation_id).record(
            actor="system:orchestrator",
            step="events_after_workflow_end",
            outcome=contact.status,
            outputs=outputs,
        )

    async def _ended_actions(self, contact: Contact, events: Sequence[InboxEvent]) -> list[Action]:
        """The actions of this contact these events report as ended, of the kinds whose outcome
        the customer is owed (a credit, a visit booking)."""
        ended = []
        for event in events:
            action_id = (event.payload or {}).get("action_id")
            if event.event_type != ACTION_FINISHED_EVENT or not action_id:
                continue
            action = await self._rt.actions.get(UUID(str(action_id)))
            if (
                action is not None
                and action.action_type in OWED_TO_THE_CUSTOMER
                and action.contact_id == contact.contact_id
                and action.status in TERMINAL
            ):
                ended.append(action)
        return ended

    async def _mark_everything_applied(self, contact_id: UUID) -> None:
        events = await self._rt.inbox.fetch_unapplied(contact_id)
        if events:
            await self._rt.inbox.mark_applied(contact_id, events[-1].seq)


def _log_failed_turn(contact_id: UUID, error: BaseException) -> None:
    log.error("contact_processing_error", contact_id=str(contact_id), error=repr(error))


def _why_a_person(error: Exception, attempts: int) -> str:
    """What the person who takes the contact is told about why the workflow let go of it."""
    if isinstance(error, UnbackedClaimError):
        stopped = error.violation
        return (
            f"Reason: {MESSAGE_BLOCKED}. A message to the customer was stopped before it was"
            f" sent: it would have said something about the network ({stopped.claim}) that the"
            f" checks of that moment did not back ({stopped.code}, template"
            f" {stopped.template_id}, network {stopped.network_state or 'not looked at'})."
            " The customer was told only that a person is taking over. Nothing further was done"
            " automatically; see the audit trail and the conversation."
        )
    return (
        f"Reason: {WORKFLOW_ERROR}. The automated workflow failed {attempts} times"
        f" ({type(error).__name__}). Nothing further was done automatically; see the audit"
        " trail and the conversation."
    )


async def _main() -> None:
    settings = get_settings()
    configure_logging()
    concurrency = settings.worker_concurrency
    database = await Database.open(
        settings.database_conninfo(),
        max_size=max(settings.db_pool_max, concurrency + POOL_HEADROOM),
        lock_connections=concurrency,
    )
    gateway = open_gateway(settings, get_oss_keys())
    try:
        runtime = build_orchestrator_runtime(settings, database, gateway)
        try:
            await _serve(runtime, concurrency)
        finally:
            await runtime.surge.aclose()
    finally:
        await gateway.aclose()
        await database.close()


async def _serve(runtime: OrchestratorRuntime, concurrency: int) -> None:
    """Run the loop until the process is asked to stop."""
    checkpointer = AsyncPostgresSaver(runtime.db.pool)
    worker = OrchestratorWorker(
        runtime, build_graph(runtime, checkpointer), checkpointer, concurrency=concurrency
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    log.info(
        "orchestrator_started",
        llm_provider=runtime.settings.llm_provider,
        concurrency=concurrency,
    )
    await worker.run_forever(stop)


def run() -> None:
    asyncio.run(_main())
