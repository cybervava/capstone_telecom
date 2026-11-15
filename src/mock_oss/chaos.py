"""Fault injection rules (one per target) and how they alter a matching request."""

from __future__ import annotations

import random
import threading
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Request, Response

from mock_oss.clock import Clock, iso
from mock_oss.errors import error_response

Target = Literal["network", "network_feed", "crm", "billing", "field", "incident", "notify"]
Mode = Literal["ok", "down", "timeout", "latency", "error_rate", "response_loss"]
CallNext = Callable[[Request], Awaitable[Response]]

WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_PREFIXES: list[tuple[str, Target]] = [
    ("/network/v1/events", "network_feed"),
    ("/network/", "network"),
    ("/crm/", "crm"),
    ("/billing/", "billing"),
    ("/field/", "field"),
    ("/incident/", "incident"),
    ("/notify/", "notify"),
]


def target_for_path(path: str) -> Target | None:
    """The API a path belongs to; ``None`` for /admin, /health and unknown paths."""
    for prefix, target in _PREFIXES:
        if path.startswith(prefix):
            return target
    return None


@dataclass(frozen=True)
class ChaosRule:
    target: Target
    mode: Mode
    latency_ms: int = 0
    error_rate: float = 0.0
    hold_seconds: float = 30.0
    expires_at: datetime | None = None

    def is_live(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at

    def describe(self) -> dict[str, object]:
        return {
            "target": self.target,
            "mode": self.mode,
            "latency_ms": self.latency_ms,
            "error_rate": self.error_rate,
            "hold_seconds": self.hold_seconds,
            "expires_at": iso(self.expires_at) if self.expires_at else None,
        }


def build_rule(
    target: Target,
    mode: Mode,
    *,
    latency_ms: int,
    error_rate: float,
    hold_seconds: float,
    ttl_seconds: float | None,
    now: datetime,
) -> ChaosRule:
    expires = now + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
    return ChaosRule(target, mode, latency_ms, error_rate, hold_seconds, expires)


class ChaosRules:
    """Thread-safe in-memory rule table."""

    def __init__(self) -> None:
        self._rules: dict[str, ChaosRule] = {}
        self._lock = threading.Lock()

    def apply(self, rule: ChaosRule) -> None:
        with self._lock:
            if rule.mode == "ok":
                self._rules.pop(rule.target, None)
            else:
                self._rules[rule.target] = rule

    def clear(self) -> int:
        with self._lock:
            count = len(self._rules)
            self._rules = {}
            return count

    def active(self, now: datetime) -> list[ChaosRule]:
        with self._lock:
            self._rules = {t: r for t, r in self._rules.items() if r.is_live(now)}
            return sorted(self._rules.values(), key=lambda r: r.target)

    def for_target(self, target: Target, now: datetime) -> ChaosRule | None:
        with self._lock:
            rule = self._rules.get(target)
            if rule is not None and not rule.is_live(now):
                del self._rules[target]
                return None
            return rule


async def _drain(response: Response) -> None:
    body: AsyncIterable[object] | None = getattr(response, "body_iterator", None)
    if body is not None:
        async for _ in body:
            pass


def _timeout() -> Response:
    return error_response(504, "GATEWAY_TIMEOUT", "upstream did not respond in time (injected)")


async def apply_rule(
    rule: ChaosRule, request: Request, call_next: CallNext, clock: Clock, rng: random.Random
) -> Response:
    is_write = request.method in WRITE_METHODS
    if rule.mode == "down":
        return error_response(503, "UPSTREAM_UNAVAILABLE", f"{rule.target} is down (injected)")
    if rule.mode == "timeout" or (rule.mode == "response_loss" and not is_write):
        await clock.sleep(rule.hold_seconds)
        return _timeout()
    if rule.mode == "latency":
        await clock.sleep(rule.latency_ms / 1000)
        return await call_next(request)
    if rule.mode == "error_rate":
        if rng.random() < rule.error_rate:
            return error_response(500, "INTERNAL_ERROR", "injected upstream error")
        return await call_next(request)
    if rule.mode == "response_loss":
        await _drain(await call_next(request))
        return _timeout()
    return await call_next(request)
