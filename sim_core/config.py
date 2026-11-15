"""Pydantic v2 configuration models for the Eleven cloud-resilience simulator.

These models hold *validated configuration data only* (numbers, strings,
enums). They are deliberately **not** used as live simulation state: the live
SimPy resources are created separately from these values inside
:mod:`sim_core.topology`. Keeping config (immutable data) and live state
(mutable SimPy objects) separate is a hard architectural rule of the project.
"""

from __future__ import annotations

import json
import math
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Every config model is immutable and rejects fields it does not know. A
#: misspelt key (``hit_rte``) would otherwise be dropped in silence and the
#: default used in its place, which reads as a working config.
_STRICT = ConfigDict(frozen=True, extra="forbid")

#: Most requests one run may be expected to generate. A run records every
#: request, so this bounds its memory and time; it is far above any run the
#: tool is meant for and exists to stop a profile that explodes (exponential
#: growth left at its default doubling period over a long run).
MAX_EXPECTED_REQUESTS: int = 1_000_000

#: Slack when the probabilities of a group of alternative edges are summed:
#: an even split written to four decimals (three edges at 0.3333) still
#: counts as covering every request.
GROUP_SUM_TOLERANCE: float = 1e-3


class PatternType(str, Enum):
    """Base traffic curve.

    A *spike* window is modelled separately (see
    :class:`TrafficPattern.spike_*`) so it can be layered on top of either
    base curve.
    """

    CONSTANT = "constant"
    EXPONENTIAL = "exponential"


class ChaosEventType(str, Enum):
    """Supported chaos / failure-injection events.

    Every event mutates *live* simulation state (resource capacity, service
    time, or cache hit-rate) for a bounded window and then restores it. This
    is what makes the injection "real": it changes what the running request
    processes actually experience, rather than merely annotating a metrics log.
    """

    COMPONENT_FAILURE = "component_failure"  # drop a component's live capacity
    NETWORK_LATENCY = "network_latency"      # inflate service times
    CACHE_OUTAGE = "cache_outage"            # force cache misses onto the DB


class ComponentRole(str, Enum):
    """Semantic role of a node in the request-path graph.

    Roles drive two kinds of special behaviour: a ``cache`` node decides
    hit/miss (a hit ends the branch, a miss falls through downstream), and
    chaos events target components by component object. Everything else is a
    plain capacity-constrained service hop.
    """

    LOAD_BALANCER = "load_balancer"
    WORKER = "worker"
    CACHE = "cache"
    DATABASE = "database"
    EXTERNAL_API = "external_api"
    GENERIC = "generic"


class ComponentConfig(BaseModel):
    """Configuration shared by every infrastructure component."""

    model_config = _STRICT

    name: str = Field(..., min_length=1, description="Unique name within the topology.")
    role: ComponentRole = Field(
        ComponentRole.GENERIC,
        description=(
            "Semantic role of the node (drives cache hit/miss semantics). "
            "Defaults to a plain service hop."
        ),
    )
    max_capacity: int = Field(
        ...,
        ge=1,
        description="Maximum number of concurrent users the component can serve.",
    )
    service_time: float = Field(
        ...,
        gt=0.0,
        description="Mean service time in seconds; samples are drawn from an exponential distribution.",
    )
    timeout: Optional[float] = Field(
        None,
        ge=0.0,
        description=(
            "Max seconds to wait for a slot before giving up on one attempt. "
            "None (default) means wait forever."
        ),
    )
    retry_limit: int = Field(
        0,
        ge=0,
        description=(
            "How many times a timed-out slot acquisition is retried before the "
            "request is failed (total attempts = retry_limit + 1)."
        ),
    )
    retry_backoff: float = Field(
        0.1,
        ge=0.0,
        description="Seconds to wait between retry attempts after a timeout.",
    )
    cost_per_hour: float = Field(
        0.0,
        ge=0.0,
        description=(
            "Simulated cost rate in USD per active slot-hour (the provider's "
            "hourly rate for this service tier). 0.0 (default) disables cost "
            "accounting for this component."
        ),
    )


class LoadBalancerConfig(ComponentConfig):
    """Layer-7 load balancer / ingress in front of the worker pool."""

    role: ComponentRole = ComponentRole.LOAD_BALANCER


