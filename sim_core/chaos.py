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
    """Temporarily remove a fraction of a component's live capacity.

    Fewer live slots means new requests genuinely queue longer; in-flight ones
    keep their slots until they finish, which is the realistic degradation.
    When ``event.target`` names a node, the incident is aimed at exactly that
    component (incident playbooks rely on this); otherwise a random victim is
    drawn, which is the original behaviour.

    The drop is reference-counted on the component, so overlapping windows on
    the same node compose deterministically and a restore never cancels a
    window that is still active.
    """
    if event.start_time > 0:
        yield env.timeout(event.start_time)

    while True:
        if event.target is not None:
            victim = topology.component(event.target)
        else:
            victim = topology.pick_random(rng)
        victim.apply_capacity_drop(event.intensity)  # real: fewer slots -> real queuing

        yield env.timeout(_window(event))

        victim.release_capacity_drop(event.intensity)  # restore (or recompute)
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

    multiplier = 1.0 + event.intensity
    victims = topology.components
    while True:
        for comp in victims:
            comp.apply_latency_spike(multiplier)  # real: longer service delays

        yield env.timeout(_window(event))

        for comp in victims:
            comp.release_latency_spike(multiplier)
        yield env.timeout(event.interval)


def _cache_outage(
    env: simpy.Environment, topology: Topology, event: ChaosEvent, rng: np.random.Generator
) -> Generator[object, None, None]:
    """Drive the cache hit-rate down (0.0 == fully disabled) for a window.

    More misses fall through to the database, so the DB genuinely gets more
    load during the outage.
    """
    cache = topology.cache
    if cache is None:
        return  # nothing to fail - the process ends immediately

    if event.start_time > 0:
        yield env.timeout(event.start_time)

    while True:
        cache.apply_hit_rate_drop(event.intensity)  # real: more lookups hit the DB

        yield env.timeout(_window(event))

        cache.release_hit_rate_drop(event.intensity)  # restore (or recompute)
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
