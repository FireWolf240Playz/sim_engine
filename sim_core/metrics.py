"""Typed metric collection and pandas aggregation.

Every percentile/average is computed over the full list of per-request samples
accumulated across the whole run - never from a single data point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from sim_core.topology import Topology


@dataclass
class RequestRecord:
    """Outcome of a single request traversing the topology."""

    request_id: str
    start: float
    end: float
    success: bool
    sla_met: bool
    cache_hit: Optional[bool]
    component_times: Dict[str, float] = field(default_factory=dict)

    @property
    def latency(self) -> float:
        return self.end - self.start


@dataclass
class UtilizationSample:
    """A periodic snapshot of queue depth / utilisation per component."""

    time: float
    per_component: Dict[str, Dict[str, float]]


class MetricsCollector:
    """Accumulates request outcomes and periodic utilisation samples."""

    def __init__(self, topology: Topology) -> None:
        self._topology = topology
        self.requests: List[RequestRecord] = []
        self.utilization: List[UtilizationSample] = []
        self.retry_counts: Dict[str, int] = {}

    # -- recording ---------------------------------------------------------
    def record_request(self, record: RequestRecord) -> None:
        self.requests.append(record)

    def record_retry(self, component_name: str) -> None:
        """Count one timeout retry against *component_name*."""
        self.retry_counts[component_name] = self.retry_counts.get(component_name, 0) + 1

    @property
    def total_retries(self) -> int:
        """Total timeout retries across all components this run."""
        return sum(self.retry_counts.values())

    def sample_utilization(self, env_now: float) -> None:
        per: Dict[str, Dict[str, float]] = {}
        for comp in self._topology.components:
            per[comp.name] = {
                "queue_length": float(comp.queue_length),
                "in_use": float(comp.in_use),
                "capacity": float(comp.capacity),
                "utilization": comp.utilization,
            }
        self.utilization.append(UtilizationSample(time=env_now, per_component=per))

    # -- aggregation -------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        """Headline metrics over all completed requests."""
        if not self.requests:
            return {
                "requests": 0,
                "completion_rate": None,
                "failed_requests": 0,
                "sla_compliance": None,
                "avg_latency": None,
                "p50_latency": None,
                "p95_latency": None,
                "p99_latency": None,
                "cache_hit_rate": None,
                "total_retries": 0,
            }

        latencies = np.array([r.latency for r in self.requests], dtype=float)
        n = len(self.requests)
        completed = sum(1 for r in self.requests if r.success)
        sla_ok = sum(1 for r in self.requests if r.sla_met)
        cache_requests = [r for r in self.requests if r.cache_hit is not None]
        cache_hit_rate = (
            sum(1 for r in cache_requests if r.cache_hit) / len(cache_requests)
            if cache_requests
            else None
        )

        return {
            "requests": n,
            "completion_rate": completed / n,
            "failed_requests": n - completed,
            "sla_compliance": sla_ok / n,
            "avg_latency": float(np.mean(latencies)),
            "p50_latency": float(np.percentile(latencies, 50)),
            "p95_latency": float(np.percentile(latencies, 95)),
            "p99_latency": float(np.percentile(latencies, 99)),
            "cache_hit_rate": cache_hit_rate,
            "total_retries": self.total_retries,
        }

    def requests_df(self) -> pd.DataFrame:
        """One row per completed request, with per-component latency breakdowns."""
        rows: List[Dict[str, Any]] = []
        for r in self.requests:
            row: Dict[str, Any] = {
                "request_id": r.request_id,
                "start": r.start,
                "end": r.end,
                "latency": r.latency,
                "success": r.success,
                "sla_met": r.sla_met,
                "cache_hit": r.cache_hit,
            }
            for name, t in r.component_times.items():
                row[f"latency_{name}"] = t
            rows.append(row)
        return pd.DataFrame(rows)

    def utilization_df(self) -> pd.DataFrame:
        """Long-format utilisation samples (one row per component per tick)."""
        rows: List[Dict[str, Any]] = []
        for s in self.utilization:
            for name, m in s.per_component.items():
                rows.append(
                    {
                        "time": s.time,
                        "component": name,
                        "queue_length": m["queue_length"],
                        "in_use": m["in_use"],
                        "capacity": m["capacity"],
                        "utilization": m["utilization"],
                    }
                )
        return pd.DataFrame(rows)
