"""Typed metric collection and pandas aggregation.

Every percentile/average is computed over the full list of per-request samples
accumulated across the whole run - never from a single data point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from sim_core.findings import build_findings, verdict_headline
from sim_core.score import (
    cost_extrapolation,
    cost_grade,
    cost_per_completed_request,
    resilience_score,
    score_explanation,
)
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

    def __init__(
        self,
        topology: Topology,
        sla_target: Optional[float] = None,
    ) -> None:
        self._topology = topology
        #: SLA target (seconds) from the config, surfaced in ``summary()`` so
        #: the p95-vs-SLA headroom term of the resilience score can be
        #: computed from the summary dict alone (see :mod:`sim_core.score`).
        self._sla_target = sla_target
        self.requests: List[RequestRecord] = []
        self.utilization: List[UtilizationSample] = []
        self.retry_counts: Dict[str, int] = {}
        self.cost_by_component: Dict[str, float] = {}
        #: Provisioned (traffic-independent) portion of the total bill, per
        #: component: ``cost_per_hour * capacity * dt_hours``. The remainder
        #: (total - base) is the *metered* part billed on ``in_use + queue``,
        #: i.e. the load/chaos-driven cost. Split for the steady-state vs
        #: under-load headline in ``summary()``.
        self.cost_base_by_component: Dict[str, float] = {}
        self.sizing: Dict[str, Dict[str, Any]] = {}
        self._last_sample_time: Optional[float] = None
        #: Run horizon captured by :meth:`settle_cost`; used by
        #: :func:`sim_core.score.cost_extrapolation` for the steady-state
        #: hour/month/year projection in ``summary()``.
        self._run_duration: Optional[float] = None

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
        self._accumulate_cost(env_now)
        per: Dict[str, Dict[str, float]] = {}
        for comp in self._topology.components:
            per[comp.name] = {
                "queue_length": float(comp.queue_length),
                "in_use": float(comp.in_use),
                "capacity": float(comp.capacity),
                "utilization": comp.utilization,
            }
        self.utilization.append(UtilizationSample(time=env_now, per_component=per))

    # -- sizing analysis ----------------------------------------------------
    @staticmethod
    def _verdict(mean_utilization: float) -> str:
        """Classify one component's sizing from its mean utilisation.

        Mean utilisation is the verdict driver: in steady state it equals
        offered load per provisioned slot (Little's law, independent of the
        service-time distribution) - the classic capacity-planning number and
        the one provider dashboards report. Deliberately coarse - a
        directional FinOps signal, not a benchmark. (Tail statistics like the
        p95 of point-in-time utilisation or queue length are deliberately
        *not* used for the verdict: even a healthy queueing system briefly
        saturates and queues often enough to sit in its own p95 tail, so a
        tail-based verdict misflags well-provisioned components.)
        """
        if mean_utilization >= 0.85:
            return "undersized"
        if mean_utilization <= 0.5:
            return "oversized"
        return "right_sized"

    def _accumulate_cost(self, env_now: float) -> None:
        """Add the cost accrued since the previous sample (called every tick).

        Two-part billing, mirroring how real services price (instance-hour
        base + metered usage): each component accrues
        ``cost_per_hour * (max_capacity + in_use + queue_length) * dt_hours``,
        where ``dt_hours = dt / 3600`` because ``cost_per_hour`` is priced per
        *hour* while simulation time is in *seconds*.
        The ``max_capacity`` term is the *provisioned* base - you pay for the
        slots you sized whether they are busy or not (oversizing therefore
        shows up as waste); the ``in_use + queue`` term meters the active
        workload, so a chaotic run that saturates and queues costs more than
        a healthy one. Components with ``cost_per_hour == 0`` contribute
        nothing.
        """
        if self._last_sample_time is None:
            self._last_sample_time = env_now
            if env_now > 0.0:
                self._run_duration = env_now
            return
        dt = env_now - self._last_sample_time
        if dt <= 0.0:
            return
        if env_now > 0.0:
            self._run_duration = max(self._run_duration or 0.0, env_now)
        for comp in self._topology.components:
            rate = comp.config.cost_per_hour
            if rate <= 0.0:
                continue
            billed_slots = comp.capacity + comp.in_use + comp.queue_length
            self.cost_by_component[comp.name] = (
                self.cost_by_component.get(comp.name, 0.0)
                + rate * billed_slots * (dt / 3600.0)  # rate is $/hour, dt is in seconds
            )
            # Base (provisioned) part: the slots you sized, billed whether or
            # not they are busy. Metered part = total - base, implicitly.
            self.cost_base_by_component[comp.name] = (
                self.cost_base_by_component.get(comp.name, 0.0)
                + rate * comp.capacity * (dt / 3600.0)
            )
        self._last_sample_time = env_now

    def settle_cost(self, end_time: float) -> None:
        """Accrue cost for the final interval after the last periodic sample.

        ``env.run(until=D)`` can stop before the sampler's tick at t=D is
        processed, which would silently drop the last ``metrics_interval`` of
        billing; calling this once at the horizon closes that gap. No-op when
        nothing is pending (no prior sample, or zero/negative dt).
        """
        self._accumulate_cost(end_time)

    # -- aggregation -------------------------------------------------------
    def _compute_sizing(self) -> None:
        """Fill ``self.sizing`` from the accumulated utilisation samples.

        Per component: mean utilisation (the verdict driver - in steady state
        it equals offered load per slot), p95 utilisation and p95 queue
        (context for the report), and ``recommended_capacity`` = ceil(p99 of
        concurrent demand ``in_use + queue``) - "size to your p99 demand",
        the classic right-sizing number (most meaningful on longer runs with
        many samples). Always computed, cost-independent, so the report can
        advise on sizing even for a run with no rates configured.
        """
        self.sizing.clear()
        if not self.utilization:
            return
        for comp in self._topology.components:
            name = comp.name
            util = np.array([s.per_component[name]["utilization"] for s in self.utilization])
            queue = np.array([s.per_component[name]["queue_length"] for s in self.utilization])
            demand = util * comp.capacity + queue  # == in_use + queue per sample
            mean_utilization = float(np.mean(util))
            p95_utilization = float(np.percentile(util, 95))
            p95_queue = float(np.percentile(queue, 95))
            self.sizing[name] = {
                "mean_utilization": mean_utilization,
                "p95_utilization": p95_utilization,
                "p95_queue": p95_queue,
                "recommended_capacity": max(1, int(np.ceil(np.percentile(demand, 99)))),
                "status": self._verdict(mean_utilization),
            }

    def summary(self) -> Dict[str, Any]:
        """Headline metrics over all completed requests.

        Beyond the raw metrics, the summary carries the Phase 2 scoring
        block (see :mod:`sim_core.score` for the documented formulas):

        - ``sla_target`` — the configured SLA (context for the headroom term
          and for consumers of this dict).
        - ``cost_per_completed_request`` — unit cost of a successful request.
        - ``resilience_score`` — deterministic 0..100 resilience number.
        - ``cost_grade`` — 'A'..'F' right-sizing / cost-efficiency grade.
        - ``cost_extrapolation`` — ``{hour, month, year}`` steady-state
          projection ("same load sustained 24/7"), or ``None`` when the run
          duration is unknown or no rates are configured.

        All of it is derived purely from the metrics accumulated by this
        collector — no extra simulation.
        """
        self._compute_sizing()
        total_cost = sum(self.cost_by_component.values())

        if not self.requests:
            result: Dict[str, Any] = {
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
                "total_cost": total_cost,
                "cost_breakdown_by_component": dict(self.cost_by_component),
                "component_sizing": dict(self.sizing),
            }
        else:
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
            result = {
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
                "total_cost": total_cost,
                "cost_breakdown_by_component": dict(self.cost_by_component),
                "component_sizing": dict(self.sizing),
            }

        # -- Phase 2 scoring block (pure functions, deterministic) ---------
        result["sla_target"] = self._sla_target
        result["cost_per_completed_request"] = cost_per_completed_request(result)
        result["resilience_score"] = resilience_score(result)
        result["cost_grade"] = cost_grade(result)
        # Roadmap 1.2: the plain-English "why this score" verdict. Pure
        # function of the dict above (see :mod:`sim_core.findings`), so it
        # is deterministic and rides along into CLI, API, and profile runs.
        result["findings"] = build_findings(result)
        # The score's own arithmetic in plain terms (same dict, same words) —
        # powers the "how the score is built" strip and the full report.
        result["score_explanation"] = score_explanation(result)
        # One-sentence overall verdict (breaks / bends / holds), derived
        # from the findings above, so it can never disagree with them.
        result["verdict_headline"] = verdict_headline(result, result["findings"])

        # Cost split: provisioned base (traffic-independent) vs metered
        # (in_use + queue, load/chaos-driven). The two always sum to total.
        cost_base = sum(self.cost_base_by_component.values())
        result["cost_base"] = cost_base
        result["cost_metered"] = max(0.0, total_cost - cost_base)

        duration = self._run_duration
        if duration is None and self.utilization:
            duration = max(sample.time for sample in self.utilization)
        result["cost_extrapolation"] = (
            cost_extrapolation(total_cost, duration) if duration and duration > 0.0 else None
        )
        #: "What the same bill looks like with none of the load/chaos on top"
        #: - the pure provisioned base, projected the same way (24/7 label
        #: applies equally). None when there is no base to project.
        result["steady_state_cost_extrapolation"] = (
            cost_extrapolation(cost_base, duration)
            if duration and duration > 0.0 and cost_base > 0.0
            else None
        )
        return result

    def timeseries(self) -> List[Dict[str, Any]]:
        """Per-tick timeline for the frontend (pure aggregation, no simulation).

        One entry per :meth:`sample_utilization` tick, in time order.
        Latency percentiles are computed over the requests that *completed
        inside that tick's window* — requests are recorded at completion in
        event order, so their end-times are non-decreasing and a running
        pointer is exact. Windows with no completed request carry ``None``
        percentiles so a chart can gap honestly instead of drawing a fake
        zero (mirrors the :mod:`sim_core.metrics` no-single-point rule).

        Shape::

            {
              "time": 4.0,
              "p50_latency": 1.8, "p95_latency": 3.1, "sla_met": 0.9,
              "per_component": {"worker": {"utilization": 0.7, "queue_length": 2.0}},
            }
        """
        ticks: List[Dict[str, Any]] = []
        cursor = 0
        for sample in self.utilization:
            window: List[RequestRecord] = []
            while cursor < len(self.requests) and self.requests[cursor].end <= sample.time:
                window.append(self.requests[cursor])
                cursor += 1
            entry: Dict[str, Any] = {
                "time": sample.time,
                "p50_latency": None,
                "p95_latency": None,
                "sla_met": None,
                "per_component": {
                    name: {
                        "utilization": metrics["utilization"],
                        "queue_length": metrics["queue_length"],
                    }
                    for name, metrics in sample.per_component.items()
                },
            }
            if window:
                latencies = np.array([record.latency for record in window], dtype=float)
                entry["p50_latency"] = float(np.percentile(latencies, 50))
                entry["p95_latency"] = float(np.percentile(latencies, 95))
                entry["sla_met"] = sum(1 for record in window if record.sla_met) / len(window)
            ticks.append(entry)
        return ticks

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
