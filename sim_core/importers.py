"""Import real infrastructure descriptions into a simulation topology.

This is the "upload your architecture" entry point (Phase A main feature):
it accepts the text of an infrastructure description — Eleven's native
YAML/JSON config *or* a ``terraform show -json`` document — and returns a
fully validated :class:`sim_core.config.SimulationConfig` the engine can run
immediately, plus an :class:`ImportReport` that says what was understood,
what was estimated, and what was skipped.

Design rules
------------
* **Pure and deterministic.** No RNG, no I/O (the text is passed in by the
  caller), no clock. The same input text always produces the same
  ``SimulationConfig``.
* **Config-only output.** The result is a Pydantic config model, exactly
  like a hand-written YAML file; the live SimPy world is still built
  separately by :class:`sim_core.engine.CloudSimulator`.
* **Honest about estimates.** Terraform documents carry no load, latency,
  or price semantics, so every capacity / service-time / cost / hit-rate
  we fill in is a labeled estimate recorded in ``report.assumptions``.
  Native configs carry their own numbers and are passed through untouched.
* **Connected graphs only.** The Terraform path has no request-flow
  information, so edges are inferred with a documented heuristic
  (entry → workers → cache → database chain; generic hops at 0.5) and a
  reachability safety-net guarantees the result satisfies
  :class:`sim_core.config.GraphTopologyConfig`'s validators.

Supported resource types (AWS-first, plus common GCP/Azure/k8s services);
anything else is recorded in ``report.unmapped_resources`` and skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from sim_core.config import (
    AppWorkerConfig,
    CacheConfig,
    ComponentConfig,
    ComponentRole,
    DatabaseConfig,
    Edge,
    GraphTopologyConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TrafficPattern,
)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ArchitectureImportError(ValueError):
    """The text could not be turned into a simulation topology.

    Carries an actionable message: a parse failure with both parsers'
    complaints, or the list of resource types that could not be mapped.
    """


class NodeSummary(BaseModel):
    """One imported node, as shown to the user in the preview."""

    name: str
    role: ComponentRole
    max_capacity: int
    service_time: float


class ImportReport(BaseModel):
    """What the import understood and what it had to assume.

    ``assumptions`` are things filled in for the user (every estimated
    parameter is listed here, honestly labeled); ``warnings`` are things
    the user should double-check (defaults applied, traffic invented);
    ``unmapped_resources`` are resources we skipped.
    """

    format: str = Field(..., description="native_yaml | native_json | terraform_json")
    source: Optional[str] = Field(None, description="Filename the user supplied, if any.")
    node_count: int
    edge_count: int
    nodes: List[NodeSummary]
    edges: List[Edge]
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unmapped_resources: List[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    """A runnable config plus the human-readable import report."""

    config: SimulationConfig
    report: ImportReport


# ---------------------------------------------------------------------------
# Format detection & parsing
# ---------------------------------------------------------------------------

TerraformDoc = Dict[str, Any]


def _try_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse *text* as a JSON object; None when it is not one."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _try_yaml(text: str) -> Optional[Dict[str, Any]]:
    """Parse *text* as a YAML mapping; None when it is not one."""
    import yaml  # local import: PyYAML stays optional for JSON-only users

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _is_terraform_doc(data: Mapping[str, Any]) -> bool:
    """``terraform show -json`` documents have ``values.root_module.resources``."""
    values = data.get("values")
    return (
        isinstance(values, Mapping)
        and isinstance(values.get("root_module"), Mapping)
        and "resources" in values["root_module"]  # type: ignore[index]
    )


@dataclass(frozen=True)
class _Parsed:
    """The parsed document plus which of the two paths should handle it."""

    data: Dict[str, Any]
    format: str  # "native" | "terraform"
    origin: str  # "json" | "yaml" — only used to label the report


def _parse(text: str) -> _Parsed:
    """Detect the format of *text* and parse it into a plain dict.

    JSON is tried first (a Terraform JSON doc is also valid YAML, but we
    want the JSON label for it); YAML is the fallback. Both parsers
    failing is an :class:`ArchitectureImportError` with both complaints.
    """
    if not text or not text.strip():
        raise ArchitectureImportError("the uploaded text is empty")

    as_json = _try_json(text)
    if as_json is not None:
        return _Parsed(as_json, "terraform" if _is_terraform_doc(as_json) else "native", "json")

    as_yaml = _try_yaml(text)
    if as_yaml is not None:
        return _Parsed(as_yaml, "terraform" if _is_terraform_doc(as_yaml) else "native", "yaml")

    raise ArchitectureImportError(
        "could not parse the text as JSON or YAML. Upload an Eleven config "
        "(topology/traffic, like examples/api_stack.yaml), a JSON/YAML file, "
        "or the output of `terraform show -json`."
    )


def _format_label(parsed: _Parsed) -> str:
    if parsed.format == "terraform":
        return "terraform_json"
    return f"native_{parsed.origin}"


# ---------------------------------------------------------------------------
# Native path: Eleven YAML/JSON configs pass through validation as-is
# ---------------------------------------------------------------------------

DEFAULT_BASE_RPS = 2.0
DEFAULT_DURATION = 120.0


def _import_native(data: Dict[str, Any], source: Optional[str], fmt: str) -> ImportResult:
    """Validate a native config, filling in defaults only where required.

    ``traffic`` is mandatory on :class:`SimulationConfig`; a document that
    describes a topology without traffic gets a labeled default (2.0 rps
    for 120 s) rather than a raw validation error. Everything else the
    config carries is trusted and passed through untouched.
    """
    warnings: List[str] = []
    if "traffic" not in data:
        duration = data.get("duration", DEFAULT_DURATION)
        data = {**data, "traffic": {"base_rps": DEFAULT_BASE_RPS, "duration": duration}}
        warnings.append(
            f"no traffic section supplied — assumed {DEFAULT_BASE_RPS} req/s "
            f"for {duration:.0f} s. Adjust the traffic block to match your real load."
        )

    try:
        config = SimulationConfig.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError and friends
        raise ArchitectureImportError(f"invalid Eleven config: {exc}") from exc

    return _result(config, fmt, source, warnings=warnings)


# ---------------------------------------------------------------------------
# Terraform path: resource types → roles, attributes → estimated numbers
# ---------------------------------------------------------------------------

#: Resource type → semantic role. AWS-first; GCP/Azure/k8s included where
#: they have one obvious equivalent. Queues/topics/object-storage become
#: plain "generic" hops (no first-class queue role exists in the engine).
TF_ROLE_MAP: Dict[str, ComponentRole] = {
    # load balancers / ingress
    "aws_lb": ComponentRole.LOAD_BALANCER,
    "aws_elb": ComponentRole.LOAD_BALANCER,
    "azurerm_lb": ComponentRole.LOAD_BALANCER,
    # application workers
    "aws_ecs_service": ComponentRole.WORKER,
    "aws_ec2_instance": ComponentRole.WORKER,
    "aws_lambda_function": ComponentRole.WORKER,
    "azurerm_app_service": ComponentRole.WORKER,
    "azurerm_linux_web_app": ComponentRole.WORKER,
    "azurerm_windows_web_app": ComponentRole.WORKER,
    "gcp_compute_instance": ComponentRole.WORKER,
    "gcp_compute_instance_group_manager": ComponentRole.WORKER,
    "kubernetes_deployment": ComponentRole.WORKER,
    # caches
    "aws_elasticache_replication_group": ComponentRole.CACHE,
    "aws_elasticache_cluster": ComponentRole.CACHE,
    "azurerm_redis_cache": ComponentRole.CACHE,
    # databases
    "aws_rds_cluster": ComponentRole.DATABASE,
    "aws_db_instance": ComponentRole.DATABASE,
    "aws_dynamodb_table": ComponentRole.DATABASE,
    "azurerm_postgresql_server": ComponentRole.DATABASE,
    "azurerm_mysql_server": ComponentRole.DATABASE,
    "azurerm_mssql_server": ComponentRole.DATABASE,
    "gcp_sql_database_instance": ComponentRole.DATABASE,
    # queues / topics / object storage: plain capacity hops
    "aws_sqs_queue": ComponentRole.GENERIC,
    "aws_sns_topic": ComponentRole.GENERIC,
    "aws_s3_bucket": ComponentRole.GENERIC,
}

#: Type-specific defaults: (max_capacity, service_time_s, cost_per_hour_usd).
TF_DEFAULTS: Dict[str, Tuple[int, float, float]] = {
    "aws_lb": (64, 0.2, 0.02),
    "aws_elb": (64, 0.2, 0.02),
    "azurerm_lb": (64, 0.2, 0.03),
    "aws_ecs_service": (8, 1.0, 0.05),
    "aws_ec2_instance": (4, 1.0, 0.05),
    "aws_lambda_function": (16, 0.4, 0.01),
    "azurerm_app_service": (16, 0.8, 0.05),
    "azurerm_linux_web_app": (16, 0.8, 0.05),
    "azurerm_windows_web_app": (16, 0.8, 0.05),
    "gcp_compute_instance": (4, 1.0, 0.06),
    "gcp_compute_instance_group_manager": (8, 1.0, 0.06),
    "kubernetes_deployment": (8, 1.0, 0.05),
    "aws_elasticache_replication_group": (8, 0.1, 0.03),
    "aws_elasticache_cluster": (8, 0.1, 0.03),
    "azurerm_redis_cache": (8, 0.1, 0.03),
    "aws_rds_cluster": (8, 1.5, 0.15),
    "aws_db_instance": (8, 1.5, 0.15),
    "aws_dynamodb_table": (32, 0.4, 0.05),
    "azurerm_postgresql_server": (8, 1.5, 0.12),
    "azurerm_mysql_server": (8, 1.5, 0.12),
    "azurerm_mssql_server": (8, 1.5, 0.18),
    "gcp_sql_database_instance": (8, 1.5, 0.15),
    "aws_sqs_queue": (20, 0.3, 0.005),
    "aws_sns_topic": (20, 0.3, 0.002),
    "aws_s3_bucket": (50, 0.2, 0.004),
}

#: Capacity by instance size — a deliberately coarse vCPU-ish ladder. The
#: point is a plausible shape, not a price sheet; every value is labeled
#: "estimated" in the report.
_EC2_CAPACITY = {
    "t3.micro": 1, "t3.small": 2, "t3.medium": 4, "t3.large": 8, "t3.xlarge": 16,
    "t4g.small": 2, "t4g.medium": 4, "t4g.large": 8,
    "m5.large": 8, "m5.xlarge": 16, "m6i.large": 8,
}
_EC2_COST = {
    "t3.micro": 0.01, "t3.small": 0.02, "t3.medium": 0.04, "t3.large": 0.08, "t3.xlarge": 0.16,
    "t4g.small": 0.017, "t4g.medium": 0.033, "t4g.large": 0.066,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m6i.large": 0.096,
}
_RDS_CAPACITY = {
    "db.t3.micro": 2, "db.t3.small": 4, "db.t3.medium": 8, "db.t3.large": 16,
    "db.m5.large": 16, "db.m5.xlarge": 32, "db.r6g.large": 16,
}
_RDS_COST = {
    "db.t3.micro": 0.025, "db.t3.small": 0.05, "db.t3.medium": 0.10, "db.t3.large": 0.20,
    "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.r6g.large": 0.23,
}
_CACHE_CAPACITY = {
    "cache.t3.micro": 4, "cache.t3.small": 4, "cache.t3.medium": 8,
    "cache.t3.large": 16, "cache.m5.large": 16,
}
_CACHE_COST = {
    "cache.t3.micro": 0.017, "cache.t3.small": 0.017, "cache.t3.medium": 0.033,
    "cache.t3.large": 0.066, "cache.m5.large": 0.121,
}
_GCP_MACHINE_CAPACITY = {
    "e2-small": 2, "e2-medium": 4, "e2-large": 8, "n1-standard-1": 2,
    "n1-standard-2": 4, "n2-standard-2": 4, "n2-standard-4": 8,
}
_GCP_MACHINE_COST = {
    "e2-small": 0.02, "e2-medium": 0.04, "e2-large": 0.08, "n1-standard-1": 0.05,
    "n1-standard-2": 0.10, "n2-standard-2": 0.12, "n2-standard-4": 0.23,
}


def _first_present(attrs: Mapping[str, Any], *keys: str) -> Any:
    """First non-None attribute value among *keys*, else None."""
    for key in keys:
        if key in attrs and attrs[key] is not None:
            return attrs[key]
    return None


def _estimate(
    rtype: str, name: str, attrs: Mapping[str, Any]
) -> Tuple[int, float, Optional[float], float, Optional[str]]:
    """Capacity / service time / hit-rate / cost for one Terraform resource.

    Returns ``(max_capacity, service_time, hit_rate, cost_per_hour, note)``
    where ``note`` is the estimate label (None when pure defaults applied
    — those still get a generic assumption line by the caller).
    """
    base_cap, base_st, base_cost = TF_DEFAULTS[rtype]

    if rtype == "aws_ec2_instance":
        itype = str(_first_present(attrs, "instance_type") or "")
        if itype:
            return (
                _EC2_CAPACITY.get(itype, 4), base_st, None,
                _EC2_COST.get(itype, base_cost),
                f"capacity/cost estimated from instance type {itype!r}",
            )

    if rtype in ("aws_ecs_service", "kubernetes_deployment"):
        count = _first_present(attrs, "desired_count", "replicas", "replica_count")
        if isinstance(count, (int, float)) and count > 0:
            return (
                max(2, int(count) * 4), base_st, None,
                round(base_cost * max(1, int(count)), 3),
                f"capacity estimated from {count} replicas (≈4 slots each)",
            )

    if rtype in ("aws_elasticache_replication_group", "aws_elasticache_cluster", "azurerm_redis_cache"):
        node = str(_first_present(attrs, "node_type", "family") or "")
        if node:
            return (
                _CACHE_CAPACITY.get(node, 8), base_st, 0.8,
                _CACHE_COST.get(node, base_cost),
                f"capacity/cost estimated from node type {node!r}; hit-rate assumed 0.8",
            )
        return (base_cap, base_st, 0.8, base_cost, "hit-rate assumed 0.8 (typical read-through cache)")

    if rtype in ("aws_rds_cluster", "aws_db_instance", "gcp_sql_database_instance",
                 "azurerm_postgresql_server", "azurerm_mysql_server", "azurerm_mssql_server"):
        klass = str(_first_present(attrs, "instance_class", "instance_type", "machine_type") or "")
        if klass:
            return (
                _RDS_CAPACITY.get(klass, 8), base_st, None,
                _RDS_COST.get(klass, base_cost),
                f"capacity/cost estimated from instance class {klass!r}",
            )

    if rtype == "gcp_compute_instance":
        machine = str(_first_present(attrs, "machine_type") or "")
        if machine:
            return (
                _GCP_MACHINE_CAPACITY.get(machine, 4), base_st, None,
                _GCP_MACHINE_COST.get(machine, base_cost),
                f"capacity/cost estimated from machine type {machine!r}",
            )

    if rtype == "aws_lambda_function":
        mem = _first_present(attrs, "memory_size")
        if isinstance(mem, (int, float)):
            return (base_cap, base_st, None, base_cost,
                    f"concurrency/cost estimated from memory size {int(mem)} MB")

    return (base_cap, base_st, None, base_cost, None)


def _unique_name(used: set[str], base: str) -> str:
    """Deduplicate resource names (TF allows `default` per type)."""
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    used.add(f"{base}-{i}")
    return f"{base}-{i}"


def _build_edges(
    nodes: List[ComponentConfig], assumptions: List[str]
) -> List[Edge]:
    """Infer a connected request-flow graph for TF-imported nodes.

    Heuristic (documented in the module docstring): entry (LB, else first
    worker) → each worker (split evenly) → each cache (p=1.0) → each
    database (p=1.0, the cache's miss path). Generic hops (queues, topics,
    object storage) get p=0.5 from every upstream hop, since TF carries no
    call-graph data and we only know "some" traffic touches them.
    A reachability pass at the end links anything still orphaned, so the
    result always satisfies the topology validators.
    """
    by_role: Dict[ComponentRole, List[ComponentConfig]] = {
        role: [] for role in ComponentRole
    }
    for node in nodes:
        by_role[node.role].append(node)

    lbs, workers = by_role[ComponentRole.LOAD_BALANCER], by_role[ComponentRole.WORKER]
    caches, dbs = by_role[ComponentRole.CACHE], by_role[ComponentRole.DATABASE]
    generics = by_role[ComponentRole.GENERIC]

    if len(nodes) <= 1:
        return []

    upstream = lbs or workers
    if not upstream:
        # No entry role at all (e.g. only a db + a queue): first node is the
        # entry and reaches everything directly.
        entry = nodes[0]
        edges: List[Edge] = []
        for node in nodes[1:]:
            if node.name == entry.name:
                continue
            p = 0.5 if node.role is ComponentRole.GENERIC else 1.0
            edges.append(Edge(source=entry.name, target=node.name, probability=p))
        assumptions.append(
            f"no load balancer or worker found — {entry.name!r} is the entry node "
            "and reaches the other components directly (inferred)"
        )
        return edges

    edges = []
    for source in upstream:
        if workers and source not in workers:
            # entry fans out to the worker pool, split evenly
            share = 1.0 / len(workers)
            for worker in workers:
                edges.append(Edge(source=source.name, target=worker.name,
                                  probability=round(share, 4)))
        for cache in caches:
            edges.append(Edge(source=source.name, target=cache.name, probability=1.0))
        if not caches:
            for db in dbs:
                edges.append(Edge(source=source.name, target=db.name, probability=1.0))
        for generic in generics:
            edges.append(Edge(source=source.name, target=generic.name, probability=0.5))

    for worker in workers:
        for cache in caches:
            edges.append(Edge(source=worker.name, target=cache.name, probability=1.0))
        if not caches:
            for db in dbs:
                edges.append(Edge(source=worker.name, target=db.name, probability=1.0))
        for generic in generics:
            edges.append(
                Edge(source=worker.name, target=generic.name, probability=0.5),
            )

    for cache in caches:
        for db in dbs:
            edges.append(Edge(source=cache.name, target=db.name, probability=1.0))

    if workers and not lbs:
        # Multiple workers, no LB: the first worker is the entry; link it to
        # its siblings so every node stays on the request path.
        entry = workers[0]
        for sibling in workers[1:]:
            edges.append(Edge(source=entry.name, target=sibling.name, probability=1.0))
        assumptions.append(
            f"no load balancer found — {entry.name!r} is the entry and fans out "
            "to the other workers (inferred)"
        )
    if generics:
        assumptions.append(
            "queues/topics/object-storage are plain capacity hops at 0.5 "
            "probability — Terraform carries no call-graph data, so this is inferred"
        )

    # Reachability safety net: link anything still unreachable from the entry.
    entry = (lbs or workers)[0]
    adjacency: Dict[str, List[str]] = {node.name: [] for node in nodes}
    for edge in edges:
        adjacency[edge.source].append(edge.target)
    seen: set[str] = {entry.name}
    frontier = [entry.name]
    while frontier:
        for target in adjacency[frontier.pop()]:
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    for node in nodes:
        if node.name in seen:
            continue
        p = 0.5 if node.role is ComponentRole.GENERIC else 1.0
        edges.append(Edge(source=entry.name, target=node.name, probability=p))
        assumptions.append(f"{node.name!r} was unreachable — linked directly from {entry.name!r} (inferred)")

    # Deduplicate identical edges (e.g. entry==worker cases).
    unique: List[Edge] = []
    for edge in edges:
        if edge not in unique:
            unique.append(edge)
    return unique


def _import_terraform(doc: Dict[str, Any], source: Optional[str]) -> ImportResult:
    """Map a ``terraform show -json`` document onto a simulation topology."""
    root = doc["values"]["root_module"]  # type: ignore[index]
    resources: List[Dict[str, Any]] = list(root.get("resources") or [])

    nodes: List[ComponentConfig] = []
    assumptions: List[str] = []
    warnings: List[str] = []
    unmapped: List[str] = []
    used_names: set[str] = set()

    for resource in resources:
        rtype = str(resource.get("type") or "")
        role = TF_ROLE_MAP.get(rtype)
        if role is None:
            address = resource.get("address") or rtype or "unknown"
            unmapped.append(str(address))
            continue

        name = _unique_name(used_names, str(resource.get("name") or rtype))
        instances = resource.get("instances") or []
        attrs: Mapping[str, Any] = (
            instances[0].get("attributes") or {} if instances else {}
        )

        capacity, service_time, hit_rate, cost, note = _estimate(rtype, name, attrs)
        kwargs: Dict[str, Any] = dict(
            name=name,
            role=role,
            max_capacity=int(capacity),
            service_time=float(service_time),
            cost_per_hour=float(cost),
        )
        if hit_rate is not None:
            kwargs["hit_rate"] = float(hit_rate)
        # Construct the role-specific subclass so subclass-only fields
        # (CacheConfig.hit_rate) survive serialization and engine reconstitution.
        node_class: type = {
            ComponentRole.LOAD_BALANCER: LoadBalancerConfig,
            ComponentRole.WORKER: AppWorkerConfig,
            ComponentRole.CACHE: CacheConfig,
            ComponentRole.DATABASE: DatabaseConfig,
        }.get(role, ComponentConfig)
        nodes.append(node_class(**kwargs))
        assumptions.append(
            f"{name!r} ({rtype}): "
            + (note if note else "capacity/service time/cost are type-level estimates")
            + " — all estimated, no live metrics available"
        )

    if not nodes:
        listed = sorted({u.split(".", 1)[0] if "." in u else u for u in unmapped}) or ["(none)"]
        raise ArchitectureImportError(
            "no mappable resources found. Mappable types: "
            + ", ".join(sorted(TF_ROLE_MAP))
            + ". Unmapped in this document: " + ", ".join(listed) + "."
        )

    if unmapped:
        warnings.append(
            f"skipped {len(unmapped)} unmappable resource(s): " + ", ".join(unmapped)
        )

    edges = _build_edges(nodes, assumptions)

    # TF carries no traffic semantics: invent a labeled default.
    warnings.append(
        f"no traffic info in Terraform — assumed {DEFAULT_BASE_RPS} req/s "
        f"for {DEFAULT_DURATION:.0f} s. Adjust to your real load before judging results."
    )

    config = SimulationConfig(
        seed=None,
        duration=DEFAULT_DURATION,
        metrics_interval=2.0,
        sla_target=None,
        topology=GraphTopologyConfig(nodes=nodes, edges=edges, entry_node=None),
        traffic=TrafficPattern(base_rps=DEFAULT_BASE_RPS, duration=DEFAULT_DURATION),
        chaos=[],
    )
    return _result(config, "terraform_json", source, assumptions=assumptions, warnings=warnings, unmapped=unmapped)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _result(
    config: SimulationConfig,
    fmt: str,
    source: Optional[str],
    assumptions: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    unmapped: Optional[List[str]] = None,
) -> ImportResult:
    """Assemble the ImportResult + report from a validated config."""
    graph = config.topology
    return ImportResult(
        config=config,
        report=ImportReport(
            format=fmt,
            source=source,
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            nodes=[
                NodeSummary(
                    name=node.name,
                    role=node.role,
                    max_capacity=node.max_capacity,
                    service_time=node.service_time,
                )
                for node in graph.nodes
            ],
            edges=list(graph.edges),
            assumptions=assumptions or [],
            warnings=warnings or [],
            unmapped_resources=unmapped or [],
        ),
    )


def import_architecture(text: str, filename: Optional[str] = None) -> ImportResult:
    """Turn an uploaded architecture description into a runnable config.

    Accepts Eleven native YAML/JSON (the ``examples/api_stack.yaml`` shape)
    or ``terraform show -json`` output; the format is auto-detected.
    Raises :class:`ArchitectureImportError` with an actionable message when
    nothing mappable is found. Deterministic: identical text yields an
    identical :class:`SimulationConfig`.
    """
    parsed = _parse(text)
    if parsed.format == "terraform":
        return _import_terraform(parsed.data, filename)
    return _import_native(parsed.data, filename, _format_label(parsed))


__all__ = [
    "ArchitectureImportError",
    "ImportReport",
    "ImportResult",
    "NodeSummary",
    "TF_ROLE_MAP",
    "import_architecture",
]
