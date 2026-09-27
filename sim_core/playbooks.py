"""Named incident playbooks: one-click, realistic failure scenarios.

A :class:`Playbook` packages a human-readable description with the chaos
events that make the incident *real* inside the running simulation (live
capacity drops, inflated service times, cache hit-rate loss - see
:mod:`sim_core.chaos`). Playbooks are config, not state: :meth:`Playbook.apply`
returns a new :class:`SimulationConfig` with the incident's chaos events
appended to whatever chaos the config already defines, so a user's own chaos
windows keep working alongside the incident.

Targeting is by *role* (database, cache, external API), not by node name, so
the same playbook works on any topology that has a node with that role; the
concrete node name is written into ``ChaosEvent.target`` for the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from sim_core.config import (
    ChaosEvent,
    ChaosEventType,
    ComponentRole,
    GraphTopologyConfig,
    SimulationConfig,
)

__all__ = ["PLAYBOOKS", "Playbook", "get_playbook", "list_playbooks"]


@dataclass(frozen=True)
class Playbook:
    """A named incident: what it does (in words) and how to inject it."""

    name: str
    description: str
    build_events: Callable[[GraphTopologyConfig, float], List[ChaosEvent]]

    def apply(self, config: SimulationConfig) -> SimulationConfig:
        """Return ``config`` with this incident's chaos events appended.

        The first injection lands ~25% into the run (floored at 5 s) so the
        incident always fires inside the horizon, even for short runs.
        """
        start = max(5.0, round(config.duration * 0.25, 1))
        events = self.build_events(config.topology, start)
        return config.model_copy(update={"chaos": [*config.chaos, *events]})


def _node_with_role(topology: GraphTopologyConfig, role: ComponentRole, playbook: str) -> str:
    """The first node with ``role``; a clear error when the incident has no target."""
    for node in topology.nodes:
        if node.role is role:
            return node.name
    raise ValueError(
        f"playbook {playbook!r} needs a node with role {role.value!r}, "
        f"but the topology has none"
    )


# ---------------------------------------------------------------------------
# The four built-in incidents
# ---------------------------------------------------------------------------

def _db_failover(topology: GraphTopologyConfig, start: float) -> List[ChaosEvent]:
    """Database failover: 5 s full outage, then 30 s of degraded service."""
    db = _node_with_role(topology, ComponentRole.DATABASE, "db_failover")
    return [
        ChaosEvent(
            event_type=ChaosEventType.COMPONENT_FAILURE,
            intensity=1.0,  # full outage: 0 live slots for the failover window
            start_time=start,
            interval=60.0,
            duration=5.0,
            target=db,
        ),
        ChaosEvent(
            event_type=ChaosEventType.COMPONENT_FAILURE,
            intensity=0.5,  # replica serving at half capacity while it catches up
            start_time=start + 5.0,
            interval=60.0,
            duration=30.0,
            target=db,
        ),
    ]


def _cross_region_latency_spike(topology: GraphTopologyConfig, start: float) -> List[ChaosEvent]:
    """Every hop ~80% slower for 15 s, as if traffic crossed regions."""
    del topology  # the incident affects the whole path; no specific node needed
    return [
        ChaosEvent(
            event_type=ChaosEventType.NETWORK_LATENCY,
            intensity=0.8,
            start_time=start,
            interval=60.0,
            duration=15.0,
        ),
    ]


def _cache_eviction_storm(topology: GraphTopologyConfig, start: float) -> List[ChaosEvent]:
    """90% of the cache hit-rate lost for 20 s; lookups fall through to the DB."""
    _node_with_role(topology, ComponentRole.CACHE, "cache_eviction_storm")  # fail loud if absent
    return [
        ChaosEvent(
            event_type=ChaosEventType.CACHE_OUTAGE,
            intensity=0.9,
            start_time=start,
            interval=60.0,
            duration=20.0,
        ),
    ]


def _dependency_timeout_cascade(topology: GraphTopologyConfig, start: float) -> List[ChaosEvent]:
    """A downstream dependency times out, then retry traffic clogs the path."""
    try:
        dependency = _node_with_role(topology, ComponentRole.EXTERNAL_API, "dependency_timeout_cascade")
    except ValueError:
        # No dedicated dependency node: the database is the classic slow
        # downstream service, so aim the cascade at it instead.
        dependency = _node_with_role(topology, ComponentRole.DATABASE, "dependency_timeout_cascade")
    return [
        ChaosEvent(
            event_type=ChaosEventType.COMPONENT_FAILURE,
            intensity=1.0,  # dependency unresponsive for the timeout window
            start_time=start,
            interval=60.0,
            duration=10.0,
            target=dependency,
        ),
        ChaosEvent(
            event_type=ChaosEventType.NETWORK_LATENCY,
            intensity=0.5,  # retry/congestion tail behind the outage
            start_time=start + 10.0,
            interval=60.0,
            duration=20.0,
        ),
    ]


#: Registry of every built-in playbook, keyed by CLI name.
PLAYBOOKS: Dict[str, Playbook] = {
    "db_failover": Playbook(
        name="db_failover",
        description=(
            "Database failover: 5 s full outage, then 30 s of degraded "
            "(half-capacity) service on the database node."
        ),
        build_events=_db_failover,
    ),
    "cross_region_latency_spike": Playbook(
        name="cross_region_latency_spike",
        description=(
            "Cross-region latency spike: every hop in the path ~80% slower "
            "for 15 s."
        ),
        build_events=_cross_region_latency_spike,
    ),
    "cache_eviction_storm": Playbook(
        name="cache_eviction_storm",
        description=(
            "Cache eviction storm: 90% of the hit-rate lost for 20 s, so "
            "lookups fall through to the database."
        ),
        build_events=_cache_eviction_storm,
    ),
    "dependency_timeout_cascade": Playbook(
        name="dependency_timeout_cascade",
        description=(
            "Dependency timeout cascade: the external dependency (or the "
            "database, if there is none) times out for 10 s, then retry "
            "traffic slows the whole path for 20 s."
        ),
        build_events=_dependency_timeout_cascade,
    ),
}


def get_playbook(name: str) -> Playbook:
    """The built-in playbook with this CLI name (clear error when unknown)."""
    try:
        return PLAYBOOKS[name]
    except KeyError:
        raise KeyError(
            f"unknown playbook {name!r} (available: {', '.join(PLAYBOOKS)})"
        ) from None


def list_playbooks() -> List[Playbook]:
    """Every built-in playbook, in registry order."""
    return list(PLAYBOOKS.values())
