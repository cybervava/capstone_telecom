"""The surge profile: how many contacts are offered, and when."""

from __future__ import annotations

import itertools
import random

import pytest
from loadtest.profile import BRIEF, QUICK, Profile, arrival_times


def test_the_profile_of_the_brief_offers_what_its_shape_says() -> None:
    """1 a second to 10 within 15 minutes is 4,950 contacts; 10 a second for 15 more is 9,000."""
    assert BRIEF.duration == 1800
    assert BRIEF.offered_by(900) == pytest.approx(4950)
    assert BRIEF.offered_by(1800) == pytest.approx(13950)
    assert (BRIEF.rate_at(0), BRIEF.rate_at(450), BRIEF.rate_at(900)) == (1.0, 5.5, 10.0)
    assert BRIEF.rate_at(1700) == 10.0


@pytest.mark.parametrize("count", [0.5, 100, 4949.5, 4950, 5000, 13949.5])
def test_the_time_a_contact_is_due_is_the_inverse_of_the_count_due_by_then(count: float) -> None:
    assert BRIEF.offered_by(BRIEF.time_of(count)) == pytest.approx(count)


def test_evenly_spread_arrivals_follow_the_ramp_and_then_the_hold() -> None:
    times = arrival_times(QUICK)

    assert len(times) == 930  # 330 on the ramp, 600 on the hold
    assert times == sorted(times)
    assert 0 < times[0] < 1
    assert times[-1] < QUICK.duration
    first_ten_seconds = sum(1 for t in times if t < 10)
    last_ten_seconds = sum(1 for t in times if t >= QUICK.duration - 10)
    assert first_ten_seconds == pytest.approx(17.5, abs=1)  # about 1 a second, climbing
    assert last_ten_seconds == 100


def test_random_arrivals_keep_the_rate_and_are_the_same_for_the_same_seed() -> None:
    once = arrival_times(BRIEF, random.Random(7))
    again = arrival_times(BRIEF, random.Random(7))
    other = arrival_times(BRIEF, random.Random(8))

    assert once == again
    assert once != other
    assert once == sorted(once)
    assert len(once) == pytest.approx(13950, rel=0.03)
    held = [t for t in once if t >= 900]
    assert len(held) / 900 == pytest.approx(10.0, rel=0.05)
    # Bunched, as callers are: some gaps are several times the average of a tenth of a second.
    gaps = [later - earlier for earlier, later in itertools.pairwise(held)]
    assert max(gaps) > 0.5


def test_a_flat_profile_has_no_ramp() -> None:
    flat = Profile(start_rate=5.0, peak_rate=5.0, ramp_seconds=0.0, hold_seconds=10.0)

    assert len(arrival_times(flat)) == 50
    assert flat.time_of(25) == pytest.approx(5.0)


@pytest.mark.parametrize(
    "bad",
    [
        {"start_rate": 0.0},
        {"start_rate": 5.0, "peak_rate": 1.0},
        {"ramp_seconds": -1.0},
        {"ramp_seconds": 0.0, "hold_seconds": 0.0},
    ],
)
def test_a_profile_that_makes_no_sense_is_refused(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="must"):
        Profile(**bad)
