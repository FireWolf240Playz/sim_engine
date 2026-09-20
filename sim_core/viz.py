"""Matplotlib reporting.

Renders a three-panel report - per-request latency with P50/P95/P99 lines,
cumulative completion & SLA compliance, and per-component utilisation over
time - and always saves it to a PNG. ``show=True`` additionally pops a GUI
window; otherwise the Agg backend is used so it works headless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Union

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
