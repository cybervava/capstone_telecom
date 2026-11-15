"""Action executor: the only process that writes to enterprise systems.

It claims actions from the ledger, sends each one exactly as it was frozen at proposal time under
its own key as the Idempotency-Key, and records the outcome once.

How an answer is read is the heart of this module:

* success                 -> SUCCEEDED
* a definite rejection    -> FAILED, and a person is told
* anything else           -> the outcome is unknown: the identical request is replayed later.
  That covers timeouts and 5xx, but also an answer that could not be parsed, an auth failure and
  an unexplained conflict, because in none of those cases do we know the write did not land.

Replays are bounded twice: after ``alert_after_attempts`` a person is told while replaying goes
on, and after ``give_up_after`` the action is closed as FAILED for manual reconciliation.

A customer notification is a write with a condition attached. Consent is read from the CRM
immediately before sending, not when the message was proposed, and a message that may not or
should no longer be sent ends as SKIPPED: finished on purpose, with nobody to alert. A service
credit has more conditions, and its own module (``executor.credits``).

The clock for giving up starts when an action became runnable. For one that needed an approval
that is the approval, which may come days after the proposal.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import structlog
from pydantic import BaseModel

from skylink_care.adapters.gateway import ExecutorClients, OssGateway
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.clock import Clock, utc_now
from skylink_care.config import get_oss_keys, get_settings
from skylink_care.db.actions import (
    COMPLAINT_REGISTER,
    CREDIT_APPLY,
    GAVE_UP,
    INCIDENT_LINK_COMPLAINT,
    INCIDENT_OPEN,
    INCIDENT_UPDATE,
    NOTIFY_SEND,
    Action,
    ActionRepository,
    ActionStatus,
)
from skylink_care.db.approvals import ApprovalRepository
from skylink_care.db.audit import AuditRepository
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.domain.credit import CreditPolicy, load_policy
from skylink_care.executor.credits import CreditApplier
from skylink_care.executor.outcomes import (
    MissingPrerequisiteError,
    NotAttempted,
    Outcome,
    Refused,
    Skipped,
)
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.observability.audit import Auditor
from skylink_care.observability.logging import configure_logging

log = structlog.get_logger(__name__)

ACTOR = "system:executor"
MAX_BACKOFF_SECONDS = 60.0
MAX_BACKOFF_DOUBLINGS = 10
# Rejections that are final: replaying the identical request cannot succeed.
DEFINITE_REJECTIONS = frozenset({ErrorKind.INVALID, ErrorKind.NOT_FOUND, ErrorKind.GONE})
# A conflict is final only when the gateway names a business reason for it.
DEFINITE_CONFLICT_CODES = frozenset(
    {"IDEMPOTENCY_KEY_REUSED", "COMPLAINT_REF_CONFLICT", "SLOT_FULL", "ACCOUNT_SUSPENDED"}
)
# Deferrals are audited for the first attempts and then periodically, to bound the trail.
AUDIT_EVERY_ATTEMPT_UNTIL = 5
AUDIT_EVERY_NTH_ATTEMPT = 10
ACTION_FAILED = "ACTION_FAILED"
ACTION_DELAYED = "ACTION_DELAYED"
# Why a notification was not sent.
NO_CONSENT = "NO_CONSENT"
TOO_LATE = "TOO_LATE"
# The gateway's own refusal when the consent record no longer names a channel.
NO_PREFERRED_CHANNEL = "NO_PREFERRED_CHANNEL"
# Outcomes after which nobody has to be alerted.
SETTLED_QUIETLY = frozenset({ActionStatus.SUCCEEDED, ActionStatus.SKIPPED})
# Actions whose outcome the customer's conversation is waiting to hear.
WAKES_CONTACT = frozenset({CREDIT_APPLY})


type Handler = Callable[[Action], Awaitable[Outcome]]


class ActionExecutor:
    def __init__(
        self,
        actions: ActionRepository,
        audit: AuditRepository,
        handoffs: HandoffRepository,
        clients: ExecutorClients,
        *,
        batch_size: int = 20,
        visibility_seconds: float = 30.0,
        poll_seconds: float = 0.25,
        backoff_base_seconds: float = 2.0,
        alert_after_attempts: int = 8,
        give_up_after: timedelta = timedelta(hours=24),
        notification_ttl: timedelta = timedelta(minutes=30),
        approvals: ApprovalRepository | None = None,
        credit_policy: CreditPolicy | None = None,
        credit_review_all: bool = True,
        now: Clock = utc_now,
        approval_clock: Clock | None = None,
    ) -> None:
        self._actions = actions
        self._approvals = approvals
        # Business time: an approval's expiry and a billing cycle are stamped by other systems
        # and judged by this clock. Ages of ledger rows are judged by ``now``.
        self._approval_now = approval_clock or now
        self._audit = audit
        self._handoffs = handoffs
        self._clients = clients
        self._batch_size = batch_size
        self._visibility_seconds = visibility_seconds
        self._poll_seconds = poll_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._alert_after_attempts = alert_after_attempts
        self._give_up_after = give_up_after
        self._notification_ttl = notification_ttl
        self._now = now
        credits = CreditApplier(
            actions,
            approvals,
            clients.billing,
            credit_policy,
            review_all=credit_review_all,
            now=self._approval_now,
        )
        self._handlers = self._handlers_with(credits)

    def _handlers_with(self, credits: Handler) -> dict[str, Handler]:
        return {
            COMPLAINT_REGISTER: self._register_complaint,
            INCIDENT_OPEN: self._open_incident,
            INCIDENT_UPDATE: self._update_incident,
            INCIDENT_LINK_COMPLAINT: self._link_complaint,
            NOTIFY_SEND: self._send_notification,
            CREDIT_APPLY: credits,
        }

    def handler(self, action_type: str) -> Handler | None:
        """What executes actions of this type."""
        return self._handlers.get(action_type)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                handled = await self.run_once()
            except Exception:
                # A failure here (the database, usually) must not end the only writer process.
                log.exception("executor_loop_error")
                handled = 0
            if handled == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue

    async def run_once(self) -> int:
        """Execute one batch of runnable actions. Returns how many were attempted."""
        await self._expire_approvals()
        for blocked in await self._actions.block_orphans():
            try:
                await self._escalate(blocked, ActionStatus.BLOCKED, blocked.last_error or "blocked")
            except Exception:
                # The row is already BLOCKED and will not be returned again, so one failed
                # alert must not stop the alerts for the rest.
                log.exception("blocked_action_not_escalated", action_key=blocked.action_key)
        claimed = await self._actions.claim_runnable(
            limit=self._batch_size, visibility_seconds=self._visibility_seconds
        )
        await asyncio.gather(*(self._execute_safely(action) for action in claimed))
        return len(claimed)

    async def _expire_approvals(self) -> None:
        """An approval nobody gave in time ends its action; the waiting contact is woken."""
        if self._approvals is None:
            return
        try:
            expired = await self._approvals.expire_due(self._approval_now())
        except Exception:
            log.exception("approval_expiry_failed")
            return
        if expired:
            log.warning("approvals_expired", count=expired)

    async def _execute_safely(self, action: Action) -> None:
        """Run one action; an unexpected error becomes "outcome unknown", never a crash."""
        audit = Auditor(
            self._audit, contact_id=action.contact_id, correlation_id=action.correlation_id
        )
        try:
            await self._execute(action, audit)
        except Exception as exc:
            log.exception("action_execution_error", action_key=action.action_key)
            try:
                # An exception can quote the data it failed on; it is masked before it is stored.
                detail = str(redact_for_log(f"UNEXPECTED: {exc!r}"))
                await self._replay_later(action, audit, detail, unknown=True)
            except Exception:
                # Could not even record the deferral. The claim lapses and the action is
                # replayed under the same key, which is safe.
                log.exception("action_deferral_failed", action_key=action.action_key)

    async def _execute(self, action: Action, audit: Auditor) -> None:
        handler = self._handlers.get(action.action_type)
        if handler is None:
            await self._finish(action, audit, ActionStatus.FAILED, error="no handler for this type")
            return
        try:
            outcome = await handler(action)
        except MissingPrerequisiteError as exc:
            await self._finish(action, audit, ActionStatus.BLOCKED, error=str(exc))
            return
        if isinstance(outcome, Skipped):
            # What held the action up before it was skipped stays on the record.
            earlier = f" (after: {action.last_error})" if action.last_error else ""
            log.info("action_skipped", action_key=action.action_key, reason=outcome.reason)
            await self._finish(
                action, audit, ActionStatus.SKIPPED, error=f"{outcome.reason}{earlier}"
            )
        elif isinstance(outcome, NotAttempted):
            await self._after_error(action, audit, outcome.error, sent=False)
        elif isinstance(outcome, Refused):
            await self._finish(action, audit, ActionStatus.FAILED, error=outcome.reason)
        elif isinstance(outcome, Ok):
            body = (
                outcome.value.model_dump(mode="json")
                if isinstance(outcome.value, BaseModel)
                else {}
            )
            await self._finish(
                action,
                audit,
                ActionStatus.SUCCEEDED,
                external_ref=_external_ref(action.action_type, body),
                response_body=body,
                replayed=outcome.replayed,
            )
        else:
            await self._after_error(action, audit, outcome, sent=True)

    async def _after_error(self, action: Action, audit: Auditor, error: Err, *, sent: bool) -> None:
        """A definite rejection ends the action; anything else is tried again later.

        ``sent`` says whether the write itself went out. Only then can its outcome be unknown.
        """
        if _is_definite_rejection(error):
            await self._finish(
                action, audit, ActionStatus.FAILED, error=f"{error.kind.value}: {error.code}"
            )
            return
        await self._replay_later(
            action,
            audit,
            f"{error.kind.value}: {error.code or error.message}",
            unknown=sent and error.kind is not ErrorKind.CIRCUIT_OPEN,
        )

    async def _replay_later(
        self, action: Action, audit: Auditor, error: str, *, unknown: bool
    ) -> None:
        # From when it became runnable: an approval may have taken longer than the whole window.
        age = self._now() - (action.ready_at or action.created_at)
        if age >= self._give_up_after:
            await self._finish(
                action,
                audit,
                ActionStatus.FAILED,
                error=f"{GAVE_UP} after {action.attempts}: {error}",
            )
            return
        doublings = min(max(action.attempts - 1, 0), MAX_BACKOFF_DOUBLINGS)
        delay = min(self._backoff_base_seconds * 2**doublings, MAX_BACKOFF_SECONDS)
        if not await self._actions.defer(action.action_id, delay_seconds=delay, error=error):
            return  # finished by someone else in the meantime
        if action.attempts == self._alert_after_attempts:
            await self._escalate(action, ActionStatus.IN_FLIGHT, error, reason=ACTION_DELAYED)
        if (
            action.attempts <= AUDIT_EVERY_ATTEMPT_UNTIL
            or action.attempts % AUDIT_EVERY_NTH_ATTEMPT == 0
        ):
            await audit.record(
                actor=ACTOR,
                step=f"action:{action.action_type}",
                outcome="OUTCOME_UNKNOWN" if unknown else "DEFERRED",
                inputs={"action_key": action.action_key, "attempt": action.attempts},
                outputs={"error": error, "retry_in_seconds": delay},
            )

    async def _finish(
        self,
        action: Action,
        audit: Auditor,
        status: ActionStatus,
        *,
        external_ref: str | None = None,
        response_body: dict[str, Any] | None = None,
        error: str | None = None,
        replayed: bool = False,
    ) -> None:
        recorded = await self._actions.finish(
            action.action_id,
            status,
            external_ref=external_ref,
            response_body=response_body,
            error=error,
            wake_contact=action.action_type in WAKES_CONTACT,
        )
        if not recorded:
            return
        try:
            await audit.record(
                actor=ACTOR,
                step=f"action:{action.action_type}",
                outcome=status.value,
                inputs={
                    "action_key": action.action_key,
                    "attempt": action.attempts,
                    "request_hash": action.request_hash,
                    "requested_by": action.requested_by,
                },
                outputs={"external_ref": external_ref, "error": error, "replayed": replayed},
                policy_version=action.policy_version,
            )
        except Exception:
            # The outcome is already final. A lost audit line must not also cost the alert.
            log.exception("action_audit_failed", action_key=action.action_key)
        if status not in SETTLED_QUIETLY:
            await self._escalate(action, status, error or status.value)

    async def _escalate(
        self, action: Action, status: ActionStatus, error: str, *, reason: str = ACTION_FAILED
    ) -> None:
        """A write did not happen, or is taking too long: make sure a person finds out."""
        log.error(
            "action_needs_attention",
            action_key=action.action_key,
            action_type=action.action_type,
            status=status.value,
            attempts=action.attempts,
            episode_id=str(action.episode_id) if action.episode_id else None,
            error=error,
        )
        if action.contact_id is None:
            return
        await self._handoffs.open(
            action.contact_id,
            reason=reason,
            summary=(
                f"Reason: {reason}. Enterprise write {action.action_type}"
                f" ({action.action_key}) is {status.value} after {action.attempts}"
                f" attempt(s): {error}. Check the target system before repeating it by hand."
            ),
            priority=2,
        )

    # --- handlers: each sends the frozen body unchanged ------------------------------------------

    async def _register_complaint(self, action: Action) -> Result[Any]:
        return await self._clients.incident.register_complaint(
            action.request_body,
            idempotency_key=action.action_key,
            correlation_id=action.correlation_id,
        )

    async def _open_incident(self, action: Action) -> Result[Any]:
        return await self._clients.incident.open(
            action.request_body,
            idempotency_key=action.action_key,
            correlation_id=action.correlation_id,
        )

    async def _update_incident(self, action: Action) -> Result[Any]:
        return await self._clients.incident.update(
            await self._incident_id(action),
            action.request_body,
            idempotency_key=action.action_key,
            correlation_id=action.correlation_id,
        )

    async def _link_complaint(self, action: Action) -> Result[Any]:
        return await self._clients.incident.link_complaint(
            await self._incident_id(action),
            action.request_body,
            idempotency_key=action.action_key,
            correlation_id=action.correlation_id,
        )

    async def _send_notification(self, action: Action) -> Outcome:
        """Send a proactive message if, at this moment, the customer still wants it.

        A message proposed long ago is not sent late, consent is read fresh, and the gateway's
        own refusal for lack of a channel is honoured. Every decision not to send goes through
        ``_skip``, which keeps the record honest on repeat attempts.
        """
        if self._now() - action.created_at > self._notification_ttl:
            return await self._skip(action, TOO_LATE)
        customer_id = str(action.request_body.get("customer_id", ""))
        consent = await self._clients.crm.consent(customer_id, correlation_id=action.correlation_id)
        if not isinstance(consent, Ok):
            return NotAttempted(consent)
        if not consent.value.outage_notifications or consent.value.channel == "none":
            return await self._skip(action, NO_CONSENT)
        sent = await self._clients.notify.send(
            action.request_body,
            idempotency_key=action.action_key,
            correlation_id=action.correlation_id,
        )
        if (
            isinstance(sent, Err)
            and sent.kind is ErrorKind.CONFLICT
            and sent.code == NO_PREFERRED_CHANNEL
        ):
            # Consent was withdrawn between our check and the gateway's own.
            return await self._skip(action, NO_CONSENT)
        return sent

    async def _skip(self, action: Action, reason: str) -> Outcome:
        """Decide not to send, unless an earlier attempt already did.

        On a repeat attempt the earlier one may have reached the customer with its answer lost.
        The gateway is asked first, so a delivered message is never recorded as skipped (which
        would also withhold the "restored" message that depends on it).
        """
        if action.attempts > 1:
            earlier = await self._clients.notify.by_key(
                action.action_key, correlation_id=action.correlation_id
            )
            if isinstance(earlier, Ok):
                return earlier
            if earlier.kind is not ErrorKind.NOT_FOUND:
                return NotAttempted(earlier)
        return Skipped(reason)

    async def _incident_id(self, action: Action) -> str:
        if action.depends_on is None:
            raise MissingPrerequisiteError("action has no incident to refer to")
        prerequisite = await self._actions.get(action.depends_on)
        if prerequisite is None or not prerequisite.external_ref:
            raise MissingPrerequisiteError("the incident has no external reference yet")
        return prerequisite.external_ref


def _is_definite_rejection(error: Err) -> bool:
    if error.kind in DEFINITE_REJECTIONS:
        return True
    return error.kind is ErrorKind.CONFLICT and error.code in DEFINITE_CONFLICT_CODES


def _external_ref(action_type: str, body: dict[str, Any]) -> str | None:
    if action_type in {INCIDENT_OPEN, INCIDENT_UPDATE}:
        return str(body["incident_id"]) if body.get("incident_id") else None
    if action_type == COMPLAINT_REGISTER:
        return str(body["complaint_ref"]) if body.get("complaint_ref") else None
    if action_type == NOTIFY_SEND:
        return str(body["message_id"]) if body.get("message_id") else None
    if action_type == CREDIT_APPLY:
        return str(body["credit_id"]) if body.get("credit_id") else None
    return None


async def _main() -> None:
    settings = get_settings()
    configure_logging()
    database = await Database.open(settings.database_conninfo(), max_size=settings.db_pool_max)
    gateway = OssGateway(settings.oss_base_url, get_oss_keys())
    try:
        executor = ActionExecutor(
            ActionRepository(database),
            AuditRepository(database),
            HandoffRepository(database),
            gateway.for_executor(),
            approvals=ApprovalRepository(database),
            # Read at start, like every other process: a bad policy file stops the executor
            # before it can apply anything.
            credit_policy=load_policy(settings.credit_policy_path),
            credit_review_all=settings.credit_review_all,
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        log.info("executor_started")
        await executor.run_forever(stop)
    finally:
        await gateway.aclose()
        await database.close()


def run() -> None:
    asyncio.run(_main())
