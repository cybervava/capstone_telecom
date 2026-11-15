"""Gateway middleware: request ids, fault injection and per-API request counters."""

from __future__ import annotations

import random
import threading
import uuid
from collections import Counter

from fastapi import FastAPI, Request, Response

from mock_oss.chaos import CallNext, ChaosRules, apply_rule, target_for_path
from mock_oss.clock import Clock

MAX_REQUEST_ID = 128


class RequestCounters:
    """In-memory request counts per API and HTTP status (the outcome)."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def record(self, api: str, status: int) -> None:
        with self._lock:
            self._counts[(api, str(status))] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            items = sorted(self._counts.items())
        result: dict[str, dict[str, int]] = {}
        for (api, outcome), count in items:
            result.setdefault(api, {})[outcome] = count
        return result

    def reset(self) -> None:
        with self._lock:
            self._counts = Counter()


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied and len(supplied) <= MAX_REQUEST_ID and supplied.isprintable():
        return supplied
    return uuid.uuid4().hex


def install_gateway_middleware(app: FastAPI) -> None:
    rng = random.Random()

    @app.middleware("http")
    async def gateway(request: Request, call_next: CallNext) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        target = target_for_path(request.url.path)
        clock: Clock = app.state.clock
        rules: ChaosRules = app.state.chaos
        rule = rules.for_target(target, clock.now()) if target is not None else None
        if rule is not None:
            response = await apply_rule(rule, request, call_next, clock, rng)
        else:
            response = await call_next(request)
        if target is not None:
            counters: RequestCounters = app.state.counters
            counters.record(target, response.status_code)
        response.headers["X-Request-ID"] = request_id
        return response
