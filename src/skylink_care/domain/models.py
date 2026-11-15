"""Shared vocabulary of the care platform: small enums and immutable records.

Nothing here performs I/O. Every other layer converts to and from these types at its boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Channel(StrEnum):
    CHAT = "chat"
    CALL_TRANSCRIPT = "call_transcript"


class ServiceType(StrEnum):
    BROADBAND = "broadband"
    MOBILE = "mobile"


class Intent(StrEnum):
    """What the customer is asking about. The cause (outage or device) is decided by evidence."""

    CONNECTIVITY = "connectivity"
    BILLING = "billing"
    OTHER = "other"


class NetworkState(StrEnum):
    """Three-state network truth. Only CONFIRMED_CLEAR unlocks line tests, guides and dispatch."""

    ACTIVE = "ACTIVE"
    CONFIRMED_CLEAR = "CONFIRMED_CLEAR"
    UNKNOWN = "UNKNOWN"


class MappingStatus(StrEnum):
    """Outcome of resolving a service address to a network node."""

    RESOLVED = "RESOLVED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    STALE = "STALE"


@dataclass(frozen=True)
class OutageInfo:
    """One outage episode as reported by the network systems."""

    outage_id: str
    version: int
    cause: str
    planned: bool
    started_at: datetime
    eta: datetime | None
    eta_source: str | None
    resolved_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


@dataclass(frozen=True)
class NodeMapping:
    """A service's node as far as the operational mapping can tell."""

    status: MappingStatus
    node_id: str | None
    candidate_node_ids: tuple[str, ...]
    map_version: str | None

    @property
    def is_resolved(self) -> bool:
        return self.status is MappingStatus.RESOLVED and self.node_id is not None
