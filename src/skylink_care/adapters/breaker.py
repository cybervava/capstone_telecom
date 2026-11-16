"""Circuit breakers: a dependency that keeps failing is rested, not called for every contact.

Without one, an API that is down costs each caller a full timeout, and in a surge that is what
uses the workers up. A circuit counts the failures of one dependency that come in a row. At
the limit it opens: calls are answered at once with ``CIRCUIT_OPEN`` ("not attempted") for a
rest period. Then one call is let through as a probe. A good answer closes the circuit; a
failure rests the dependency for another period.

"Not attempted" says nothing about the world. A reader treats it like any other failed read
(the status is unverified, never "clear"), and the executor replays a write later under the
same key, knowing that this time nothing was sent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
# The event feed is polled by its own process on its own schedule, so it is rested on its own:
# the status API being down must not stop the feed that can still prove an outage.
FEED = "network_feed"
_FEED_PATH = "/network/v1/events"

# Seconds that only ever go forwards. Rest periods are about elapsed time, not time of day.
Monotonic = Callable[[], float]


@dataclass(frozen=True)
class BreakerPolicy:
    # Failures in a row, with no answer in between, that open the circuit.
    failures: int = 5
    # How long the dependency is left alone before one call tries it again.
    rest_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.failures < 1:
            raise ValueError("failures must be 1 or more, or the circuit would never close")
        if self.rest_seconds <= 0:
            raise ValueError("rest_seconds must be more than 0, or the circuit would never rest")


@dataclass(frozen=True)
class Ticket:
    """Leave for one call to go out, and which circuit it was given by: the one before its
    latest opening, or the one after. What a call reports counts only while it is news."""

    opening: int
    probe: bool


class Circuit:
    """The circuit of one dependency. Not thread-safe: one event loop owns it.

    One circuit covers everything a gateway API offers. If one of its resources fails while
    another answers, the answers keep the circuit closed: this rests an API that is down, not
    a single resource that is broken.
    """

    def __init__(self, name: str, policy: BreakerPolicy, monotonic: Monotonic) -> None:
        self.name = name
        self._policy = policy
        self._monotonic = monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False
        # How often the circuit has opened. A call admitted before the latest opening reports
        # on a dependency as it was then.
        self._openings = 0

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return CLOSED
        return HALF_OPEN if self._probing else OPEN

    def admit(self) -> Ticket | None:
        """Leave for a call to go out now, or None while the dependency is rested. While the
        circuit is open, only one call gets it: the probe, once the rest is over."""
        if self._opened_at is None:
            return Ticket(self._openings, probe=False)
        rested = self._monotonic() - self._opened_at >= self._policy.rest_seconds
        if self._probing or not rested:
            return None
        self._probing = True
        return Ticket(self._openings, probe=True)

    def answered(self, ticket: Ticket) -> None:
        """The dependency gave an answer, whatever it said: it is there."""
        if not self._is_news(ticket):
            return
        if self._opened_at is not None:
            log.info("circuit_closed", dependency=self.name)
        self._failures, self._opened_at, self._probing = 0, None, False

    def failed(self, ticket: Ticket) -> None:
        """No usable answer: a timeout, a refused connection, a server error."""
        if not self._is_news(ticket):
            return
        self._failures += 1
        if ticket.probe or self._failures >= self._policy.failures:
            self._open(probe=ticket.probe)

    def abandoned(self, ticket: Ticket) -> None:
        """The call ended without saying anything about the dependency: it was cancelled, or
        never sent. A probe that ends this way gives its place to the next call. Nothing was
        learnt, so nothing is rested again."""
        if ticket.probe and self._is_news(ticket):
            self._probing = False

    def _is_news(self, ticket: Ticket) -> bool:
        """While the circuit is open only its probe has anything to tell it, and a call from
        before the latest opening never has."""
        if ticket.opening != self._openings:
            return False
        return self._opened_at is None or ticket.probe

    def _open(self, *, probe: bool) -> None:
        self._openings += 1
        self._opened_at, self._probing = self._monotonic(), False
        log.warning("circuit_opened", dependency=self.name, failures=self._failures, probe=probe)


class Circuits:
    """The circuits of one process, one per dependency, found by the path of a call."""

    def __init__(self, policy: BreakerPolicy, monotonic: Monotonic = time.monotonic) -> None:
        self._policy = policy
        self._monotonic = monotonic
        self._circuits: dict[str, Circuit] = {}

    def for_path(self, path: str) -> Circuit:
        name = dependency_of(path)
        if name not in self._circuits:
            self._circuits[name] = Circuit(name, self._policy, self._monotonic)
        return self._circuits[name]

    def snapshot(self) -> dict[str, str]:
        """The state of every dependency this process has called, for a person or a metric."""
        return {name: circuit.state for name, circuit in sorted(self._circuits.items())}


def dependency_of(path: str) -> str:
    """The dependency a gateway path belongs to: its first segment, the event feed apart."""
    if path.startswith(_FEED_PATH):
        return FEED
    return path.strip("/").split("/", 1)[0] or "unknown"
