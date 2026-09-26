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
    render_report,
)

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


def register_sim_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``run`` and ``demo`` subcommands to the CLI parser."""
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
