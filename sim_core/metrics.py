"""Typed metric collection and pandas aggregation.

Every percentile/average is computed over the full list of per-request samples
accumulated across the whole run - never from a single data point.
"""

from __future__ import annotations

import copy
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
    #: This request's own timeout retries. The collector's ``retry_counts``
    #: say which node was retried; this says which request paid for it.
    retries: int = 0
    #: The node whose slot the request never got, when that is why it failed.
    failed_at: Optional[str] = None
    #: Still in the system, past its budget, when the run stopped: ``end`` is
    #: the horizon, not a finish time (see ``settle_unfinished``).
    overdue: bool = False

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
        edge_count: int = 0,
    ) -> None:
        self._topology = topology
        #: SLA target (seconds) from the config, surfaced in ``summary()`` so
        #: the p95-vs-SLA headroom term of the resilience score can be
        #: computed from the summary dict alone (see :mod:`sim_core.score`).
        self._sla_target = sla_target
        self.requests: List[RequestRecord] = []
        self.utilization: List[UtilizationSample] = []
        self.retry_counts: Dict[str, int] = {}
        #: Per component, exact (counted at every acquisition, not sampled):
        #: how many acquisitions had to queue, and the most slots ever held
        #: after a grant that did not queue. A node nobody waited for replays
        #: the same events at any larger size, which is what lets
        #: :mod:`sim_core.rightsize` skip runs that cannot differ.
        self.wait_counts: dict[str, int] = {}
        self.peak_in_use: dict[str, int] = {}
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
        #: Arrival time of every request that has arrived and not finished
        #: yet, in arrival order. Whatever is left when the run stops is
        #: settled by :meth:`settle_unfinished`, so no arrival goes missing.
        self._open: dict[str, float] = {}
        #: Requests still in the system at the horizon, and how many of them
        #: were already past their budget (those are recorded as failed).
        self.unfinished: int = 0
        self.overdue: int = 0
        #: The ticks, built once at each sample (see :meth:`sample_utilization`)
        #: and returned by :meth:`timeseries`: what a watcher was sent and what
        #: the finished run reports are the same objects' copies.
        self._ticks: list[dict[str, Any]] = []
        #: Where the previous tick's window ended: an index into ``requests``,
        #: the arrivals so far, and each node's retries so far.
        self._tick_cursor = 0
        self._arrivals = 0
        self._arrivals_at_tick = 0
        self._retries_at_tick: dict[str, int] = {}
        #: Requests that set off along ``config.topology.edges[i]`` since the
        #: previous tick.
        self._edge_flow: list[int] = [0] * edge_count

    # -- recording ---------------------------------------------------------
    def record_arrival(self, request_id: str, start: float) -> None:
        """Note that a request entered the system at ``start``."""
        self._open[request_id] = start
        self._arrivals += 1

    def record_edge(self, index: int) -> None:
        """Count one request setting off along edge ``index`` of the config."""
        self._edge_flow[index] += 1

    def record_request(self, record: RequestRecord) -> None:
        self._open.pop(record.request_id, None)
        self.requests.append(record)

    def settle_unfinished(self, end_time: float) -> None:
        """Account for the requests still in the system when the run stops.

        A run that only counts finished requests rewards an outage: the
        requests stuck behind a dead node never finish, so they never show up
        as failures, and completion rate and latency improve the longer the
        node stays down. Every unfinished request is therefore counted
        (``unfinished``), and one that has already waited past its budget is
        recorded as a failed, SLA-missing request whose latency is its age at
        the horizon - a lower bound on the latency it would have had.

        The budget is the SLA target. With no SLA target it is the latency of
        the slowest request that did finish (zero when none did). A request
        younger than the budget is merely in flight, as some always are when
        a healthy run stops, and stays out of the record: its outcome is not
        known and is not guessed.
        """
        self.unfinished = len(self._open)
        budget = self._sla_target
        if budget is None:
            budget = max((r.latency for r in self.requests), default=0.0)
        late = [(rid, start) for rid, start in self._open.items() if end_time - start > budget]
        self._open.clear()
        self.overdue = len(late)
        for request_id, start in late:
            self.requests.append(
                RequestRecord(
                    request_id=request_id,
                    start=start,
                    end=end_time,
                    success=False,
                    sla_met=False,
                    cache_hit=None,
                    overdue=True,
                )
            )

    def request_log(self) -> list[dict[str, Any]]:
        """One plain row per recorded request, in arrival order.

        ``summary()`` answers "how did the run go"; this answers "which
        requests, and why", which is what a per-request export needs. The
        collector records at completion, so the rows are sorted back into
        arrival order (stable: equal arrivals keep their recorded order) and
        the records themselves are left as they were.

        ``wait_time`` is everything that was not service: queueing for a slot
        and retry backoff. An overdue request was never served to the end, so
        its whole age at the horizon is wait.

        Every value is a plain Python type, so a row serialises as strict
        JSON and :mod:`sim_core.exports` never sees a dataclass or a numpy
        scalar.
        """
        rows: list[dict[str, Any]] = []
        for record in sorted(self.requests, key=lambda r: r.start):
            start = float(record.start)
            end = float(record.end)
            latency = end - start
            service_time = float(sum(record.component_times.values()))
            if record.overdue:
                outcome = "overdue"
            elif record.success:
                outcome = "completed"
            else:
                outcome = "failed"
            rows.append(
                {
                    "request_id": str(record.request_id),
                    "t": start,
                    "end": end,
                    "latency": latency,
                    "service_time": service_time,
                    "wait_time": max(0.0, latency - service_time),
                    "outcome": outcome,
                    "sla_met": bool(record.sla_met),
                    "retries": int(record.retries),
                    "cache_hit": None if record.cache_hit is None else bool(record.cache_hit),
                    "failed_at": record.failed_at,
                    "path": list(record.component_times),
                }
            )
        return rows

    def record_retry(self, component_name: str) -> None:
        """Count one timeout retry against *component_name*."""
        self.retry_counts[component_name] = self.retry_counts.get(component_name, 0) + 1

    def record_wait(self, component_name: str) -> None:
        """Count one slot acquisition that found no free slot and queued."""
        self.wait_counts[component_name] = self.wait_counts.get(component_name, 0) + 1

    def record_in_use(self, component_name: str, in_use: int) -> None:
        """Note the slots held right after one was granted without a wait."""
        if in_use > self.peak_in_use.get(component_name, 0):
            self.peak_in_use[component_name] = in_use

    @property
    def total_retries(self) -> int:
        """Total timeout retries across all components this run."""
        return sum(self.retry_counts.values())

    def sample_utilization(self, env_now: float) -> dict[str, Any]:
        """Take one sample and build its tick; returns a copy of the tick.

        The tick's counts cover the window since the previous sample:
        requests are recorded at completion, in event order, so the records
        appended since then are exactly the ones that ended inside it.
        """
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

        window = self.requests[self._tick_cursor :]
        self._tick_cursor = len(self.requests)
        tick: dict[str, Any] = {
            "time": float(env_now),
            "p50_latency": None,
            "p95_latency": None,
            "sla_met": None,
            "arrived": self._arrivals - self._arrivals_at_tick,
            "completed": sum(1 for record in window if record.success),
            "failed": sum(1 for record in window if not record.success),
            "per_component": {
                name: {
                    "utilization": metrics["utilization"],
                    "queue_length": metrics["queue_length"],
                    "capacity": int(metrics["capacity"]),
                    "in_use": int(metrics["in_use"]),
                    "retries": self.retry_counts.get(name, 0)
                    - self._retries_at_tick.get(name, 0),
                }
                for name, metrics in per.items()
            },
            "edge_flow": list(self._edge_flow),
        }
        if window:
            # No percentile from nothing: a window with no completed request
            # keeps None so a chart gaps honestly instead of drawing a zero.
            latencies = np.array([record.latency for record in window], dtype=float)
            tick["p50_latency"] = float(np.percentile(latencies, 50))
            tick["p95_latency"] = float(np.percentile(latencies, 95))
            tick["sla_met"] = sum(1 for record in window if record.sla_met) / len(window)

        self._arrivals_at_tick = self._arrivals
        self._retries_at_tick = dict(self.retry_counts)
        self._edge_flow = [0] * len(self._edge_flow)
        self._ticks.append(tick)
        return copy.deepcopy(tick)

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
            # The configured size, not the live one: a node that chaos took
            # down is still on the bill, so an outage is never a discount.
            provisioned = comp.config.max_capacity
            billed_slots = provisioned + comp.in_use + comp.queue_length
            self.cost_by_component[comp.name] = (
                self.cost_by_component.get(comp.name, 0.0)
                + rate * billed_slots * (dt / 3600.0)  # rate is $/hour, dt is in seconds
            )
            # Base (provisioned) part: the slots you sized, billed whether or
            # not they are busy. Metered part = total - base, implicitly.
            self.cost_base_by_component[comp.name] = (
                self.cost_base_by_component.get(comp.name, 0.0)
                + rate * provisioned * (dt / 3600.0)
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
            in_use = np.array([s.per_component[name]["in_use"] for s in self.utilization])
            # The slots held at each sample, not utilisation x capacity:
            # chaos changes the capacity mid-run, the samples kept the truth.
            demand = in_use + queue
            mean_utilization = float(np.mean(util))
            p95_utilization = float(np.percentile(util, 95))
            p95_queue = float(np.percentile(queue, 95))
            self.sizing[name] = {
                "mean_utilization": mean_utilization,
                "p95_utilization": p95_utilization,
                "p95_queue": p95_queue,
                "recommended_capacity": max(1, int(np.ceil(np.percentile(demand, 99)))),
                "status": self._verdict(mean_utilization),
                "waited_requests": self.wait_counts.get(name, 0),
                "peak_in_use": self.peak_in_use.get(name, 0),
            }

    def summary(self) -> Dict[str, Any]:
        """Headline metrics over every recorded request.

        A recorded request is one that finished (completed or failed) or was
        overdue at the horizon (:meth:`settle_unfinished`); overdue requests
        count as failed. ``unfinished_requests`` is everything still in the
        system when the run stopped and ``overdue_requests`` the part of it
        that was recorded, so ``requests + unfinished_requests -
        overdue_requests`` is the number of arrivals.

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

        result["unfinished_requests"] = self.unfinished
        result["overdue_requests"] = self.overdue

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
        """Per-tick timeline for the frontend (pure, no simulation).

        One entry per :meth:`sample_utilization` tick, in time order: copies
        of the ticks built as the run went, the same ones a watcher was sent
        (roadmap 3.1). Latency percentiles cover the requests that completed
        inside the tick's window, and a window with none carries ``None``.

        Shape::

            {
              "time": 4.0,
              "p50_latency": 1.8, "p95_latency": 3.1, "sla_met": 0.9,
              "arrived": 9, "completed": 7, "failed": 1,
              "per_component": {"worker": {
                  "utilization": 0.7, "queue_length": 2.0,
                  "capacity": 6, "in_use": 4, "retries": 0}},
              "edge_flow": [9, 9, 4],
            }

        ``arrived`` / ``completed`` / ``failed`` and each node's ``retries``
        count the window that ends at the tick; ``capacity`` and ``in_use``
        are the node's live slots at the tick (lower during an outage);
        ``edge_flow[i]`` counts the requests that set off along
        ``config.topology.edges[i]`` in the window.
        """
        return copy.deepcopy(self._ticks)

    def requests_df(self) -> pd.DataFrame:
        """One row per recorded request, with per-component latency breakdowns."""
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
