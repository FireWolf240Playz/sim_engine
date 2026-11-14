"""Arrivals follow the configured rate, including where it changes.

Measured before these tests existed:

- Each gap was drawn from the rate at the moment it started, so a gap begun
  at a low base rate ran straight over the start of a spike. At 0.05 rps
  base, a 5 s spike at 50 rps (250 expected arrivals) delivered 27.6 on
  average and was missed entirely in 158 of 200 seeds.
- Exponential growth had the same lag, and no ceiling: 20 rps doubling every
  30 s (the default) is 20,480 rps after 300 s and 21 million after 600 s.
- A spike given as one or two of its three fields was silently ignored.

A constant rate with no spike is untouched: same draws, same arrival times.
"""

from __future__ import annotations

import math
from collections.abc import Generator
from typing import Any

import numpy as np
import pytest
import simpy
from pydantic import ValidationError

from sim_core import SimulationConfig
from sim_core.config import MAX_EXPECTED_REQUESTS, PatternType, TrafficPattern
from sim_core.traffic import generate_traffic


def _arrivals(pattern: TrafficPattern, seed: int, until: float | None = None) -> list[float]:
    env = simpy.Environment()
    times: list[float] = []

    def request(_request_id: str) -> Generator[Any]:
        times.append(env.now)
        yield env.timeout(0)

    env.process(generate_traffic(env, pattern, request, np.random.default_rng(seed)))
    env.run(until=until if until is not None else pattern.start_time + pattern.duration + 1.0)
    return times


def _mean_count(pattern: TrafficPattern, lo: float, hi: float, seeds: int) -> tuple[float, int]:
    """(mean arrivals in [lo, hi) over the seeds, seeds with none at all)."""
    counts = [sum(1 for t in _arrivals(pattern, seed) if lo <= t < hi) for seed in range(seeds)]
    return float(np.mean(counts)), sum(1 for c in counts if c == 0)


# ---------------------------------------------------------------------------
# the arrival process
# ---------------------------------------------------------------------------

def test_a_constant_rate_keeps_its_exact_arrival_times() -> None:
    """Every pinned score in the repo rests on this stream not moving."""
    pattern = TrafficPattern(base_rps=5.0, duration=20.0)
    gaps = np.random.default_rng(3).exponential(1.0 / 5.0, size=400)
    expected = [float(t) for t in np.cumsum(gaps) if t < 20.0]
    assert _arrivals(pattern, seed=3) == pytest.approx(expected, abs=1e-9)
    assert len(expected) > 60


def test_a_spike_is_delivered_at_a_low_base_rate() -> None:
    pattern = TrafficPattern(
        base_rps=0.05, duration=100.0, spike_rps=50.0, spike_start=40.0, spike_duration=5.0
    )
    mean, missed = _mean_count(pattern, 40.0, 45.0, seeds=200)
    assert missed == 0
    assert mean == pytest.approx(250.0, rel=0.02)


def test_the_rate_returns_to_base_when_the_spike_ends() -> None:
    pattern = TrafficPattern(
        base_rps=0.05, duration=100.0, spike_rps=50.0, spike_start=10.0, spike_duration=5.0
    )
    mean, _ = _mean_count(pattern, 15.0, 100.0, seeds=300)
    assert mean == pytest.approx(0.05 * 85.0, rel=0.1)


def test_exponential_growth_delivers_its_integral() -> None:
    """0.2 rps doubling every 5 s for 40 s: (0.2 * 5 / ln 2) * (2^8 - 1) arrivals."""
    pattern = TrafficPattern(
        base_rps=0.2, duration=40.0, pattern_type=PatternType.EXPONENTIAL, growth_period=5.0
    )
    expected = 0.2 * 5.0 / math.log(2.0) * (2.0**8 - 1.0)
    mean, _ = _mean_count(pattern, 0.0, 40.0, seeds=300)
    assert mean == pytest.approx(expected, rel=0.02)
    assert pattern.expected_requests(40.0) == pytest.approx(expected)


def test_traffic_honours_its_own_window() -> None:
    pattern = TrafficPattern(base_rps=20.0, duration=10.0, start_time=5.0)
    times = _arrivals(pattern, seed=1, until=60.0)
    assert times and min(times) >= 5.0 and max(times) < 15.0


# ---------------------------------------------------------------------------
# what a profile adds up to
# ---------------------------------------------------------------------------

def test_expected_requests_adds_up_the_profile() -> None:
    constant = TrafficPattern(base_rps=4.0, duration=50.0)
    assert constant.expected_requests(50.0) == pytest.approx(200.0)
    assert constant.expected_requests(20.0) == pytest.approx(80.0)  # the run ends first
    assert constant.peak_rps(50.0) == 4.0

    spiked = TrafficPattern(
        base_rps=4.0, duration=50.0, spike_rps=10.0, spike_start=10.0, spike_duration=5.0
    )
    assert spiked.expected_requests(50.0) == pytest.approx(4.0 * 45.0 + 10.0 * 5.0)
    assert spiked.expected_requests(12.0) == pytest.approx(4.0 * 10.0 + 10.0 * 2.0)
    assert spiked.peak_rps(50.0) == 10.0
    assert spiked.peak_rps(8.0) == 4.0  # the run ends before the spike

    late = TrafficPattern(base_rps=4.0, duration=50.0, start_time=30.0)
    assert late.expected_requests(40.0) == pytest.approx(40.0)

    growing = TrafficPattern(
        base_rps=20.0, duration=300.0, pattern_type=PatternType.EXPONENTIAL
    )
    assert growing.peak_rps(300.0) == pytest.approx(20.0 * 2.0**10)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spike",
    [
        {"spike_rps": 5.0},
        {"spike_rps": 5.0, "spike_start": 2.0},
        {"spike_start": 2.0, "spike_duration": 3.0},
    ],
)
def test_a_partial_spike_is_rejected(spike: dict[str, float]) -> None:
    with pytest.raises(ValidationError, match="spike_rps, spike_start and spike_duration"):
        TrafficPattern(base_rps=1.0, duration=10.0, **spike)


def test_a_spike_outside_the_traffic_window_is_rejected() -> None:
    with pytest.raises(ValidationError, match="never opens"):
        TrafficPattern(
            base_rps=1.0, duration=10.0, spike_rps=5.0, spike_start=50.0, spike_duration=5.0
        )


def _config(traffic: dict[str, Any], duration: float) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "seed": 1,
            "duration": duration,
            "topology": {"nodes": [{"name": "solo", "max_capacity": 4, "service_time": 0.1}]},
            "traffic": traffic,
        }
    )


def test_traffic_that_starts_after_the_run_ends_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no request would ever arrive"):
        _config({"base_rps": 1.0, "duration": 10.0, "start_time": 20.0}, duration=10.0)


def test_runaway_growth_is_rejected_with_its_numbers() -> None:
    traffic = {"base_rps": 20.0, "duration": 600.0, "pattern_type": "exponential"}
    with pytest.raises(ValidationError) as raised:
        _config(traffic, duration=600.0)
    message = str(raised.value)
    assert "growth_period" in message
    assert f"{MAX_EXPECTED_REQUESTS:,}" in message
    assert "rps" in message


def test_growth_inside_the_limit_is_accepted() -> None:
    traffic = {"base_rps": 20.0, "duration": 120.0, "pattern_type": "exponential"}
    config = _config(traffic, duration=120.0)
    assert config.traffic.expected_requests(120.0) < MAX_EXPECTED_REQUESTS