class AppWorkerConfig(ComponentConfig):
    """Application worker pool that handles the business logic."""

    role: ComponentRole = ComponentRole.WORKER


class CacheConfig(ComponentConfig):
    """Read-through cache (e.g. Redis) sitting between workers and the database.

    A *hit* is served straight from cache and skips the database; a *miss*
    falls through to the database.
    """

    hit_rate: float = Field(
        0.9,
        ge=0.0,
        le=1.0,
        description="Probability that a lookup is served from cache.",
    )

    role: ComponentRole = ComponentRole.CACHE

    @field_validator("role")
    @classmethod
    def _role_is_cache(cls, role: ComponentRole) -> ComponentRole:
        if role is not ComponentRole.CACHE:
            raise ValueError(
                f"a CacheConfig always has role 'cache', got {role.value!r}; use the "
                "config class of that role, which has no hit_rate"
            )
        return role


class DatabaseConfig(ComponentConfig):
    """Primary data store (e.g. Postgres)."""

    role: ComponentRole = ComponentRole.DATABASE


class TopologyConfig(BaseModel):
    """The ordered request path: load balancer -> worker -> (cache) -> database.

    Exactly one of each required role; the cache is optional.
    """

    model_config = _STRICT

    load_balancer: LoadBalancerConfig
    app_worker: AppWorkerConfig
    database: DatabaseConfig
    cache: Optional[CacheConfig] = Field(
        None,
        description="Optional cache between the worker pool and the database.",
    )

    def component_names(self) -> List[str]:
        """Names of every configured component (cache included when present)."""
        names = [self.load_balancer.name, self.app_worker.name, self.database.name]
        if self.cache is not None:
            names.append(self.cache.name)
        return names

    def to_graph(self) -> GraphTopologyConfig:
        """Convert this fixed pipeline into the equivalent request-path graph.

        ``lb -> worker -> (cache) -> database`` becomes a chain of edges with
        ``probability=1.0`` (the cache's downstream edge is only followed on a
        miss, exactly as before), so legacy and graph configs are
        interchangeable.
        """
        nodes: List[ComponentConfig] = [self.load_balancer, self.app_worker]
        edges = [
            Edge(source=self.load_balancer.name, target=self.app_worker.name, probability=1.0)
        ]
        if self.cache is not None:
            nodes.append(self.cache)
            edges.append(Edge(source=self.app_worker.name, target=self.cache.name, probability=1.0))
            edges.append(Edge(source=self.cache.name, target=self.database.name, probability=1.0))
        else:
            edges.append(Edge(source=self.app_worker.name, target=self.database.name, probability=1.0))
        nodes.append(self.database)
        return GraphTopologyConfig(nodes=nodes, edges=edges, entry_node=self.load_balancer.name)


class Edge(BaseModel):
    """A directed edge in the request-path graph.

    ``probability`` is the probability that a request currently at ``source``
    follows this edge to ``target``. Edges out of a node are evaluated
    *independently*, so a node with several outgoing edges fans out to several
    downstream components (e.g. a worker calling two services), each visited
    with its own probability.

    Edges out of one node that share a ``group`` are *alternatives* instead:
    each request takes at most one of them, chosen by their probabilities.
    That is how a load balancer picks one backend. The probabilities of a
    group may sum to less than 1, in which case the remainder of the requests
    takes none of its edges.
    """

    model_config = _STRICT

    source: str = Field(..., min_length=1, description="Name of the source node.")
    target: str = Field(..., min_length=1, description="Name of the target node.")
    probability: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="Probability (0-1) that a request at the source follows this edge.",
    )
    group: str | None = Field(
        None,
        min_length=1,
        description=(
            "Edges out of the same node with the same group are alternatives: "
            "a request takes at most one, picked by probability. None "
            "(default) means the edge is followed independently."
        ),
    )


# Role-discriminated union of the component config types.
#
# Each concrete subclass pins a fixed ``role`` default, so the ``role`` field
# acts as a natural discriminator: a node payload is validated as the matching
# subclass (preserving subclass-only fields like ``CacheConfig.hit_rate`` on
# serialization), with ``generic``/``external_api`` roles falling back to the
# plain ComponentConfig. This also fixes a serialization bug: with a plain
# ``List[ComponentConfig]`` field, the parent model's ``model_dump()`` would
# drop subclass-only fields such as ``hit_rate`` when writing YAML/JSON.


