"""Fixtures that assemble the care platform itself on top of PostgreSQL and the mock gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from mock_oss.clock import ManualClock
from skylink_care.adapters.gateway import OssGateway
from skylink_care.config import Settings
from skylink_care.db.actions import ActionRepository
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import IntakeResult, NewContact
from skylink_care.db.database import Database
from skylink_care.db.handoffs import HandoffRepository
from skylink_care.domain.models import Channel
from skylink_care.executor.worker import ActionExecutor
from skylink_care.ingest.consumer import FeedConsumer, PollOutcome
from skylink_care.ingest.worker import build_consumer
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.offline import OfflineProvider
from skylink_care.orchestrator.graph import build_graph
from skylink_care.orchestrator.worker import OrchestratorWorker
from skylink_care.pipelines.reference_loader import load_reference
from skylink_care.runtime import OrchestratorRuntime, build_orchestrator_runtime


@dataclass
class Platform:
    """Handles to everything a journey test needs, with helpers to drive the workers by hand."""

    runtime: OrchestratorRuntime
    worker: OrchestratorWorker
    executor: ActionExecutor
    checkpointer: AsyncPostgresSaver
    # The NOC feed consumer. It runs only when a test calls ``feed()``: a test that never does
    # is a test of the platform with no feed evidence at all.
    ingest: FeedConsumer
    clock: ManualClock

    async def feed(self) -> PollOutcome:
        """Let the feed consumer read everything the NOC has published so far."""
        outcome = await self.ingest.catch_up()
        assert outcome.error is None, f"feed poll failed: {outcome.error}"
        return outcome

    def advance(self, seconds: float) -> None:
        """Move the clock shared by the gateway and the platform."""
        self.clock.advance(seconds)

    async def contact(self, text: str, **overrides: Any) -> IntakeResult:
        fields: dict[str, Any] = {
            "channel": Channel.CHAT,
            "client_request_id": uuid4().hex,
            "text": text,
            "correlation_id": uuid4().hex,
            "received_at": self.runtime.now(),
        }
        return await self.runtime.contacts.accept(NewContact(**{**fields, **overrides}))

    async def say(self, contact_id: UUID, text: str) -> None:
        """A follow-up message from the customer."""
        key = uuid4().hex
        await self.runtime.messages.add(
            contact_id, direction="inbound", author="customer", body=text, dedupe_key=f"in:{key}"
        )
        await self.runtime.inbox.add(contact_id, "customer.message", dedupe_key=f"msg:{key}")

    async def settle(self, rounds: int = 12) -> None:
        """Run workers until nothing is left to do (bounded, so a loop fails the test).

        Deferred actions become runnable again after a short back-off, so an idle pass waits
        briefly and checks once more before declaring the platform settled.
        """
        idle = 0
        for _ in range(rounds * 4):
            ran = await self.worker.run_once()
            executed = await self.executor.run_once()
            if ran or executed:
                idle = 0
                continue
            idle += 1
            if idle >= 3:
                return
            await asyncio.sleep(0.12)
        raise AssertionError("the platform did not settle")

    def restarted(self) -> Platform:
        """The same platform after a process restart: new worker and graph objects, same data."""
        checkpointer = AsyncPostgresSaver(self.runtime.db.pool)
        worker = OrchestratorWorker(
            self.runtime,
            build_graph(self.runtime, checkpointer),
            checkpointer,
            retry_backoff_seconds=0.05,
        )
        return Platform(self.runtime, worker, self.executor, checkpointer, self.ingest, self.clock)

    async def replies(self, contact_id: UUID) -> list[str]:
        messages = await self.runtime.messages.list_for_contact(contact_id)
        return [message.body for message in messages if message.direction == "outbound"]


@pytest.fixture
def care_settings(database_conninfo: str) -> Settings:
    return Settings(
        env="test",
        db_password="unused-by-tests",
        credit_policy_path=Path("data/policies/credit_policy.v0.yaml"),
        llm_provider="offline",
    )


@pytest.fixture
async def platform(
    db: Database,
    datapack: Path,
    gateway: OssGateway,
    care_settings: Settings,
    stack_clock: ManualClock,
) -> Platform:
    await load_reference(db, datapack)

    def now() -> datetime:
        return stack_clock.now().astimezone(UTC)

    runtime = build_orchestrator_runtime(
        care_settings, db, gateway, llm=LLMGateway(OfflineProvider()), now=now
    )
    checkpointer = AsyncPostgresSaver(db.pool)
    worker = OrchestratorWorker(
        runtime, build_graph(runtime, checkpointer), checkpointer, retry_backoff_seconds=0.05
    )
    executor = ActionExecutor(
        ActionRepository(db),
        AuditRepository(db),
        HandoffRepository(db),
        gateway.for_executor(),
        visibility_seconds=0.2,
        backoff_base_seconds=0.05,
    )
    ingest = build_consumer(care_settings, db, gateway, now=now)
    return Platform(runtime, worker, executor, checkpointer, ingest, stack_clock)


def now_utc() -> datetime:
    return datetime.now(UTC)
