"""In-process token-bucket rate limiting for the public endpoints.

Two rules matter more than the mechanism. A caller's *first* contact is never limited: every
contact must receive a complaint reference, surge or not. And limits are per caller (session or
customer), never global, so a surge of many customers is not mistaken for abuse by one.

This limiter lives in one API process. With several replicas the same rule belongs at the edge
(API gateway or WAF) or in a shared store.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        max_keys: int = 50_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(capacity)
        self._refill = refill_per_second
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(self, key: str) -> bool:
        """Spend one token for ``key``. False when the caller has none left."""
        now = self._clock()
        bucket = self._buckets.pop(key, None) or _Bucket(self._capacity, now)
        bucket.tokens = min(self._capacity, bucket.tokens + (now - bucket.updated) * self._refill)
        bucket.updated = now
        self._buckets[key] = bucket  # most recently used goes last
        if len(self._buckets) > self._max_keys:
            self._evict(now, keep=key)
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def _evict(self, now: float, *, keep: str) -> None:
        """Drop one bucket to stay within the memory bound.

        Prefer a bucket that has refilled completely: forgetting it changes nothing for its
        caller. Only when no such bucket exists is a limited one dropped, and then the newest,
        so creating many keys cannot be used to reset an established caller's limit.
        """
        for candidate, bucket in self._buckets.items():
            refilled = bucket.tokens + (now - bucket.updated) * self._refill
            if candidate != keep and refilled >= self._capacity:
                del self._buckets[candidate]
                return
        # Everyone is still limited. Drop the newest other key: under a flood that is one of the
        # flooder's own, and a long-standing limited caller keeps its bucket.
        newest = next(key for key in reversed(self._buckets) if key != keep)
        del self._buckets[newest]

    def has_history(self, key: str) -> bool:
        return key in self._buckets
