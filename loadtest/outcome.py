"""What became of each offered contact, and whether the run met its targets.

The targets are those of the plan: at least 99.5% of the contacts that were offered are
accepted, every complaint reference is in the caller's hands within 120 seconds, and nobody's
first contact is turned away by a rate limit. The regulator's rule read strictly allows no
contact at all to be left without a reference after two minutes, so that count is given beside
the share.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Offered:
    """One offered contact, as its caller saw it. Times are seconds from when it was due."""

    due: float  # seconds from the start of the run
    route: str  # "web": the customer, signed in. "channel": a contact centre, for the caller
    affected: bool  # on a node of the outage
    # A reference in hand within the time allowed. One that came later is not accepted, and
    # its time is kept.
    accepted: bool = False
    rate_limited: bool = False
    reference_seconds: float | None = None
    attempts: int = 0
    told_delayed: bool = False
    # Only for callers who stay for the reply (web): when it was seen, and which message.
    reply_seconds: float | None = None
    reply_template: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Targets:
    accepted_share: float = 0.995
    reference_seconds: float = 120.0


@dataclass(frozen=True)
class Criterion:
    name: str
    target: str
    measured: str
    met: bool


def percentile(values: Sequence[float], share: float) -> float | None:
    """The value below which this share of the values lie (nearest rank). None of nothing."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def judge(offered: Sequence[Offered], targets: Targets) -> list[Criterion]:
    """The three targets of the plan against what the callers saw."""
    accepted = [one for one in offered if one.accepted]
    share = len(accepted) / len(offered) if offered else 0.0
    # Every reference that was issued, in time or not.
    references = [one.reference_seconds for one in offered if one.reference_seconds is not None]
    slowest = max(references, default=None)
    limited = sum(1 for one in offered if one.rate_limited)
    return [
        Criterion(
            "Offered contacts accepted",
            f"at least {targets.accepted_share:.1%}",
            f"{share:.2%} ({len(accepted):,} of {len(offered):,})",
            bool(offered) and share >= targets.accepted_share,
        ),
        Criterion(
            "Slowest complaint reference",
            f"within {targets.reference_seconds:.0f} s",
            "none issued" if slowest is None else f"{slowest:.2f} s",
            slowest is not None and slowest <= targets.reference_seconds,
        ),
        Criterion("First contacts turned away by a rate limit", "none", str(limited), limited == 0),
    ]


def summarise(offered: Sequence[Offered], targets: Targets) -> dict[str, Any]:
    """The figures of a run, for the report and for whoever reads the JSON."""
    accepted = [one for one in offered if one.accepted]
    references = [one.reference_seconds for one in offered if one.reference_seconds is not None]
    stayed = [one for one in accepted if one.route == "web"]
    replies = [one.reply_seconds for one in stayed if one.reply_seconds is not None]
    errors: dict[str, int] = {}
    for one in offered:
        if not one.accepted and one.error:
            errors[one.error] = errors.get(one.error, 0) + 1
    return {
        "offered": len(offered),
        "accepted": len(accepted),
        # The regulator's rule read strictly: nobody may be left without a reference.
        "without_reference_in_time": sum(
            1
            for one in offered
            if one.reference_seconds is None or one.reference_seconds > targets.reference_seconds
        ),
        "rate_limited": sum(1 for one in offered if one.rate_limited),
        "sent_more_than_once": sum(1 for one in offered if one.attempts > 1),
        "told_delayed": sum(1 for one in accepted if one.told_delayed),
        "by_route": {
            route: sum(1 for one in offered if one.route == route)
            for route in sorted({one.route for one in offered})
        },
        "affected": sum(1 for one in offered if one.affected),
        "reference_seconds": _spread(references),
        "stayed_for_the_reply": len(stayed),
        "saw_a_reply": len(replies),
        "reply_seconds": _spread(replies),
        "not_accepted_because": dict(sorted(errors.items())),
    }


def _spread(values: Sequence[float]) -> dict[str, float | None]:
    def at(share: float) -> float | None:
        found = percentile(values, share)
        return None if found is None else round(found, 3)

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99), "max": at(1.0)}
