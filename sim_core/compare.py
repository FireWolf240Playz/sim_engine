"""Compare-runs engine — one core, three faces.

Runs multiple :class:`~sim_core.config.SimulationConfig` variants and diffs
their summaries. The three product faces all reduce to the same primitive
(run configs, compare summaries) and differ only in how they produce the
config list and what they display:

* **multi-cloud** — the same logical architecture, each component re-calibrated
  with a provider preset (AWS / Azure / GCP) via :func:`apply_provider`;
* **what-if A/B** — the baseline plus one or more patched parameters via
  :func:`set_path` (e.g. ``app_worker.max_capacity=20``);
* **capacity sweep** — one parameter swept over a value range via
  :func:`sweep`, with :func:`knee_point` locating the directional
  right-sizing knee.

The engine is a pure library: no file I/O, no CLI, no new dependencies.
Every run is an independent :class:`~sim_core.engine.CloudSimulator` start,
so runs are reproducible whenever the config carries a fixed ``seed``
(set it for meaningful A/B comparisons — an unseeded run is random by
design).

Directional estimates only: preset calibrations and the knee heuristic are
documented approximations, not benchmarks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sim_core.config import (
    ComponentRole,
    SimulationConfig,
)
from sim_core.engine import CloudSimulator
from sim_core.presets import PRESETS

__all__ = [
    "PROVIDERS",
    "COMPARE_METRICS",
    "RunResult",
    "run_one",
    "run_many",
    "diff_runs",
    "set_path",
    "apply_provider",
    "sweep",
    "knee_point",
]

#: Provider order used by the multi-cloud face (matches the preset docs).
PROVIDERS: Tuple[str, ...] = ("aws", "azure", "gcp")

#: Headline metrics compared across runs: (summary key, display kind).
#: Kinds: ``int`` | ``frac`` (0-1 fraction) | ``sec`` (seconds) | ``usd``.
COMPARE_METRICS: Tuple[Tuple[str, str], ...] = (
    ("requests", "int"),
    ("completion_rate", "frac"),
    ("sla_compliance", "frac"),
    ("p50_latency", "sec"),
    ("p95_latency", "sec"),
    ("p99_latency", "sec"),
    ("total_retries", "int"),
    ("total_cost", "usd"),
)


@dataclass(frozen=True)
class RunResult:
    """One labelled simulation run: config, summary, and optional sweep value.

    ``parameter_value`` is set by :func:`sweep` so :func:`knee_point` can
    read the swept value back without parsing labels.
    """

    label: str
    summary: Dict[str, Any]
    config: Optional[SimulationConfig] = None
    parameter_value: Optional[float] = None


def _coerce(value: Any) -> Any:
    """Coerce CLI-style strings to numbers; pass everything else through.

    ``"20" -> 20``, ``"2.5" -> 2.5``, ``"abc"`` stays a string (Pydantic
    then raises a clear validation error if the field rejects it).
    """
    if isinstance(value, str):
        text = value.strip()
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                pass
    return value


def run_one(
    label: str,
    config: SimulationConfig,
    parameter_value: Optional[float] = None,
) -> RunResult:
    """Run a single config and wrap the summary in a labelled result."""
    summary = CloudSimulator(config).run()
    return RunResult(
        label=label,
        summary=summary,
        config=config,
        parameter_value=parameter_value,
    )


def run_many(pairs: Sequence[Tuple[str, SimulationConfig]]) -> List[RunResult]:
    """Run every (label, config) pair, in order, as an independent simulation.

    Each run is a fresh :class:`CloudSimulator`, so results are isolated;
    with a fixed ``seed`` in each config the comparison is reproducible.
    """
    return [run_one(label, config) for label, config in pairs]


def diff_runs(
    results: Sequence[RunResult],
    baseline_index: int = 0,
) -> Dict[str, Any]:
    """Diff every run against the baseline run (index ``baseline_index``).

    Returns ``{"baseline": label, "runs": [{"label", "values", "deltas",
    "sizing"}, ...]}`` where ``deltas`` are per-metric differences vs the
    baseline (``None`` where either side is missing) and ``sizing`` maps
    each component to its right-sizing ``status`` from the summary.
    """
    if not results:
        raise ValueError("diff_runs needs at least one result")
    baseline = results[baseline_index].summary
    runs: List[Dict[str, Any]] = []
    for result in results:
        values = {key: result.summary.get(key) for key, _kind in COMPARE_METRICS}
        deltas: Dict[str, Optional[float]] = {}
        for key, _kind in COMPARE_METRICS:
            base_value, run_value = baseline.get(key), result.summary.get(key)
            if base_value is None or run_value is None:
                deltas[key] = None
            else:
                deltas[key] = float(run_value) - float(base_value)
        sizing = {
            component: info.get("status")
            for component, info in (result.summary.get("component_sizing") or {}).items()
        }
        runs.append(
            {
                "label": result.label,
                "values": values,
                "deltas": deltas,
                "sizing": sizing,
            }
        )
    return {"baseline": results[baseline_index].label, "runs": runs}


# ---------------------------------------------------------------------------
# Config patching (what-if workhorse)
# ---------------------------------------------------------------------------

def set_path(config: SimulationConfig, path: str, value: Any) -> SimulationConfig:
    """Return a new config with the dotted ``path`` set to ``value``.

    Supported path forms (the original frozen config is never mutated):

    * top-level scalars: ``duration``, ``metrics_interval``, ``sla_target``;
    * traffic fields: ``traffic.base_rps``, ``traffic.spike_rps``, ...;
    * chaos events by index: ``chaos.0.intensity``;
    * topology nodes by name: ``app_worker.max_capacity``,
      ``redis.hit_rate`` (node name as first segment).

    The patched config is re-validated through Pydantic, so out-of-range
    values raise ``ValidationError`` with the offending field named.
    """
    data = config.model_dump(mode="json")
    parts = [part for part in path.split(".") if part != ""]
    if len(parts) < 1:
        raise ValueError(f"empty config path: {path!r}")
    head = parts[0]
    value = _coerce(value)

    if head in ("duration", "metrics_interval", "sla_target"):
        if len(parts) != 1:
            raise ValueError(f"too many path segments for {head!r}: {path!r}")
        data[head] = value
        return SimulationConfig.model_validate(data)

    if head == "traffic":
        if len(parts) != 2:
            raise ValueError(f"traffic paths need exactly two segments: {path!r}")
        data["traffic"][parts[1]] = value
        return SimulationConfig.model_validate(data)

    if head == "chaos":
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError(f"chaos paths look like 'chaos.0.intensity': {path!r}")
        data["chaos"][int(parts[1])][parts[2]] = value
        return SimulationConfig.model_validate(data)

    # Node-scoped path: <node-name>.<field>
    if len(parts) != 2:
        raise ValueError(
            f"unknown config path {path!r} (expected top-level, traffic.*, "
            "chaos.<i>.*, or <node>.<field>)"
        )
    for node in data["topology"]["nodes"]:
        if node["name"] == head:
            node[parts[1]] = value
            break
    else:
        names = [node["name"] for node in data["topology"]["nodes"]]
        raise ValueError(f"unknown node {head!r} in path {path!r}; nodes: {names}")
    return SimulationConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Multi-cloud face: provider preset calibration
# ---------------------------------------------------------------------------

#: Preset name per provider per role (generic / external_api roles have no
#: preset and pass through unchanged).
_ROLE_PRESET: Dict[str, Dict[ComponentRole, str]] = {
    "aws": {
        ComponentRole.LOAD_BALANCER: "aws_alb",
        ComponentRole.WORKER: "aws_app_worker",
        ComponentRole.CACHE: "aws_elasticache",
        ComponentRole.DATABASE: "aws_rds_small",
    },
    "azure": {
        ComponentRole.LOAD_BALANCER: "azure_app_gateway",
        ComponentRole.WORKER: "azure_app_worker",
        ComponentRole.CACHE: "azure_cache",
        ComponentRole.DATABASE: "azure_sql_small",
    },
    "gcp": {
        ComponentRole.LOAD_BALANCER: "gcp_lb",
        ComponentRole.WORKER: "gcp_app_worker",
        ComponentRole.CACHE: "gcp_memorystore",
        ComponentRole.DATABASE: "gcp_cloud_sql_small",
    },
}

#: The calibration fields a provider preset swaps in (hit_rate is cache-only).
_SWAP_FIELDS: Tuple[str, ...] = ("max_capacity", "service_time", "cost_per_hour")


def apply_provider(config: SimulationConfig, provider: str) -> SimulationConfig:
    """Re-calibrate every presettable node with ``provider`` presets.

    For each node whose role has a preset for the provider, the preset's
    ``max_capacity`` / ``service_time`` / ``cost_per_hour`` (plus
    ``hit_rate`` on caches) replaces the current values. The node's
    ``name``, ``role``, and any user-set ``timeout`` / ``retry_*`` fields
    are preserved, so the same logical architecture stays intact and only
    the provider calibration changes. Nodes without a preset (generic,
    external_api) pass through untouched.

    Directional estimate: presets approximate small on-demand tiers and are
    not guaranteed benchmarks.
    """
    table = _ROLE_PRESET.get(provider)
    if table is None:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of {sorted(_ROLE_PRESET)}"
        )
    data = config.model_dump(mode="json")
    for node in data["topology"]["nodes"]:
        preset_name = table.get(node.get("role"))
        if preset_name is None:
            continue
        preset = PRESETS[preset_name](name=node["name"]).model_dump(mode="json")
        for field in _SWAP_FIELDS:
            node[field] = preset[field]
        if "hit_rate" in preset:
            node["hit_rate"] = preset["hit_rate"]
    return SimulationConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Capacity-sweep face
# ---------------------------------------------------------------------------

def sweep(
    config: SimulationConfig,
    param_path: str,
    values: Sequence[Any],
) -> List[RunResult]:
    """Sweep ``param_path`` over ``values``, one run per value.

    Labels look like ``app_worker.max_capacity=8``; each result carries
    ``parameter_value`` so :func:`knee_point` can read it back.
    """
    results: List[RunResult] = []
    for value in values:
        patched = set_path(config, param_path, value)
        numeric = _coerce(value)
        parameter = float(numeric) if isinstance(numeric, (int, float)) else None
        results.append(
            run_one(f"{param_path}={value}", patched, parameter_value=parameter)
        )
    return results


def knee_point(
    results: Sequence[RunResult],
    metric: str = "p95_latency",
    threshold: float = 0.05,
) -> Optional[float]:
    """Locate the directional right-sizing knee in a sweep.

    A step *pays off* when it improves ``metric`` by at least
    ``threshold`` (fraction) relative to the previous point (e.g. a 20%
    point improving p95 latency by >= 5%). The knee is the last point that
    still paid off — the largest capacity you would buy. If the metric
    improves at every step the knee is the last value (sweep higher); if
    no step pays off the knee is the first value.

    Heuristic, single-run data: treat it as a directional hint, not a
    benchmark. Returns ``None`` when fewer than two results carry a
    ``parameter_value``.
    """
    ordered = sorted(
        (r for r in results if r.parameter_value is not None),
        key=lambda r: r.parameter_value,  # type: ignore[type-var]
    )
    if len(ordered) < 2:
        return None
    values = [r.parameter_value for r in ordered]
    series = [r.summary.get(metric) for r in ordered]
    knee = values[0]
    for i in range(1, len(ordered)):
        previous, current = series[i - 1], series[i]
        if previous is None or current is None or previous == 0:
            continue
        improvement = (float(previous) - float(current)) / float(previous)
        if improvement >= threshold:
            knee = values[i]
    return knee
