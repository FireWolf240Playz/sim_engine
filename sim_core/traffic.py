"""Traffic generation: Poisson arrivals whose rate follows a configured profile.

``generate_traffic`` is a SimPy process: it is started with
``env.process(generate_traffic(...))`` and yields ``env.timeout()`` events to
advance the clock, spawning one request process per arrival. It honours both
``start_time`` and ``duration`` (the original code ignored both), and models
arrivals as a Poisson process (exponential inter-arrival times) instead of a
fixed spacing.
"""

from __future__ import annotations

from typing import Callable, Generator

import numpy as np
import simpy

from sim_core.config import PatternType, TrafficPattern

# A factory that, given a unique id, returns a request-process generator to
# be scheduled on the environment (e.g. ``CloudSimulator.serve_request``).
RequestFactory = Callable[[str], Generator[object, None, None]]


def _target_rps(pattern: TrafficPattern, t: float) -> float:
    """Return the target arrival rate (requests/second) at time ``t``.

    Returns ``0.0`` before the traffic window opens.
    """
    if t < pattern.start_time:
        return 0.0

    if pattern.pattern_type is PatternType.EXPONENTIAL:
        elapsed = t - pattern.start_time
        rps = pattern.base_rps * (2.0 ** (elapsed / pattern.growth_period))
    else:
        rps = pattern.base_rps

    # A spike window (when configured) overrides the base rate while active.
    if (
        pattern.spike_rps is not None
        and pattern.spike_start is not None
        and pattern.spike_duration is not None
        and pattern.spike_start <= t < pattern.spike_start + pattern.spike_duration
    ):
        rps = pattern.spike_rps

    return max(rps, 1e-9)


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

    # Wait until the traffic window opens (a no-op when start_time is 0).
    if pattern.start_time > 0:
        yield env.timeout(pattern.start_time)

    counter = 0
    while env.now < end:
        rps = _target_rps(pattern, env.now)
        if rps <= 1e-9:
            # No traffic yet (clock before start) - nudge forward to avoid a spin.
            yield env.timeout(0.01)
            continue

        # Exponential inter-arrival time for the current target rate.
        inter_arrival = float(rng.exponential(1.0 / rps))
        yield env.timeout(inter_arrival)

        # Only spawn if we are still inside the window.
        if env.now < end:
            request_id = f"req-{counter}"
            counter += 1
            env.process(new_request(request_id))
