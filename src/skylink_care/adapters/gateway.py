"""Builds gateway clients, one agent identity at a time.

All clients share one HTTP connection pool, but each is bound to the key of a single identity, so
the gateway can enforce least privilege per agent. A process can only build clients for
identities whose key it was given: asking for another fails at once with the variable to set.

They also share one circuit per dependency: what one agent learns about an API that is down,
every other client of the process respects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from skylink_care.adapters.breaker import BreakerPolicy, Circuits, Monotonic
from skylink_care.adapters.clients import (
    BillingReader,
    BillingWriter,
    CrmReader,
    FieldReader,
    FieldWriter,
    IncidentWriter,
    NetworkFeedReader,
    NetworkReader,
    NotifyWriter,
)
from skylink_care.adapters.http import OssHttp
from skylink_care.config import AgentIdentity, OssKeys, Settings

DEFAULT_TIMEOUT = httpx.Timeout(connect=1.0, read=3.0, write=3.0, pool=2.0)
DEFAULT_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)


@dataclass(frozen=True)
class GatewayPolicy:
    """How a process treats a dependency that fails."""

    breaker: BreakerPolicy = field(default_factory=BreakerPolicy)
    # A read that fails fast is tried again this many times. A write never is.
    read_retries: int = 1
    # The pause before another try, half to one and a half times this, so that callers that
    # failed together do not come back together.
    retry_pause_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.read_retries < 0 or self.retry_pause_seconds < 0:
            raise ValueError("read_retries and retry_pause_seconds cannot be negative")


@dataclass(frozen=True)
class ExecutorClients:
    """Everything the action executor may touch. Built only in the executor process."""

    billing: BillingWriter
    field: FieldWriter
    incident: IncidentWriter
    notify: NotifyWriter
    network: NetworkReader
    crm: CrmReader


class OssGateway:
    def __init__(
        self,
        base_url: str,
        keys: OssKeys,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        policy: GatewayPolicy | None = None,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self._keys = keys
        self._policy = policy or GatewayPolicy()
        self.circuits = Circuits(self._policy.breaker, monotonic)
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, limits=DEFAULT_LIMITS, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _as(self, identity: AgentIdentity) -> OssHttp:
        return OssHttp(
            self._client,
            api_key=self._keys.require(identity),
            identity=identity,
            circuits=self.circuits,
            read_retries=self._policy.read_retries,
            retry_pause_seconds=self._policy.retry_pause_seconds,
        )

    def crm_for_triage(self) -> CrmReader:
        return CrmReader(self._as("triage"))

    def network_for_diagnostics(self) -> NetworkReader:
        return NetworkReader(self._as("diagnostics"))

    def billing_for_resolution(self) -> BillingReader:
        return BillingReader(self._as("resolution"))

    def field_for_dispatch(self) -> FieldReader:
        return FieldReader(self._as("dispatch"))

    def network_for_mcp(self) -> NetworkReader:
        """Network Monitoring for the MCP server, under its own key. That key may read a
        node's status; the gateway refuses it a line test, which the reader also has."""
        return NetworkReader(self._as("mcp"))

    def feed_for_ingest(self) -> NetworkFeedReader:
        return NetworkFeedReader(self._as("ingest"))

    def for_executor(self) -> ExecutorClients:
        http = self._as("executor")
        return ExecutorClients(
            billing=BillingWriter(http),
            field=FieldWriter(http),
            incident=IncidentWriter(http),
            notify=NotifyWriter(http),
            network=NetworkReader(http),
            crm=CrmReader(http),
        )


def open_gateway(settings: Settings, keys: OssKeys) -> OssGateway:
    """The gateway of a process, treating failing dependencies as its settings say."""
    policy = GatewayPolicy(
        breaker=BreakerPolicy(settings.oss_breaker_failures, settings.oss_breaker_rest_seconds),
        read_retries=settings.oss_read_retries,
    )
    return OssGateway(settings.oss_base_url, keys, policy=policy)
