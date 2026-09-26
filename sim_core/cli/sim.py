"""The ``run`` / ``demo`` simulation subcommands.

Owns the simulation-side CLI surface: the built-in demo topology, config
file loading, and running the simulator with PNG + JSON outputs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sim_core import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ChaosEventType,
    CloudSimulator,
    DatabaseConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
    compare,
    render_report,
)
from sim_core.viz import render_comparison

DEFAULT_REPORT = "eleven_report.png"
DEFAULT_JSON = "summary.json"


def default_config() -> SimulationConfig:
    """A representative pre-deployment cloud topology with chaos.

    The numbers are chosen so that the base load keeps every component
    comfortably below capacity (SLA compliance is high), while the traffic
    spike at t=60 and the three chaos windows visibly degrade latency and
    push SLA compliance down - which is exactly the resilience signal this
    tool is meant to surface.
    """
    topology = TopologyConfig(
        load_balancer=LoadBalancerConfig(
            name="load-balancer",
            max_capacity=12,
            service_time=0.5,
        ),
        app_worker=AppWorkerConfig(
            name="app-worker",
            max_capacity=6,
            service_time=2.0,
        ),
        cache=CacheConfig(
            name="redis-cache",
            max_capacity=8,
            service_time=0.3,
            hit_rate=0.85,
        ),
        database=DatabaseConfig(
            name="postgres",
            max_capacity=4,
            service_time=5.0,
        ),
    )

    traffic = TrafficPattern(
        base_rps=1.5,
        duration=120.0,
        spike_rps=6.0,
        spike_start=60.0,
        spike_duration=20.0,
    )

    chaos: List[ChaosEvent] = [
        ChaosEvent(
            event_type=ChaosEventType.COMPONENT_FAILURE,
            intensity=0.5,
            start_time=30.0,
            interval=40.0,
            duration=12.0,
        ),
        ChaosEvent(
            event_type=ChaosEventType.CACHE_OUTAGE,
            intensity=1.0,
            start_time=50.0,
            interval=45.0,
            duration=10.0,
        ),
        ChaosEvent(
            event_type=ChaosEventType.NETWORK_LATENCY,
            intensity=0.7,
            start_time=70.0,
            interval=50.0,
            duration=10.0,
        ),
    ]

    return SimulationConfig(
        seed=12,
        duration=130.0,
        metrics_interval=2.0,
        sla_target=22.0,
        topology=topology,
        traffic=traffic,
        chaos=chaos,
    )


def _fmt_seconds(value: Optional[float]) -> str:
    """Format a latency value in seconds (``n/a`` when absent)."""
    return "n/a" if value is None else f"{value:.3f}"


def _fmt_fraction(value: Optional[float]) -> str:
    """Format a 0-1 fraction as a percentage (``n/a`` when absent)."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _format_summary(summary: Dict[str, Any]) -> str:
    """Render the headline summary as a human-readable block of text."""
    lines = [
        "Eleven - pre-deployment cloud-resilience report",
        "=" * 52,
        f"Requests served .......... {summary['requests']}",
        f"Failed requests .......... {summary['failed_requests']}",
        f"Timeout retries .......... {summary['total_retries']}",
        f"Completion rate .......... {_fmt_fraction(summary['completion_rate'])}",
        f"SLA compliance ........... {_fmt_fraction(summary['sla_compliance'])}",
        f"Avg latency (s) .......... {_fmt_seconds(summary['avg_latency'])}",
        f"P50 latency (s) .......... {_fmt_seconds(summary['p50_latency'])}",
        f"P95 latency (s) .......... {_fmt_seconds(summary['p95_latency'])}",
        f"P99 latency (s) .......... {_fmt_seconds(summary['p99_latency'])}",
        f"Cache hit rate ........... {_fmt_fraction(summary['cache_hit_rate'])}",
    ]
    total_cost = summary.get("total_cost")
    if total_cost:
        lines.append(f"Estimated cost ........... ${total_cost:.2f}")
        for name, cost in summary.get("cost_breakdown_by_component", {}).items():
            lines.append(f"  - {name:<24} ${cost:.2f}")
    sizing = summary.get("component_sizing") or {}
    if sizing:
        lines.append("")
        lines.append("Right-sizing check (directional, from sampled utilisation)")
        lines.append("-" * 52)
        for name, info in sizing.items():
            lines.append(
                f"  - {name:<24} {info['status']:<12} "
                f"(avg {info['mean_utilization'] * 100:.0f}% util, "
                f"size-to {info['recommended_capacity']})"
            )
    return "\n".join(lines)