# Union of the concrete component config types, most-specific first. The order
# matters for the *serializer*: Pydantic picks the first union branch whose
# class is an ``isinstance`` match for the runtime value, so a CacheConfig
# node is serialized as a CacheConfig (keeping ``hit_rate`` in dumps) rather
# than collapsing to the plain ComponentConfig branch.
TopologyNode = Union[
    LoadBalancerConfig,
    AppWorkerConfig,
    CacheConfig,
    DatabaseConfig,
    ComponentConfig,
]


class GraphTopologyConfig(BaseModel):
    """A general request-path graph: named nodes plus directed probabilistic edges.

    This is the canonical topology format. The legacy fixed pipeline
    (:class:`TopologyConfig`: load balancer -> worker -> cache -> database)
    is automatically converted to this format by
    :class:`SimulationConfig`, so old configs keep working unchanged.
    """

    model_config = _STRICT

    nodes: List[TopologyNode] = Field(
        ...,
        min_length=1,
        description=(
            "Every component in the topology (each with a unique name and a "
            "role). Node payloads are validated as the specific config "
            "subclass matching their ``role`` (load_balancer/worker/cache/"
            "database), otherwise as a plain ComponentConfig."
        ),
    )

    @field_validator("nodes", mode="before")
    @classmethod
    def _reconstitute_node_subclasses(
        cls, nodes: List[object]
    ) -> List[object]:
        """Map plain-dict node payloads to their specific config subclass.

        A dict with ``role: worker`` validates identically against
        ``LoadBalancerConfig``, ``AppWorkerConfig`` and ``ComponentConfig``, so
        the concrete type is resolved explicitly, by ``role``. A ``hit_rate``
        belongs to a cache: with no role it means one (older configs wrote
        caches that way), with any other role it is an error, because a
        worker that silently became a cache would stop most of its traffic.
        A null ``hit_rate`` is treated as absent.

        Model instances pass through, except a plain ``ComponentConfig``
        carrying the cache role: it becomes a :class:`CacheConfig`, so it has
        a real hit rate instead of behaving as a cache that always hits.
        """
        role_map: Dict[str, type] = {
            ComponentRole.LOAD_BALANCER: LoadBalancerConfig,
            ComponentRole.WORKER: AppWorkerConfig,
            ComponentRole.CACHE: CacheConfig,
            ComponentRole.DATABASE: DatabaseConfig,
        }
        resolved: List[object] = []
        for node in nodes:
            if isinstance(node, dict):
                data = dict(node)
                if data.get("hit_rate", 0.0) is None:
                    del data["hit_rate"]
                role = data.get("role")
                if "hit_rate" in data and role is not None and role != ComponentRole.CACHE:
                    raise ValueError(
                        f"node {data.get('name')!r}: hit_rate is only valid on a cache "
                        f"node, but its role is {getattr(role, 'value', role)!r}. Remove "
                        "hit_rate, or set role: cache"
                    )
                if "hit_rate" in data:
                    resolved.append(CacheConfig.model_validate(data))
                elif role in role_map:
                    resolved.append(role_map[role].model_validate(data))
                else:
                    # generic / external_api / unknown roles: plain component
                    resolved.append(ComponentConfig.model_validate(data))
            elif (
                isinstance(node, ComponentConfig)
                and node.role is ComponentRole.CACHE
                and not isinstance(node, CacheConfig)
            ):
                resolved.append(CacheConfig.model_validate(node.model_dump()))
            else:
                resolved.append(node)
        return resolved
    edges: List[Edge] = Field(
        default_factory=list,
        description=(
            "Directed, probabilistic edges between nodes. Empty means a single "
            "isolated node: requests just visit it."
        ),
    )
    entry_node: Optional[str] = Field(
        None,
        description=(
            "Where requests enter the graph. Defaults to the unique node with "
            "no incoming edges (a load-balancer role wins ties)."
        ),
    )

    @field_validator("nodes")
    @classmethod
    def _node_names_are_unique(cls, nodes: List[ComponentConfig]) -> List[ComponentConfig]:
        names = [node.name for node in nodes]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate node name(s) in topology: {duplicates}")
        return nodes

    @model_validator(mode="after")
    def _edges_reference_known_nodes(self) -> GraphTopologyConfig:
        names = {node.name for node in self.nodes}
        for edge in self.edges:
            if edge.source not in names:
                raise ValueError(f"edge {edge.source!r} -> {edge.target!r}: unknown source node")
            if edge.target not in names:
                raise ValueError(f"edge {edge.source!r} -> {edge.target!r}: unknown target node")
            if edge.source == edge.target:
                raise ValueError(f"self-loop edge on node {edge.source!r} is not allowed")
        if self.entry_node is not None and self.entry_node not in names:
            raise ValueError(f"entry_node {self.entry_node!r} is not one of the configured nodes")
        return self

    @model_validator(mode="after")
    def _grouped_edges_are_alternatives(self) -> GraphTopologyConfig:
        """A group picks at most one edge, so its probabilities cannot pass 1."""
        totals: dict[tuple[str, str], float] = {}
        for edge in self.edges:
            if edge.group is not None:
                key = (edge.source, edge.group)
                totals[key] = totals.get(key, 0.0) + edge.probability
        for (source, group), total in totals.items():
            if total > 1.0 + GROUP_SUM_TOLERANCE:
                raise ValueError(
                    f"edges out of {source!r} in group {group!r} have probabilities "
                    f"that sum to {total:g}: alternatives can sum to at most 1. Lower "
                    "them, or drop the group if the node calls every target"
                )
        return self

    @model_validator(mode="after")
    def _all_nodes_reachable_from_entry(self) -> GraphTopologyConfig:
        """Fail fast when a node no request can ever reach.

        A node that is listed but not linked into the path (a missing edge)
        is served zero traffic: its utilisation stays 0, its sizing verdict
        reads "oversized", and chaos aimed at it changes nothing observable.
        That is a wiring mistake in practice - the same class of config
        error as an edge referencing an unknown node - so it is rejected at
        validation time with a message that says exactly what to fix.
        """
        if len(self.nodes) <= 1:
            return self  # a single node is its own entry; nothing to reach
        entry = self.resolve_entry_node()
        adjacency: Dict[str, List[str]] = {node.name: [] for node in self.nodes}
        for edge in self.edges:
            adjacency[edge.source].append(edge.target)
        seen: set[str] = {entry}
        frontier = [entry]
        while frontier:
            for target in adjacency[frontier.pop()]:
                if target not in seen:
                    seen.add(target)
                    frontier.append(target)
        orphans = sorted({node.name for node in self.nodes} - seen)
        if orphans:
            raise ValueError(
                "topology node(s) unreachable from the entry node: "
                f"{', '.join(orphans)} - no request can ever reach them, so their "
                "metrics, sizing, and chaos are all meaningless. Add an edge "
                f"linking each into the request path (entry is {entry!r}) "
                "or remove them."
            )
        return self

    def node_role(self, name: str) -> ComponentRole:
        """Role of the node called ``name`` (KeyError when unknown)."""
        for node in self.nodes:
            if node.name == name:
                return node.role
        raise KeyError(f"unknown node {name!r}")

    def node(self, name: str) -> ComponentConfig:
        """The component config called ``name`` (KeyError when unknown)."""
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(f"unknown node {name!r}")

    def resolve_entry_node(self) -> str:
        """Determine the node where requests enter the graph.

        Uses ``entry_node`` when set; otherwise the unique node with no
        incoming edges. If several nodes have no incoming edges, a node with a
        load-balancer role wins, then the first one in node order. Raises when
        every node has an incoming edge (a pure cycle) and no entry is set.
        """
        if self.entry_node is not None:
            return self.entry_node
        targets = {edge.target for edge in self.edges}
        candidates = [node.name for node in self.nodes if node.name not in targets]
        for name in candidates:
            if self.node_role(name) is ComponentRole.LOAD_BALANCER:
                return name
        if candidates:
            return candidates[0]
        raise ValueError(
            "cannot determine the entry node: every node has incoming edges; "
            "set entry_node explicitly"
        )


