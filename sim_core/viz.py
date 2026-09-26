"""Matplotlib reporting.

Renders a three-panel report - per-request latency with P50/P95/P99 lines,
cumulative completion & SLA compliance, and per-component utilisation over
time - and always saves it to a PNG. ``show=True`` additionally pops a GUI
window; otherwise the Agg backend is used so it works headless.

Also hosts :func:`render_comparison` for the compare-runs engine
(:mod:`sim_core.compare`): grouped bars for multi-cloud / what-if, and a
line chart with the knee marked for capacity sweeps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

import numpy as np

from sim_core.metrics import MetricsCollector


def render_report(
    collector: MetricsCollector,
    output_path: Union[str, Path] = "eleven_report.png",
    show: bool = False,
) -> Path:
    """Build and save the resilience report. Returns the output path."""
    import matplotlib

    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary: Dict[str, Any] = collector.summary()
    if summary["requests"] == 0:
        raise ValueError("No requests were recorded; nothing to plot.")

    requests = collector.requests
    latencies = np.array([r.latency for r in requests], dtype=float)
    x = np.arange(len(requests))

    fig, (ax_latency, ax_sla, ax_util) = plt.subplots(3, 1, figsize=(12, 12))

    # -- Panel 1: latency per request + percentiles -----------------------
    ax_latency.plot(x, latencies, lw=0.6, alpha=0.7, color="#2b6cb0", label="Latency (s)")
    for pct, color, label in (
        (50, "#e53e3e", "P50"),
        (95, "#d69e2e", "P95"),
        (99, "#805ad5", "P99"),
    ):
        value = float(np.percentile(latencies, pct))
        ax_latency.axhline(value, color=color, linestyle="--", alpha=0.8, label=f"{label}: {value:.3f}s")
    ax_latency.set_title("Per-request end-to-end latency")
    ax_latency.set_xlabel("Request (in completion order)")
    ax_latency.set_ylabel("Latency (s)")
    ax_latency.legend(loc="upper left", fontsize=8)
    ax_latency.grid(alpha=0.25)

    # -- Panel 2: cumulative completion & SLA compliance -------------------
    success = np.array([r.success for r in requests], dtype=float)
    sla_met = np.array([r.sla_met for r in requests], dtype=float)
    ax_sla.plot(x, np.cumsum(success) / (x + 1), color="#2f855a", label="Completion rate")
    ax_sla.plot(x, np.cumsum(sla_met) / (x + 1), color="#b7791f", label="SLA compliance")
    ax_sla.set_title("Cumulative completion & SLA compliance")
    ax_sla.set_xlabel("Request (in completion order)")
    ax_sla.set_ylabel("Fraction")
    ax_sla.set_ylim(0.0, 1.05)
    ax_sla.legend(loc="lower left", fontsize=8)
    ax_sla.grid(alpha=0.25)

    # -- Panel 3: per-component utilisation over time ----------------------
    util_df = collector.utilization_df()
    if not util_df.empty:
        for component, group in util_df.groupby("component"):
            group = group.sort_values("time")
            ax_util.plot(group["time"], group["utilization"], lw=1.4, label=component)
        ax_util.set_title("Component utilisation over time")
        ax_util.set_xlabel("Simulation time (s)")
        ax_util.set_ylabel("Utilisation (0-1)")
        ax_util.set_ylim(0.0, 1.1)
        ax_util.legend(loc="upper left", fontsize=8)
    else:
        ax_util.text(0.5, 0.5, "No utilisation samples", ha="center", va="center")
    ax_util.grid(alpha=0.25)

    sla_fraction = summary["sla_compliance"]
    sla_text = "n/a" if sla_fraction is None else f"{sla_fraction:.1%}"
    fig.suptitle(
        f"Eleven resilience report - {summary['requests']} requests, SLA {sla_text}",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    else:
        plt.close(fig)
    return out


_BAR_COLORS = ("#2b6cb0", "#dd6b20", "#38a169", "#805ad5", "#d53f8c", "#b7791f")


def render_comparison(
    results: Sequence[Any],
    output_path: Union[str, Path] = "comparison.png",
    kind: str = "bars",
    knee: Optional[float] = None,
    metric: str = "p95_latency",
) -> Path:
    """Render a compare-runs chart and save it to a PNG. Returns the path.

    ``kind="sweep"`` draws the swept metric (plus cost) as a line chart with
    the knee marked; any other kind (``multi-cloud`` / ``what-if``) draws
    grouped bars for completion rate, the swept metric, and cost. Results
    are :class:`sim_core.compare.RunResult` objects (duck-typed: need
    ``label``, ``summary``, ``parameter_value``).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if kind == "sweep":
        ordered = sorted(
            (r for r in results if r.parameter_value is not None),
            key=lambda r: r.parameter_value,
        )
        if len(ordered) < 2:
            raise ValueError("sweep comparison needs at least two parameter values")
        xs = [r.parameter_value for r in ordered]
        ys = [r.summary.get(metric) for r in ordered]
        costs = [r.summary.get("total_cost") for r in ordered]

        fig, (ax, ax_cost) = plt.subplots(2, 1, figsize=(11, 9), sharex=True)
        ax.plot(xs, [v if v is not None else np.nan for v in ys], marker="o", color="#2b6cb0")
        if knee is not None:
            ax.axvline(knee, color="#e53e3e", linestyle="--", alpha=0.8)
            ax.annotate(
                f"knee = {knee:g}",
                xy=(knee, ax.get_ylim()[1] * 0.98),
                xytext=(10, 0),
                textcoords="offset points",
                color="#e53e3e",
                fontsize=9,
            )
        ax.set_title(f"Capacity sweep - {metric} vs capacity value")
        ax.set_ylabel(metric)
        ax.grid(alpha=0.25)

        if all(c is not None for c in costs):
            ax_cost.bar(xs, costs, width=0.8, color="#2f855a")
            ax_cost.set_title("Estimated cost vs capacity value")
            ax_cost.set_ylabel("USD")
            ax_cost.grid(alpha=0.25, axis="y")
        ax_cost.set_xlabel("capacity value")
    else:
        labels = [r.label for r in results]
        panels = (
            ("completion_rate", "Completion rate (0-1)"),
            (metric, f"{metric} (s)"),
            ("total_cost", "Estimated cost (USD)"),
        )
        fig, axes = plt.subplots(3, 1, figsize=(11, 10))
        for ax, (key, title) in zip(axes, panels):
            values = [r.summary.get(key) for r in results]
            if all(v is None for v in values):
                ax.axis("off")
                continue
            xs = list(range(len(results)))
            ax.bar(xs, [v if v is not None else 0 for v in values], color=_BAR_COLORS[: len(results)])
            ax.set_xticks(xs)
            ax.set_xticklabels(labels, rotation=15, ha="right")
            ax.set_title(title)
            ax.grid(alpha=0.25, axis="y")
            for x, v in zip(xs, values):
                if v is not None:
                    ax.text(x, v, f"{v:.3f}", ha="center", va="bottom", fontsize=8)

    fig.suptitle("Eleven comparison", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out
