"""Typed outcome of a call to an enterprise system.

Adapters never raise for an expected failure. They return ``Ok`` or ``Err`` so that every caller
has to decide what a timeout, a refusal or a malformed answer means for the customer in front of
it. In particular, a failed read is never silently turned into "nothing found".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorKind(StrEnum):
    TIMEOUT = "TIMEOUT"  # no answer in time; for a write the outcome is unknown
    UNAVAILABLE = "UNAVAILABLE"  # connection failure or a 5xx
    CIRCUIT_OPEN = "CIRCUIT_OPEN"  # not attempted: the dependency is being rested
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    GONE = "GONE"  # the resource existed and was retired (a decommissioned node)
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    PROTOCOL = "PROTOCOL"  # the answer could not be parsed or broke the contract


_OUTCOME_UNKNOWN = frozenset({ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE})
_RETRYABLE = _OUTCOME_UNKNOWN | {ErrorKind.CIRCUIT_OPEN, ErrorKind.RATE_LIMITED}


@dataclass(frozen=True)
class Ok[T]:
    value: T
    status_code: int
    request_id: str | None = None
    # The gateway recognised the Idempotency-Key and returned the original response.
    replayed: bool = False


@dataclass(frozen=True)
class Err:
    kind: ErrorKind
    message: str
    status_code: int | None = None
    code: str | None = None  # the gateway's own error code, e.g. SLOT_FULL
    request_id: str | None = None

    @property
    def retryable(self) -> bool:
        """Sending the identical request again later may succeed."""
        return self.kind in _RETRYABLE

    @property
    def outcome_unknown(self) -> bool:
        """For a write: it may or may not have been applied."""
        return self.kind in _OUTCOME_UNKNOWN


type Result[T] = Ok[T] | Err