#: Roadmap 3.5 — how the base rate bends over the traffic window.
TrafficShape = Literal["flat", "ramp", "diurnal", "black_friday"]

#: How many times busier than ``base_rps`` each shape's peak is by default.
DEFAULT_SHAPE_PEAK: dict[str, float] = {"ramp": 3.0, "diurnal": 3.0, "black_friday": 5.0}


def shape_multiplier(shape: str, u: float, peak: float) -> float:
    """The base rate's multiplier at fraction ``u`` (0-1) of the traffic window.

    ``ramp`` climbs in a straight line to ``peak``; ``diurnal`` is one day,
    quiet at both ends and ``peak`` in the middle (``sin^2``);
    ``black_friday`` is a normal day for 40% of the window, a climb over the
    next 10%, ``peak`` held for 30%, and a decline over the last 20%.
    ``web/src/core/lib/trafficShape.ts`` mirrors these numbers.
    """
    if shape == "ramp":
        return 1.0 + (peak - 1.0) * u
    if shape == "diurnal":
        return 1.0 + (peak - 1.0) * math.sin(math.pi * u) ** 2
    if shape == "black_friday":
        if u < 0.4:
            return 1.0
        if u < 0.5:
            return 1.0 + (peak - 1.0) * (u - 0.4) / 0.1
        if u < 0.8:
            return peak
        return peak - (peak - 1.0) * (u - 0.8) / 0.2
    return 1.0


