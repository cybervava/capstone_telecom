"""Builds gateway clients, one agent identity at a time.

All clients share one HTTP connection pool, but each is bound to the key of a single identity, so
the gateway can enforce least privilege per agent. A process can only build clients for
identities whose key it was given: asking for another fails at once with the variable to set.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

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
from skylink_care.config import AgentIdentity, OssKeys

DEFAULT_TIMEOUT = httpx.Timeout(connect=1.0, read=3.0, write=3.0, pool=2.0)
DEFAULT_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)


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
    ) -> None:
        self._keys = keys
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, limits=DEFAULT_LIMITS, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _as(self, identity: AgentIdentity) -> OssHttp:
        return OssHttp(self._client, api_key=self._keys.require(identity), identity=identity)

    def crm_for_triage(self) -> CrmReader:
        return CrmReader(self._as("triage"))

    def network_for_diagnostics(self) -> NetworkReader:
        return NetworkReader(self._as("diagnostics"))

    def billing_for_resolution(self) -> BillingReader:
        return BillingReader(self._as("resolution"))

    def field_for_dispatch(self) -> FieldReader:
        return FieldReader(self._as("dispatch"))

    def crm_for_notification(self) -> CrmReader:
        return CrmReader(self._as("notification"))

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
