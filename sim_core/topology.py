"""Build and wrap the *live* SimPy resources from validated configuration.

A :class:`Component` owns a real :class:`simpy.Resource` plus the *live,
mutable* knobs that chaos injection may change at runtime (``service_time``
and ``hit_rate``). The Pydantic config is read-only and only used to seed
those live values. This is the strict separation of config (data) from live
simulation state (SimPy objects).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import simpy

from sim_core.config import (
    CacheConfig,
    ComponentConfig,
    ComponentRole,
    Edge,
    GraphTopologyConfig,
)


class Component:
    """A single live infrastructure component backed by a SimPy resource."""

    __slots__ = (
        "config",
        "resource",
        "service_time",
        "hit_rate",
        "_base_service_time",
        "_base_hit_rate",
        "_active_capacity_drops",
        "_active_latency_factors",
        "_active_hit_rate_drops",
    )

    def __init__(self, env: simpy.Environment, config: ComponentConfig) -> None:
        self.config = config
        # The live SimPy resource, created *from* the (frozen) config values.
        self.resource = simpy.Resource(env=env, capacity=config.max_capacity)
        # Live, mutable knobs that chaos injection can change at runtime.
        self._base_service_time = config.service_time
        self.service_time = config.service_time
        self._base_hit_rate = config.hit_rate if isinstance(config, CacheConfig) else 1.0
        self.hit_rate = self._base_hit_rate
        # Active chaos degradations per knob (see apply_*/release_* below).
        self._active_capacity_drops: List[float] = []
        self._active_latency_factors: List[float] = []
        self._active_hit_rate_drops: List[float] = []

    # -- read-only views of the config -------------------------------------
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_cache(self) -> bool:
        """Whether this node has read-through cache semantics (a hit ends the
        request's branch; a miss falls through downstream)."""
        return self.config.role is ComponentRole.CACHE or isinstance(self.config, CacheConfig)

    @property
    def base_service_time(self) -> float:
        return self._base_service_time

    # -- live metrics ------------------------------------------------------
    @property
    def capacity(self) -> int:
        """Current (possibly chaos-reduced) number of slots."""
        return int(self.resource.capacity)

    @property
    def queue_length(self) -> int:
        """Number of requests currently waiting for a slot."""
        return len(self.resource.queue)

    @property
    def in_use(self) -> int:
        """Number of requests currently holding a slot."""
        return self.resource.count

    @property
    def utilization(self) -> float:
        """Fraction of capacity currently in use (clamped to 0.0-1.0)."""
        cap = self.capacity
        return 0.0 if cap <= 0 else min(1.0, self.in_use / cap)

    # -- live-state mutation (used by chaos) -------------------------------
    def set_capacity(self, new_capacity: int) -> int:
        """Set the live capacity of the underlying resource.

        ``Resource.capacity`` is a read-only property in SimPy, so we update
        its backing field directly. A value of ``0`` models a full outage:
        ``_do_put`` then refuses to admit any new request until it is restored.
        Returns the capacity actually applied (>= 0).
        """
        applied = max(0, int(new_capacity))
        self.resource._capacity = applied  # noqa: SLF001 - the public property is read-only
        return applied

    # -- reference-counted chaos knobs (used by chaos) ----------------------
    #
    # Every chaos knob is reference-counted: each active window registers its
    # intensity and the live value is always recomputed from the *configured*
    # base plus the worst active degradation. This makes overlapping chaos
    # windows on the same node (e.g. a 5 s full outage followed by a 30 s
    # half-capacity window, as in the ``db_failover`` playbook) compose
    # deterministically - whichever process resumes first, the state is the
    # same - and a restore can never cancel another window that is still
    # active.

    def apply_capacity_drop(self, intensity: float) -> None:
        """Register an active capacity removal and recompute the live slots.

        Fewer live slots means new requests genuinely queue longer; in-flight
        ones keep their slots until they finish, which is the realistic
        degradation.
        """
        self._active_capacity_drops.append(intensity)
        self._recompute_capacity()

    def release_capacity_drop(self, intensity: float) -> None:
        """End one capacity removal; restore base capacity when none remain."""
        try:
            self._active_capacity_drops.remove(intensity)
        except ValueError:  # pragma: no cover - defensive: unbalanced release
            return
        self._recompute_capacity()

    def _recompute_capacity(self) -> None:
        base = self.config.max_capacity
        worst = max(self._active_capacity_drops) if self._active_capacity_drops else 0.0
        self.set_capacity(max(0, round(base * (1.0 - worst))))

    def apply_latency_spike(self, factor: float) -> None:
        """Register an active service-time inflation (factor >= 1) and recompute.

        New requests draw a longer service duration from the inflated mean, so
        the slowdown is felt by the simulation itself (real ``env.timeout``
        delays).
        """
        self._active_latency_factors.append(factor)
        self._recompute_service_time()

    def release_latency_spike(self, factor: float) -> None:
        """End one latency spike; restore base service time when none remain."""
        try:
            self._active_latency_factors.remove(factor)
        except ValueError:  # pragma: no cover - defensive: unbalanced release
            return
        self._recompute_service_time()

    def _recompute_service_time(self) -> None:
        worst = max(self._active_latency_factors) if self._active_latency_factors else 1.0
        self.service_time = max(0.0, self._base_service_time * worst)

    def apply_hit_rate_drop(self, intensity: float) -> None:
        """Register an active hit-rate loss and recompute the live hit-rate.

        More misses fall through to the database, so the DB genuinely gets
        more load during the outage.
        """
        self._active_hit_rate_drops.append(intensity)
        self._recompute_hit_rate()

    def release_hit_rate_drop(self, intensity: float) -> None:
        """End one hit-rate drop; restore base hit-rate when none remain."""
        try:
            self._active_hit_rate_drops.remove(intensity)
        except ValueError:  # pragma: no cover - defensive: unbalanced release
            return
        self._recompute_hit_rate()

    def _recompute_hit_rate(self) -> None:
        worst = max(self._active_hit_rate_drops) if self._active_hit_rate_drops else 0.0
        self.hit_rate = max(0.0, self._base_hit_rate * (1.0 - worst))

    def draw_service_time(self, rng: np.random.Generator) -> float:
        """Sample a concrete service duration (seconds) from the live mean."""
        return float(rng.exponential(self.service_time))


class Topology:
    """The live request-path graph: named components plus directed edges.

    Owns one live :class:`Component` per configured node (node order is
    preserved), the directed edge list for traversal, and the resolved entry
    node. The graph must be a DAG: cycles are rejected at construction time
    with a clear error, so a request walk can never loop forever.
    """

    __slots__ = ("env", "config", "entry_node", "_components", "_outgoing")

    def __init__(self, env: simpy.Environment, config: GraphTopologyConfig) -> None:
        self.env = env
        self.config = config
        self._components: Dict[str, Component] = {
            node.name: Component(env, node) for node in config.nodes
        }
        self._outgoing: Dict[str, List[Edge]] = {node.name: [] for node in config.nodes}
        for edge in config.edges:
            self._outgoing[edge.source].append(edge)
        self.entry_node = config.resolve_entry_node()
        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        """Reject cyclic request paths (iterative DFS with a gray/black marker).

        A cycle would make the request walk recurse forever, so it is a config
        error that should fail loudly and early, at topology construction.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {name: WHITE for name in self._components}
        for start in self._components:
            if color[start] != WHITE:
                continue
            color[start] = GRAY
            stack = [(start, iter(self._outgoing[start]))]
            while stack:
                node, edges_iter = stack[-1]
                pushed = False
                for edge in edges_iter:
                    target = edge.target
                    if color[target] == GRAY:
                        raise ValueError(
                            f"cycle detected in the request path (re-entering node "
                            f"{target!r}) - the graph must be a DAG"
                        )
                    if color[target] == WHITE:
                        color[target] = GRAY
                        stack.append((target, iter(self._outgoing[target])))
                        pushed = True
                        break
                if not pushed:
                    color[node] = BLACK
                    stack.pop()

    # -- traversal API -----------------------------------------------------
    def component(self, name: str) -> Component:
        """The live component with this name (KeyError when unknown)."""
        try:
            return self._components[name]
        except KeyError:
            raise KeyError(
                f"unknown component {name!r} (known: {sorted(self._components)})"
            ) from None

    def outgoing_edges(self, name: str) -> List[Edge]:
        """All outgoing edges from ``name``, in configured order (empty if terminal)."""
        return list(self._outgoing.get(name, []))

    # -- compatibility views -----------------------------------------------
    @property
    def components(self) -> List[Component]:
        """Every live component, in node order."""
        return list(self._components.values())

    @property
    def cache(self) -> Optional[Component]:
        """The first cache-role component (targeted by CACHE_OUTAGE chaos), if any."""
        for component in self._components.values():
            if component.is_cache:
                return component
        return None

    def pick_random(self, rng: np.random.Generator) -> Component:
        """Return a random component (used to select a chaos victim)."""
        components = self.components
        index = int(rng.integers(0, len(components)))
        return components[index]