def _shape_area(shape: str, u: float, peak: float) -> float:
    """The integral of :func:`shape_multiplier` from 0 to ``u``, in closed form."""
    rise = peak - 1.0
    if shape == "ramp":
        return u + rise * u * u / 2.0
    if shape == "diurnal":
        return u + rise * (u / 2.0 - math.sin(2.0 * math.pi * u) / (4.0 * math.pi))
    if shape == "black_friday":
        if u <= 0.4:
            return u
        if u <= 0.5:
            return u + rise * (u - 0.4) ** 2 / 0.2
        climbed = 0.5 + 0.05 * rise  # the area at u = 0.5
        if u <= 0.8:
            return climbed + peak * (u - 0.5)
        held = climbed + 0.3 * peak  # the area at u = 0.8
        return held + peak * (u - 0.8) - rise * (u - 0.8) ** 2 / 0.4
    return u


class TrafficPattern(BaseModel):
    """Traffic generation profile.

    ``base_rps`` is the sustained arrival rate. ``pattern_type`` shapes how
    that rate evolves over ``duration`` seconds. The optional ``spike_*``
    fields define a single high-rate window layered on top of the base curve.
    Arrivals between emissions are modelled as a Poisson process (exponential
    inter-arrival times) rather than fixed spacing.

    ``shape`` (roadmap 3.5) bends the base rate over the traffic window:
    ``flat`` (the default, the rate as configured), ``ramp``, ``diurnal`` or
    ``black_friday`` (see :func:`shape_multiplier`). ``base_rps`` is always
    the quiet rate; ``shape_peak`` is how many times busier the peak is
    (:data:`DEFAULT_SHAPE_PEAK` when unset). A spike still overrides the
    curve while it lasts. A shape and exponential growth are two rate laws
    and cannot be combined.
    """

    model_config = _STRICT

    base_rps: float = Field(..., gt=0.0, description="Sustained arrival rate in requests/second.")
    duration: float = Field(..., gt=0.0, description="How long traffic is generated, in seconds.")
    start_time: float = Field(0.0, ge=0.0, description="When traffic begins, in seconds.")
    pattern_type: PatternType = PatternType.CONSTANT
    growth_period: float = Field(
        30.0,
        gt=0.0,
        description="Seconds for the base rate to double (used when pattern_type is EXPONENTIAL).",
    )

    # Optional spike window, layered on top of the base curve when set.
    spike_rps: Optional[float] = Field(None, gt=0.0, description="Arrival rate during the spike window.")
    spike_start: Optional[float] = Field(None, ge=0.0, description="When the spike window begins.")
    spike_duration: Optional[float] = Field(None, gt=0.0, description="Length of the spike window.")

    shape: TrafficShape = Field(
        "flat",
        description=(
            "How the base rate bends over the traffic window "
            "(flat, ramp, diurnal, black_friday)."
        ),
    )
    shape_peak: float | None = Field(
        None,
        gt=1.0,
        description="How many times busier than base_rps the shape's peak is (default per shape).",
    )

    @model_validator(mode="after")
    def _shape_is_one_rate_law(self) -> TrafficPattern:
        """A peak needs a shape to apply to, and a shape replaces growth."""
        if self.shape == "flat":
            if self.shape_peak is not None:
                raise ValueError(
                    "shape_peak needs a shape other than flat; "
                    "on a flat pattern it would be ignored"
                )
        elif self.pattern_type is PatternType.EXPONENTIAL:
            raise ValueError(
                f"shape {self.shape!r} and exponential growth are two rate laws; "
                "pick one (set pattern_type to constant, or shape to flat)"
            )
        return self

    @property
    def peak_multiplier(self) -> float:
        """How many times ``base_rps`` the shape peaks at (1 for flat)."""
        if self.shape == "flat":
            return 1.0
        return self.shape_peak if self.shape_peak is not None else DEFAULT_SHAPE_PEAK[self.shape]

    def _window_fraction(self, t: float) -> float:
        """How far through the traffic window ``t`` is, clamped to 0-1."""
        return min(1.0, max(0.0, (t - self.start_time) / self.duration))

    def _in_spike(self, t: float) -> bool:
        return (
            self.spike_rps is not None
            and self.spike_start is not None
            and self.spike_duration is not None
            and self.spike_start <= t < self.spike_start + self.spike_duration
        )

    def rate_at(self, t: float) -> float:
        """The arrival rate (requests/second) at time ``t``.

        Zero outside the traffic window; the spike rate while a spike is
        active; else the exponential law, or ``base_rps`` times the shape.
        Pure: :mod:`sim_core.traffic` samples arrivals from it.
        """
        if t < self.start_time or t >= self.start_time + self.duration:
            return 0.0
        if self._in_spike(t):
            assert self.spike_rps is not None
            return self.spike_rps
        if self.pattern_type is PatternType.EXPONENTIAL:
            return self.base_rps * float(2.0 ** ((t - self.start_time) / self.growth_period))
        if self.shape == "flat":
            return self.base_rps
        return self.base_rps * shape_multiplier(
            self.shape, self._window_fraction(t), self.peak_multiplier
        )

    @model_validator(mode="after")
    def _spike_is_whole_and_inside_the_window(self) -> TrafficPattern:
        """A spike needs all three fields, and must overlap the traffic window."""
        given = [
            name
            for name in ("spike_rps", "spike_start", "spike_duration")
            if getattr(self, name) is not None
        ]
        if not given:
            return self
        if len(given) < 3:
            raise ValueError(
                "a spike needs spike_rps, spike_start and spike_duration together; "
                f"only {', '.join(given)} was set, which would be ignored"
            )
        assert self.spike_start is not None and self.spike_duration is not None
        window_end = self.start_time + self.duration
        spike_end = self.spike_start + self.spike_duration
        if self.spike_start >= window_end or spike_end <= self.start_time:
            raise ValueError(
                f"the spike window ({self.spike_start:g} s to {spike_end:g} s) never "
                f"opens: traffic runs from {self.start_time:g} s to {window_end:g} s. "
                "Times are absolute, "
                "measured from the start of the run"
            )
        return self

    # -- what the profile adds up to (pure; used for validation and limits) --
    def _base_requests(self, a: float, b: float) -> float:
        """Requests the base curve (no spike) generates between ``a`` and ``b``."""
        if b <= a:
            return 0.0
        if self.pattern_type is PatternType.EXPONENTIAL:
            tau = self.growth_period
            growth = _pow2((b - self.start_time) / tau) - _pow2((a - self.start_time) / tau)
            return self.base_rps * tau / math.log(2.0) * growth
        if self.shape != "flat":
            peak = self.peak_multiplier
            area = _shape_area(self.shape, self._window_fraction(b), peak) - _shape_area(
                self.shape, self._window_fraction(a), peak
            )
            return self.base_rps * self.duration * area
        return self.base_rps * (b - a)

    def _spike_overlap(self, lo: float, hi: float) -> tuple[float, float]:
        """The part of ``[lo, hi]`` the spike covers (empty when ``b <= a``)."""
        if self.spike_rps is None or self.spike_start is None or self.spike_duration is None:
            return (0.0, 0.0)
        return (max(lo, self.spike_start), min(hi, self.spike_start + self.spike_duration))

    def expected_requests(self, horizon: float) -> float:
        """Mean number of requests generated before ``horizon`` seconds.

        The integral of the rate over the traffic window, cut off at the
        horizon (the run's duration): the base curve, with the spike rate in
        place of it while the spike is active.
        """
        lo, hi = self.start_time, min(self.start_time + self.duration, horizon)
        if hi <= lo:
            return 0.0
        total = self._base_requests(lo, hi)
        a, b = self._spike_overlap(lo, hi)
        if b > a and self.spike_rps is not None:
            total += self.spike_rps * (b - a) - self._base_requests(a, b)
        return total

    def peak_rps(self, horizon: float) -> float:
        """Highest arrival rate reached before ``horizon`` seconds."""
        lo, hi = self.start_time, min(self.start_time + self.duration, horizon)
        if hi <= lo:
            return 0.0
        peak = self.base_rps
        if self.pattern_type is PatternType.EXPONENTIAL:
            peak *= _pow2((hi - lo) / self.growth_period)
        if self.shape != "flat":
            # Every curve rises to its middle (black_friday holds its peak
            # from u = 0.5): before then the highest point is where the
            # horizon cuts it; a ramp keeps rising to its end.
            u = self._window_fraction(hi)
            top = self.peak_multiplier
            if self.shape == "ramp" or u < 0.5:
                top = shape_multiplier(self.shape, u, top)
            peak *= top
        a, b = self._spike_overlap(lo, hi)
        if b > a and self.spike_rps is not None:
            peak = max(peak, self.spike_rps)
        return peak


