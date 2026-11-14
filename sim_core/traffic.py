"""Traffic generation: Poisson arrivals whose rate follows a configured profile.

``generate_traffic`` is a SimPy process: it is started with
``env.process(generate_traffic(...))`` and yields ``env.timeout()`` events to
advance the clock, spawning one request process per arrival. It honours both
``start_time`` and ``duration``, and models arrivals as a Poisson process
whose rate changes over time (a non-homogeneous one).

The rate law is fixed between *breakpoints* (where a spike opens or closes),
and each stretch is sampled exactly:

- a constant stretch draws exponential gaps at its rate;
- an exponentially growing stretch inverts its own integral, so a gap
  accounts for the rate rising while it elapses;
- a gap that would cross a breakpoint is discarded and the clock moves to the
  breakpoint. A Poisson process has no memory, so starting afresh there under
  the new rate is exact, and it is what stops a long gap drawn at a low base
  rate from running straight over the start of a spike.

A constant rate with no spike has no breakpoint: it draws the same values in
the same order as it always did.
"""

from __future__ import annotations

import math
from typing import Callable, Generator

import numpy as np
import simpy

from sim_core.config import PatternType, TrafficPattern

# A factory that, given a unique id, returns a request-process generator to
# be scheduled on the environment (e.g. ``CloudSimulator.serve_request``).
RequestFactory = Callable[[str], Generator[object, None, None]]


def _in_spike(pattern: TrafficPattern, t: float) -> bool:
    """Whether the spike window (when one is configured) is active at ``t``."""
    return (
        pattern.spike_rps is not None
        and pattern.spike_start is not None
        and pattern.spike_duration is not None
        and pattern.spike_start <= t < pattern.spike_start + pattern.spike_duration
    )


def _target_rps(pattern: TrafficPattern, t: float) -> float:
    """Return the target arrival rate (requests/second) at time ``t``.

    Returns ``0.0`` before the traffic window opens.
    """
    if t < pattern.start_time:
        return 0.0

    # A spike window (when configured) overrides the base rate while active.
    if _in_spike(pattern, t):
        assert pattern.spike_rps is not None
        return pattern.spike_rps

    if pattern.pattern_type is PatternType.EXPONENTIAL:
        elapsed = t - pattern.start_time
        return pattern.base_rps * float(2.0 ** (elapsed / pattern.growth_period))
    return pattern.base_rps


def _breakpoints(pattern: TrafficPattern, end: float) -> list[float]:
    """Times inside the traffic window where the rate law changes, ascending."""
    if pattern.spike_start is None or pattern.spike_duration is None:
        return []
    edges = (pattern.spike_start, pattern.spike_start + pattern.spike_duration)
    return sorted(t for t in edges if pattern.start_time < t < end)


def _next_gap(pattern: TrafficPattern, now: float, rng: np.random.Generator) -> float:
    """Time to the next arrival under the rate law in force at ``now``.

    Exact for as long as that law holds; the caller discards a gap that
    reaches the next breakpoint.
    """
    rate = _target_rps(pattern, now)
    if pattern.pattern_type is PatternType.EXPONENTIAL and not _in_spike(pattern, now):
        # rate(t) = rate_now * exp(k t) with k = ln 2 / growth_period. Its
        # integral over a gap g is rate_now * (exp(k g) - 1) / k; setting that
        # equal to a unit-exponential draw and solving for g gives the gap.
        k = math.log(2.0) / pattern.growth_period
        return math.log1p(float(rng.exponential(1.0)) * k / rate) / k
    return float(rng.exponential(1.0 / rate))


def generate_traffic(
    env: simpy.Environment,
    pattern: TrafficPattern,
    new_request: RequestFactory,
    rng: np.random.Generator,
) -> Generator[object, None, None]:
    """Emit one request per Poisson arrival for the configured window.

    ``new_request`` is called with a unique id and must return a process
    generator that is scheduled on ``env``.
    """
    end = pattern.start_time + pattern.duration
    breakpoints = _breakpoints(pattern, end)

    # Wait until the traffic window opens (a no-op when start_time is 0).
    if pattern.start_time > 0:
        yield env.timeout(pattern.start_time)

    counter = 0
    while env.now < end:
        now = env.now
        # The rate law in force at ``now`` holds until here.
        boundary = next((t for t in breakpoints if t > now), end)
        gap = _next_gap(pattern, now, rng)
        if now + gap >= boundary:
            # No arrival before the law changes (or the window closes).
            yield env.timeout(boundary - now)
            continue
        yield env.timeout(gap)
        request_id = f"req-{counter}"
        counter += 1
        env.process(new_request(request_id))
