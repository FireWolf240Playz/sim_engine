"""Pydantic v2 configuration models for the Eleven cloud-resilience simulator.

These models hold *validated configuration data only* (numbers, strings,
enums). They are deliberately **not** used as live simulation state: the live
SimPy resources are created separately from these values inside
:mod:`sim_core.topology`. Keeping config (immutable data) and live state
(mutable SimPy objects) separate is a hard architectural rule of the project.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    model_config = ConfigDict(frozen=True)

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


class DatabaseConfig(ComponentConfig):
    """Primary data store (e.g. Postgres)."""

    role: ComponentRole = ComponentRole.DATABASE


class TopologyConfig(BaseModel):
    """The ordered request path: load balancer -> worker -> (cache) -> database.

    Exactly one of each required role; the cache is optional.
    """

    model_config = ConfigDict(frozen=True)

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
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(..., min_length=1, description="Name of the source node.")
    target: str = Field(..., min_length=1, description="Name of the target node.")
    probability: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="Probability (0-1) that a request at the source follows this edge.",
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

    model_config = ConfigDict(frozen=True)

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
        the concrete type is resolved explicitly: by ``hit_rate`` presence for
        caches, by ``role`` otherwise. Model instances pass through untouched.
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
                if "hit_rate" in data:
                    resolved.append(CacheConfig.model_validate(data))
                elif "role" in data and data["role"] in role_map:
                    resolved.append(role_map[data["role"]].model_validate(data))
                else:
                    # generic / external_api / unknown roles: plain component
                    resolved.append(ComponentConfig.model_validate(data))
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


class TrafficPattern(BaseModel):
    """Traffic generation profile.

    ``base_rps`` is the sustained arrival rate. ``pattern_type`` shapes how
    that rate evolves over ``duration`` seconds. The optional ``spike_*``
    fields define a single high-rate window layered on top of the base curve.
    Arrivals between emissions are modelled as a Poisson process (exponential
    inter-arrival times) rather than fixed spacing.
    """

    model_config = ConfigDict(frozen=True)

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


class ChaosEvent(BaseModel):
    """A single chaos-injection profile.

    ``intensity`` is interpreted per event type:

    * ``COMPONENT_FAILURE``: fraction of the victim's capacity removed
      (0.5 -> half the slots gone, 1.0 -> full outage).
    * ``NETWORK_LATENCY``: service-time multiplier minus one
      (0.5 -> 1.5x slower service).
    * ``CACHE_OUTAGE``: fraction of the cache hit-rate removed
      (1.0 -> cache fully disabled, every lookup misses).
    """

    model_config = ConfigDict(frozen=True)

    event_type: ChaosEventType
    intensity: float = Field(0.5, ge=0.0, le=1.0)
    start_time: float = Field(0.0, ge=0.0, description="When the first injection happens.")
    interval: float = Field(60.0, gt=0.0, description="Seconds between injections.")
    duration: Optional[float] = Field(
        None,
        gt=0.0,
        description="How long each disruption lasts. Defaults to the gap between injections.",
    )


class SimulationConfig(BaseModel):
    """Top-level, fully-validated simulation specification."""

    model_config = ConfigDict(frozen=True)

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