def _pow2(exponent: float) -> float:
    """``2 ** exponent``, as infinity where a float would overflow."""
    return math.inf if exponent > 1000.0 else float(2.0**exponent)


class ChaosEvent(BaseModel):
    """A single chaos-injection profile.

    ``intensity`` is interpreted per event type:

    * ``COMPONENT_FAILURE``: fraction of the victim's capacity removed
      (0.5 -> half the slots gone, 1.0 -> full outage).
    * ``NETWORK_LATENCY``: service-time multiplier minus one
      (0.5 -> 1.5x slower service).
    * ``CACHE_OUTAGE``: fraction of the cache hit-rate removed
      (1.0 -> cache fully disabled, every lookup misses).

    ``target`` (optional) names a specific component to victimise. For
    ``COMPONENT_FAILURE`` a random component is drawn without it, which is the
    original behaviour and keeps old configs unchanged. For ``CACHE_OUTAGE`` it
    must name a cache node; without it the first cache in node order is hit.
    ``NETWORK_LATENCY`` slows every component and ignores it. Incident
    playbooks (:mod:`sim_core.playbooks`) use this to aim an incident at, e.g.,
    the database node of whatever topology they are applied to.
    """

    model_config = _STRICT

    event_type: ChaosEventType
    intensity: float = Field(0.5, ge=0.0, le=1.0)
    start_time: float = Field(0.0, ge=0.0, description="When the first injection happens.")
    interval: float = Field(
        60.0,
        gt=0.0,
        description=(
            "Seconds of calm after one disruption ends before the next begins. "
            "The event repeats every duration + interval seconds."
        ),
    )
    duration: Optional[float] = Field(
        None,
        gt=0.0,
        description="How long each disruption lasts. Defaults to interval (as long down as up).",
    )
    target: Optional[str] = Field(
        None,
        description=(
            "Component name to hit (COMPONENT_FAILURE: any node; CACHE_OUTAGE: "
            "a cache node). Must be a node name in the topology. None "
            "(default) keeps the random-victim / first-cache behaviour."
        ),
    )


