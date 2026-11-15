"""What a handler may answer besides the gateway's own result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skylink_care.adapters.result import Err, Result


@dataclass(frozen=True)
class Skipped:
    """The action was deliberately not performed. It is finished and nobody needs to act."""

    reason: str


@dataclass(frozen=True)
class NotAttempted:
    """A check that has to precede the write could not be made, so nothing was sent."""

    error: Err


@dataclass(frozen=True)
class Refused:
    """A check made immediately before the write says it must not happen.

    Nothing was sent. The action ends as FAILED and a person is told why.
    """

    reason: str


type Outcome = Result[Any] | Skipped | NotAttempted | Refused


class MissingPrerequisiteError(Exception):
    """Something the action cannot run without is missing: another action's reference, an
    approval, or a stored request that can still be read. The action ends as BLOCKED."""
