"""Rate limiter: per-caller buckets, bounded memory, and no cheap way to reset someone's limit."""

from __future__ import annotations

from skylink_care.api.ratelimit import RateLimiter, per_minute


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_caller_is_limited_and_then_refilled_over_time() -> None:
    clock = Clock()
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, clock=clock)

    burst = [limiter.allow("a") for _ in range(3)]
    clock.now += 1.0
    after_a_second = limiter.allow("a")

    assert burst == [True, True, False]
    assert after_a_second


def test_callers_do_not_share_a_bucket() -> None:
    limiter = RateLimiter(capacity=1, refill_per_second=0.0, clock=Clock())

    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_flooding_with_new_keys_does_not_reset_a_limited_caller() -> None:
    clock = Clock()
    limiter = RateLimiter(capacity=1, refill_per_second=0.001, max_keys=10, clock=clock)
    limiter.allow("victim-limited")  # now empty and far from refilled
    for n in range(8):
        limiter.allow(f"idle-{n}")
    clock.now += 2000.0  # the idle callers have refilled; the limited one is kept anyway
    limiter.allow("victim-limited")
    for n in range(50):
        limiter.allow(f"attacker-{n}")

    assert limiter.has_history("victim-limited")
    assert not limiter.allow("victim-limited")


def test_memory_stays_bounded() -> None:
    limiter = RateLimiter(capacity=1, refill_per_second=0.0, max_keys=100, clock=Clock())

    for n in range(5000):
        limiter.allow(f"key-{n}")

    assert len(limiter._buckets) <= 100


def test_so_many_at_once_and_so_many_more_each_minute() -> None:
    clock = Clock()
    limiter = per_minute(5, clock=clock)

    at_once = [limiter.allow("caller") for _ in range(6)]
    clock.now += 60
    a_minute_later = [limiter.allow("caller") for _ in range(6)]

    assert at_once == [True] * 5 + [False]
    assert a_minute_later == [True] * 5 + [False]