class SimulationConfig(BaseModel):
    """Top-level, fully-validated simulation specification."""

    model_config = _STRICT

    seed: Optional[int] = Field(None, description="RNG seed for reproducibility.")
    duration: float = Field(100.0, gt=0.0, description="Total simulation length in seconds.")
    metrics_interval: float = Field(
        1.0, gt=0.0, description="Sampling period for utilisation metrics, in seconds."
    )
    sla_target: Optional[float] = Field(
        None, gt=0.0, description="End-to-end latency SLA target in seconds (for compliance reporting)."
    )
    topology: GraphTopologyConfig
    traffic: TrafficPattern
    chaos: List[ChaosEvent] = Field(default_factory=list)

    @field_validator("topology", mode="before")
    @classmethod
    def _accept_legacy_topology(cls, value: object) -> object:
        """Backwards-compat: accept the old fixed-pipeline topology.

        A legacy :class:`TopologyConfig` (load_balancer/app_worker/cache/
        database), passed either as a model instance or as a plain dict with
        the legacy keys, is converted to the equivalent request-path graph so
        existing configs and code keep working unchanged.
        """
        if isinstance(value, TopologyConfig):
            return value.to_graph()
        if isinstance(value, dict) and "load_balancer" in value:
            return TopologyConfig.model_validate(value).to_graph()
        return value

    @model_validator(mode="after")
    def _validate_chaos_targets(self) -> "SimulationConfig":
        """Fail fast when a chaos event names a node the topology does not have,
        or aims a cache outage at a node that is not a cache."""
        names = {node.name for node in self.topology.nodes}
        caches = {
            node.name
            for node in self.topology.nodes
            if node.role is ComponentRole.CACHE or isinstance(node, CacheConfig)
        }
        for event in self.chaos:
            if event.target is None:
                continue
            if event.target not in names:
                raise ValueError(
                    f"chaos event targets unknown component {event.target!r} "
                    f"(known nodes: {sorted(names)})"
                )
            if event.event_type is ChaosEventType.CACHE_OUTAGE and event.target not in caches:
                raise ValueError(
                    f"cache_outage targets {event.target!r}, which is not a cache "
                    f"(cache nodes: {sorted(caches)})"
                )
        return self

    @model_validator(mode="after")
    def _traffic_fits_the_run(self) -> SimulationConfig:
        """Fail fast on a profile that sends nothing, or far too much."""
        traffic = self.traffic
        if traffic.start_time >= self.duration:
            raise ValueError(
                f"traffic starts at {traffic.start_time:g} s but the run ends at "
                f"{self.duration:g} s: no request would ever arrive. Lower "
                "traffic.start_time or raise duration"
            )
        expected = traffic.expected_requests(self.duration)
        if expected > MAX_EXPECTED_REQUESTS:
            peak = traffic.peak_rps(self.duration)
            growth = (
                f", or lengthen growth_period (the rate doubles every "
                f"{traffic.growth_period:g} s)"
                if traffic.pattern_type is PatternType.EXPONENTIAL
                else ""
            )
            raise ValueError(
                f"this traffic profile would generate about {expected:,.0f} requests, "
                f"peaking at {peak:,.0f} rps; the limit for one run is "
                f"{MAX_EXPECTED_REQUESTS:,}. Shorten the run or lower base_rps{growth}"
            )
        return self

    # -- file I/O: YAML / JSON ----------------------------------------------
    def _jsonable(self) -> Dict[str, Any]:
        """A plain-JSON-compatible view of this config (enums as strings,
        nested models as dicts) suitable for ``yaml.safe_dump`` / ``json.dump``."""
        return self.model_dump(mode="json")

    @classmethod
    def from_yaml(cls, path: str) -> "SimulationConfig":
        """Load and fully validate a simulation config from a YAML file.

        Both the new graph topology and the legacy fixed-pipeline topology
        (``load_balancer``/``app_worker``/``cache``/``database``) are accepted;
        the legacy form is converted to a graph on the way in, so old config
        files keep working unchanged.
        """
        import yaml  # local import: keeps PyYAML out of the import path for JSON-only users

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data is None:
            raise ValueError(f"YAML config file {path!r} is empty")
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, path: str) -> "SimulationConfig":
        """Load and fully validate a simulation config from a JSON file.

        Accepts both the new graph topology and the legacy fixed-pipeline
        topology (converted to a graph on the way in).
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.model_validate(data)

    def to_yaml(self, path: str) -> None:
        """Save this config as human-readable YAML (field order preserved)."""
        import yaml

        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self._jsonable(), fh, sort_keys=False, default_flow_style=False)

    def to_json(self, path: str) -> None:
        """Save this config as indented JSON (2-space indent, trailing newline)."""
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._jsonable(), fh, indent=2)
            fh.write("\n")
