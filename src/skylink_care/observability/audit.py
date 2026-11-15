"""Audit writer bound to one contact, plus timing helpers for tool and model calls.

Everything written here passes through ``redact_for_log`` first, so a phone number or an email
that slipped into an input cannot reach the audit table.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from typing import Any
from uuid import UUID

from skylink_care.adapters.result import Ok, Result
from skylink_care.db.audit import AuditEntry, AuditRepository
from skylink_care.guardrails.pii import redact_for_log
from skylink_care.llm.base import CallRecord


class Auditor:
    def __init__(
        self, repository: AuditRepository, *, contact_id: UUID | None, correlation_id: str
    ) -> None:
        self._repository = repository
        self._contact_id = contact_id
        self._correlation_id = correlation_id

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    async def record(
        self,
        *,
        actor: str,
        step: str,
        outcome: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        call: CallRecord | None = None,
        policy_version: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        safe_inputs = redact_for_log(inputs or {})
        safe_outputs = redact_for_log(outputs or {})
        await self._repository.append(
            AuditEntry(
                correlation_id=self._correlation_id,
                contact_id=self._contact_id,
                actor=actor,
                step=step,
                outcome=outcome,
                inputs=safe_inputs if isinstance(safe_inputs, dict) else {},
                outputs=safe_outputs if isinstance(safe_outputs, dict) else {},
                model=call.model if call else None,
                prompt_version=call.prompt_version if call else None,
                policy_version=policy_version,
                duration_ms=duration_ms if duration_ms is not None else _latency(call),
            )
        )

    async def tool[T](
        self, *, actor: str, tool: str, inputs: dict[str, Any], call: Awaitable[Result[T]]
    ) -> Result[T]:
        """Await a tool call, then record who called what, how it ended and how long it took."""
        started = time.monotonic()
        result = await call
        duration_ms = int((time.monotonic() - started) * 1000)
        if isinstance(result, Ok):
            outcome, outputs = "OK", {"status": result.status_code, "request_id": result.request_id}
        else:
            outcome = result.kind.value
            outputs = {
                "status": result.status_code,
                "code": result.code,
                "request_id": result.request_id,
            }
        await self.record(
            actor=actor,
            step=f"tool:{tool}",
            outcome=outcome,
            inputs=inputs,
            outputs=outputs,
            duration_ms=duration_ms,
        )
        return result


def _latency(call: CallRecord | None) -> int | None:
    return call.latency_ms if call else None
