"""The surge of the brief as a schedule of arrivals.

Contacts are offered at a rate that climbs from ``start_rate`` to ``peak_rate`` over the ramp
and is then held. The schedule is fixed before the run and does not react to how the platform
answers (an open workload): a customer does not wait to call because the one before is still
on hold. Every latency is measured from the instant a contact was due, so time lost queueing
in the generator counts against the platform and never in its favour.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    """Contacts a second: from ``start_rate`` up to ``peak_rate`` within the ramp, then held."""

    start_rate: float = 1.0
    peak_rate: float = 10.0
    ramp_seconds: float = 900.0
    hold_seconds: float = 900.0

    def __post_init__(self) -> None:
        if self.start_rate <= 0 or self.peak_rate < self.start_rate:
            raise ValueError("rates must be positive and the peak not below the start")
        if self.ramp_seconds < 0 or self.hold_seconds < 0 or self.duration <= 0:
            raise ValueError("the ramp and the hold must not be negative, nor both empty")

    @property
    def duration(self) -> float:
        return self.ramp_seconds + self.hold_seconds

    def rate_at(self, seconds: float) -> float:
        if seconds >= self.ramp_seconds:
            return self.peak_rate
        return self.start_rate + (self.peak_rate - self.start_rate) * seconds / self.ramp_seconds

    def offered_by(self, seconds: float) -> float:
        """How many contacts are due by this time: the area under the rate."""
        ramp = min(seconds, self.ramp_seconds)
        climbing = self.start_rate * ramp
        if self.ramp_seconds:
            climbing += (self.peak_rate - self.start_rate) * ramp**2 / (2 * self.ramp_seconds)
        return climbing + self.peak_rate * max(0.0, seconds - self.ramp_seconds)

    def time_of(self, count: float) -> float:
        """When the contact with this running count is due: the inverse of ``offered_by``."""
        in_ramp = self.offered_by(self.ramp_seconds)
        if count >= in_ramp:
            return self.ramp_seconds + (count - in_ramp) / self.peak_rate
        slope = (self.peak_rate - self.start_rate) / self.ramp_seconds
        if slope == 0:
            return count / self.start_rate
        return (-self.start_rate + math.sqrt(self.start_rate**2 + 2 * slope * count)) / slope


# The profile of the plan: 1 contact a second to 10 within 15 minutes, held for 15 minutes.
BRIEF = Profile()
# The same shape in two minutes, for a look before the long run.
QUICK = Profile(ramp_seconds=60.0, hold_seconds=60.0)
PROFILES = {"brief": BRIEF, "quick": QUICK}


def arrival_times(profile: Profile, rng: random.Random | None = None) -> list[float]:
    """When each contact is due, in seconds from the start of the run.

    With ``rng`` the arrivals are random at the profile's rate (a Poisson process: callers do
    not space themselves out, they come in bunches). Without it they are spread evenly.
    """
    total = profile.offered_by(profile.duration)
    times: list[float] = []
    count = rng.expovariate(1.0) if rng else 0.5
    while count < total:
        times.append(profile.time_of(count))
        count += rng.expovariate(1.0) if rng else 1.0
    return times