def load_config(path: str) -> SimulationConfig:
    """Load a config file, choosing the parser by extension.

    ``.yaml`` / ``.yml`` go through YAML parsing, ``.json`` through JSON;
    anything else defaults to YAML (it is a superset of JSON).
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return SimulationConfig.from_json(path)
    return SimulationConfig.from_yaml(path)


def run_simulation(
    config: SimulationConfig,
    report_path: str,
    json_path: str,
) -> Dict[str, Any]:
    """Run the simulation for ``config`` and write the PNG + JSON outputs.

    Returns the summary dict (also written to ``json_path``).
    """
    simulator = CloudSimulator(config)
    summary = simulator.run()
    print(_format_summary(summary))

    png = render_report(simulator.collector, output_path=report_path)
    print(f"\nReport written to: {png}")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(f"Summary JSON written to: {json_path}")
    return summary


# ---------------------------------------------------------------------------
# ``eleven compare`` — multi-cloud / what-if / capacity sweep
# ---------------------------------------------------------------------------

def _fmt_value(kind: str, value: Any) -> str:
    """Format one metric value for the comparison table per its display kind."""
    if value is None:
        return "n/a"
    if kind == "frac":
        return f"{value * 100:.1f}%"
    if kind == "sec":
        return f"{value:.3f}s"
    if kind == "usd":
        return f"${value:.2f}"
    return str(value)


def _fmt_delta(kind: str, delta: Any) -> str:
    """Format a signed delta vs the baseline per its display kind."""
    if delta is None:
        return "n/a"
    sign = "+" if delta >= 0 else "-"
    magnitude = abs(float(delta))
    if kind == "frac":
        return f"{sign}{magnitude * 100:.1f} pts"
    if kind == "sec":
        return f"{sign}{magnitude:.3f}s"
    if kind == "usd":
        return f"{sign}${magnitude:.2f}"
    return f"{sign}{magnitude:g}"


def _format_comparison(diff: Dict[str, Any], mode: str, extra: Dict[str, Any]) -> str:
    """Render a comparison diff as a human-readable table (values + deltas)."""
    runs = diff["runs"]
    labels = [run["label"] for run in runs]
    width = max(14, *(len(label) for label in labels)) + 2

    lines = [
        f"Eleven comparison - mode: {mode}",
        "=" * 60,
        f"baseline: {diff['baseline']}",
        "",
        f"{'metric':<16}" + "".join(f"{label:>{width}}" for label in labels),
        "-" * (16 + width * len(labels)),
    ]
    for key, kind in compare.COMPARE_METRICS:
        row = f"{key:<16}"
        for run in runs:
            row += f"{_fmt_value(kind, run['values'].get(key)):>{width}}"
        lines.append(row)

    for run in runs:
        if run["label"] == diff["baseline"]:
            continue
        lines.append("")
        lines.append(f"deltas vs baseline ({run['label']}):")
        for key, kind in compare.COMPARE_METRICS:
            delta = run["deltas"].get(key)
            if delta is not None:
                lines.append(f"  {key:<16}{_fmt_delta(kind, delta)}")

    for run in runs:
        sizing = run.get("sizing") or {}
        if sizing:
            parts = "  ".join(f"{c}={s}" for c, s in sizing.items())
            lines.append(f"sizing ({run['label']}): {parts}")

    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _cmd_compare(args: argparse.Namespace) -> int:
    """Handler for the ``eleven compare <mode>`` subcommands.

    Modes: ``multi-cloud`` (provider re-calibration), ``what-if`` (patched
    params vs baseline), ``sweep`` (one parameter over a value range + knee).
    """
    config_path = args.topology
    if not Path(config_path).is_file():
        print(f"error: config file not found: {config_path}", file=sys.stderr)
        return 2
    try:
        config = load_config(config_path)
    except Exception as exc:  # validation errors, bad YAML, missing file...
        print(f"error: could not load config {config_path!r}: {exc}", file=sys.stderr)
        return 2

    mode = args.compare_mode
    metric = getattr(args, "metric", "p95_latency")
    knee: Optional[float] = None
    try:
        if mode == "multi-cloud":
            pairs = [(p, compare.apply_provider(config, p)) for p in compare.PROVIDERS]
            results = compare.run_many(pairs)
            diff = compare.diff_runs(results)
        elif mode == "what-if":
            patched = config
            for spec in args.set:
                path, sep, raw = spec.partition("=")
                if not sep or not path.strip():
                    print(f"error: --set expects PATH=VALUE, got {spec!r}", file=sys.stderr)
                    return 2
                patched = compare.set_path(patched, path.strip(), compare._coerce(raw))
            results = compare.run_many([("baseline", config), ("what-if", patched)])
            diff = compare.diff_runs(results)
        else:  # sweep
            values = [v.strip() for v in args.values.split(",") if v.strip()]
            if not values:
                print("error: --values needs at least one value", file=sys.stderr)
                return 2
            results = compare.sweep(config, args.param, values)
            knee = compare.knee_point(results, metric=metric, threshold=args.threshold)
            diff = compare.diff_runs(results)
    except Exception as exc:  # unknown provider/node, bad value, sim failure...
        print(f"error: comparison failed: {exc}", file=sys.stderr)
        return 1

    extra: Dict[str, Any] = {}
    if mode == "sweep":
        if knee is not None:
            extra["knee"] = (
                f"{metric} keeps paying off up to value {knee:g} "
                f"(>= {args.threshold:.0%} improvement per step); beyond that, "
                f"extra capacity buys little. Directional, not a benchmark."
            )
        else:
            extra["knee"] = "knee not found (fewer than two valid sweep points)"

    print(_format_comparison(diff, mode, extra))

    payload: Dict[str, Any] = {"mode": mode, "baseline": diff["baseline"], "runs": diff["runs"]}
    if mode == "sweep":
        payload["knee"] = knee
    with open(args.json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"\nComparison JSON written to: {args.json_path}")

    if args.png_path:
        png = render_comparison(
            results,
            output_path=args.png_path,
            kind=mode,
            knee=knee,
            metric=metric,
        )
        print(f"Comparison chart written to: {png}")
    return 0


def register_sim_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``run`` / ``demo`` / ``compare`` subcommands to the CLI parser."""
    run_p = subparsers.add_parser(
        "run",
        help="Run a simulation from a config file (YAML or JSON).",
    )
    run_p.add_argument(
        "config",
        help="Path to the simulation config file (.yaml/.yml/.json).",
    )
    run_p.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"Output path for the PNG report (default: {DEFAULT_REPORT}).",
    )
    run_p.add_argument(
        "--json",
        dest="json_path",
        default=DEFAULT_JSON,
        help=f"Output path for the JSON metrics summary (default: {DEFAULT_JSON}).",
    )

    subparsers.add_parser(
        "demo",
        help="Run the built-in demo topology (no config file needed).",
    )

    # -- compare: one engine, three faces ----------------------------------
    compare_p = subparsers.add_parser(
        "compare",
        help=(
            "Compare configs: multi-cloud calibration, what-if A/B, "
            "capacity sweep + knee."
        ),
    )
    compare_sub = compare_p.add_subparsers(dest="compare_mode", required=True)

    def _add_common_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--topology",
            required=True,
            help="Path to the simulation config file (.yaml/.yml/.json).",
        )
        p.add_argument(
            "--json",
            dest="json_path",
            default="comparison.json",
            help="Output path for the comparison JSON (default: comparison.json).",
        )
        p.add_argument(
            "--png",
            dest="png_path",
            default=None,
            help="Optional output path for a comparison chart PNG.",
        )

    multi_cloud_p = compare_sub.add_parser(
        "multi-cloud",
        help="Same architecture re-calibrated per provider (aws / azure / gcp).",
    )
    _add_common_flags(multi_cloud_p)

    what_if_p = compare_sub.add_parser(
        "what-if",
        help="Baseline vs patched parameters (--set PATH=VALUE, repeatable).",
    )
    _add_common_flags(what_if_p)
    what_if_p.add_argument(
        "--set",
        action="append",
        required=True,
        metavar="PATH=VALUE",
        help=(
            "Parameter patch, e.g. app_worker.max_capacity=20, "
            "traffic.base_rps=4, chaos.0.intensity=0.25. Repeatable."
        ),
    )

    sweep_p = compare_sub.add_parser(
        "sweep",
        help="Sweep one parameter over a value range and find the knee.",
    )
    _add_common_flags(sweep_p)
    sweep_p.add_argument(
        "--param",
        required=True,
        metavar="NODE.FIELD",
        help="Parameter to sweep, e.g. app_worker.max_capacity.",
    )
    sweep_p.add_argument(
        "--values",
        required=True,
        help="Comma-separated values, e.g. 1,2,4,8,16.",
    )
    sweep_p.add_argument(
        "--metric",
        default="p95_latency",
        help="Metric to locate the knee on (default: p95_latency).",
    )
    sweep_p.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Minimum relative improvement per step to count (default: 0.05).",
    )
