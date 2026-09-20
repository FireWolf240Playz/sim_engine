"""Chaos / failure injection.

Every event here mutates *live* simulation state that the running request
processes actually read (resource capacity, service time, or cache hit-rate),
holds the disruption for a bounded window, and then restores the original
value. This is what makes the injection "real": it changes the behaviour of
the in-flight simulation instead of merely annotating a metrics log afterwards.
"""

from __future__ import annotations

from typing import Generator

import numpy as np
import simpy

from sim_core.config import ChaosEvent, ChaosEventType
from sim_core.topology import Topology


def _window(event: ChaosEvent) -> float:
    """How long a single disruption stays active before it is restored."""
    return event.duration if event.duration is not None else event.interval


def _component_failure(
    env: simpy.Environment, topology: Topology, event: ChaosEvent, rng: np.random.Generator
) -> Generator[object, None, None]:
    """Temporarily remove a fraction of a random component's capacity.

    Fewer live slots means new requests genuinely queue longer; in-flight ones
    keep their slots until they finish, which is the realistic degradation.
    """
    if event.start_time > 0:
        yield env.timeout(event.start_time)

    while True:
        victim = topology.pick_random(rng)
        original_capacity = victim.capacity
        new_capacity = max(0, round(original_capacity * (1.0 - event.intensity)))
        victim.set_capacity(new_capacity)  # real: fewer slots -> real queuing

        yield env.timeout(_window(event))

        victim.set_capacity(original_capacity)  # restore
        yield env.timeout(event.interval)


def _network_latency(
    env: simpy.Environment, topology: Topology, event: ChaosEvent, rng: np.random.Generator
) -> Generator[object, None, None]:
    """Inflate the live service time of every component for a window.

    New requests draw a longer service duration from the inflated mean, so the
    slowdown is felt by the simulation itself (real ``env.timeout`` delays).
    """
    if event.start_time > 0:
        yield env.timeout(event.start_time)

    while True:
        multiplier = 1.0 + event.intensity
        victims = topology.components
        for comp in victims:
            comp.inflate_service_time(multiplier)  # real: longer service delays

        yield env.timeout(_window(event))

        for comp in victims:
            comp.restore_service_time()
        yield env.timeout(event.interval)


def _cache_outage(
    env: simpy.Environment, topology: Topology, event: ChaosEvent, rng: np.random.Generator
) -> Generator[object, None, None]:
    """Drive the cache hit-rate down (0.0 == fully disabled) for a window.

    More misses fall through to the database, so the DB genuinely gets more
    load during the outage.
    """
    if topology.cache is None:
        return  # nothing to fail - the process ends immediately

    if event.start_time > 0:
        yield env.timeout(event.start_time)

    original_hit_rate = topology.cache.hit_rate
    degraded = max(0.0, original_hit_rate * (1.0 - event.intensity))

    while True:
        topology.cache.hit_rate = degraded  # real: more lookups hit the DB

        yield env.timeout(_window(event))

        topology.cache.hit_rate = original_hit_rate  # restore
        yield env.timeout(event.interval)


_HANDLERS = {
    ChaosEventType.COMPONENT_FAILURE: _component_failure,
    ChaosEventType.NETWORK_LATENCY: _network_latency,
    ChaosEventType.CACHE_OUTAGE: _cache_outage,
}


def inject(
    env: simpy.Environment, topology: Topology, event: ChaosEvent, rng: np.random.Generator
) -> Generator[object, None, None]:
    """Dispatch a chaos event to its handler and return a SimPy process."""
    handler = _HANDLERS.get(event.event_type)
    if handler is None:  # pragma: no cover - guarded by the ChaosEventType enum
        raise ValueError(f"Unsupported chaos event type: {event.event_type!r}")
    return handler(env, topology, event, rng)
