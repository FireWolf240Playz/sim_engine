"""Every arrival is accounted for, and a failed request never meets the SLA.

Two defects made an outage look healthy, measured on the fast stack below
(50 rps, 60 s, SLA 1 s, no timeouts) before these tests existed:

- A request still queued when the run stopped was never recorded. With the
  database dead from t=1 s the run reported 30 requests, 100% completion and
  a resilience score of 100; 2903 arrivals had vanished.
- ``sla_met`` looked at latency alone. A 1-slot worker with a 0.2 s timeout
  failed 99.7% of its requests and reported 99.8% SLA compliance, because a
  fast failure is fast.

The rule now: a request still in the system at the horizon that has already
waited past its budget is recorded as failed. The budget is the SLA target,
or, with no SLA target, the latency of the slowest request that finished.
"""

from __future__ import annotations

from typing import Any

from sim_core import CloudSimulator, SimulationConfig
from sim_core.metrics import MetricsCollector, RequestRecord

DURATION = 60.0


def _stack(
    *,
    sla_target: float | None = 1.0,
    worker: dict[str, Any] | None = None,
    chaos: list[dict[str, Any]] | None = None,
) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": DURATION,
            "sla_target": sla_target,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", "max_capacity": 100,
                     "service_time": 0.005},
                    {"name": "worker", "role": "worker", "max_capacity": 10,
                     "service_time": 0.05, **(worker or {})},
                    {"name": "db", "role": "database", "max_capacity": 10,
                     "service_time": 0.05},
                ],
                "edges": [
                    {"source": "lb", "target": "worker"},
                    {"source": "worker", "target": "db"},
                ],
            },
            "traffic": {"base_rps": 50.0, "duration": DURATION},
            "chaos": chaos or [],
        }
    )


def _db_dead_from(start: float) -> list[dict[str, Any]]:
    return [
        {"event_type": "component_failure", "target": "db", "intensity": 1.0,
         "start_time": start, "duration": 1000.0, "interval": 1000.0}
    ]


def _run(config: SimulationConfig) -> tuple[CloudSimulator, dict[str, Any]]:
    simulator = CloudSimulator(config)
    return simulator, simulator.run(suggest=False)


def _severities(summary: dict[str, Any]) -> set[str]:
    return {f"{f['severity']}:{f['id']}" for f in summary["findings"]}


# ---------------------------------------------------------------------------
# requests still in the system at the horizon
# ---------------------------------------------------------------------------

def test_a_database_dead_to_the_horizon_is_not_healthy() -> None:
    _, summary = _run(_stack(chaos=_db_dead_from(20.0)))
    assert summary["completion_rate"] < 0.5
    assert summary["sla_compliance"] < 0.5
    assert summary["resilience_score"] < 50
    assert "crit:sla_breach" in _severities(summary)
    assert summary["overdue_requests"] > 1000


def test_a_longer_outage_never_scores_better() -> None:
    scores = [
        _run(_stack(chaos=_db_dead_from(start)))[1]["resilience_score"]
        for start in (50.0, 40.0, 20.0, 1.0)
    ]
    assert scores == sorted(scores, reverse=True)
    assert scores[-1] < 5


def test_every_arrival_is_accounted_for() -> None:
    """No timeouts here, so a request in the system holds a slot or a queue
    place: what the nodes hold at the horizon is exactly what is unfinished."""
    simulator, summary = _run(_stack(chaos=_db_dead_from(40.0)))
    in_system = sum(c.in_use + c.queue_length for c in simulator.topology.components)
    assert summary["unfinished_requests"] == in_system
    assert summary["overdue_requests"] <= summary["unfinished_requests"]
    finished = summary["requests"] - summary["overdue_requests"]
    arrivals = finished + summary["unfinished_requests"]
    assert arrivals == 2933  # seed 7: the arrival stream does not depend on chaos
    assert _run(_stack())[1]["requests"] + _run(_stack())[1]["unfinished_requests"] == arrivals


def test_overdue_requests_are_failed_and_past_the_sla() -> None:
    simulator, summary = _run(_stack(chaos=_db_dead_from(40.0)))
    overdue = [r for r in simulator.collector.requests if r.end == DURATION and not r.success]
    assert len(overdue) == summary["overdue_requests"] > 0
    for record in overdue:
        assert record.latency > 1.0
        assert not record.sla_met
    # latency of an overdue request is its age: a lower bound, so p95 is no
    # longer the p95 of the lucky requests that got through before the outage
    assert summary["p95_latency"] > 10.0


def test_a_healthy_run_is_not_penalised_for_requests_in_flight() -> None:
    """Some requests are always mid-service when a run stops. They are counted
    as unfinished, not as failures."""
    _, summary = _run(_stack())
    assert summary["unfinished_requests"] > 0
    assert summary["overdue_requests"] == 0
    assert summary["completion_rate"] == 1.0
    assert summary["resilience_score"] == 100.0


def test_without_an_sla_the_budget_is_the_slowest_finished_request() -> None:
    simulator, summary = _run(_stack(sla_target=None, chaos=_db_dead_from(1.0)))
    assert summary["completion_rate"] < 0.05
    assert summary["resilience_score"] < 5
    finished = [r for r in simulator.collector.requests if r.success]
    slowest = max(r.latency for r in finished)
    overdue = [r for r in simulator.collector.requests if not r.success]
    assert min(r.latency for r in overdue) > slowest


# ---------------------------------------------------------------------------
# a failed request never meets the SLA
# ---------------------------------------------------------------------------

_STARVED = {"max_capacity": 1, "service_time": 5.0, "timeout": 0.2, "retry_limit": 0}


def test_a_fast_failure_does_not_meet_the_sla() -> None:
    simulator, summary = _run(_stack(worker=_STARVED))
    assert summary["completion_rate"] < 0.01
    assert all(not r.sla_met for r in simulator.collector.requests if not r.success)
    assert summary["sla_compliance"] <= summary["completion_rate"]
    assert "crit:sla_breach" in _severities(summary)
    assert summary["resilience_score"] == 0.0


def test_without_an_sla_compliance_is_the_completion_rate() -> None:
    _, summary = _run(_stack(sla_target=None, worker=_STARVED))
    assert summary["sla_compliance"] == summary["completion_rate"] < 0.01


# ---------------------------------------------------------------------------
# the collector on its own
# ---------------------------------------------------------------------------

def _collector(sla_target: float | None) -> MetricsCollector:
    simulator = CloudSimulator(_stack(sla_target=sla_target))
    return MetricsCollector(simulator.topology, sla_target=sla_target)


def _record(request_id: str, start: float, end: float) -> RequestRecord:
    return RequestRecord(
        request_id=request_id, start=start, end=end, success=True, sla_met=True, cache_hit=None
    )


def test_settle_unfinished_records_only_what_is_past_the_budget() -> None:
    collector = _collector(sla_target=2.0)
    collector.record_arrival("done", 0.0)
    collector.record_request(_record("done", 0.0, 0.5))
    collector.record_arrival("stuck", 1.0)
    collector.record_arrival("in-flight", 9.0)
    collector.settle_unfinished(10.0)

    summary = collector.summary()
    assert summary["unfinished_requests"] == 2
    assert summary["overdue_requests"] == 1
    assert summary["requests"] == 2
    assert summary["failed_requests"] == 1
    stuck = collector.requests[-1]
    assert (stuck.request_id, stuck.start, stuck.end) == ("stuck", 1.0, 10.0)
    assert not stuck.success and not stuck.sla_met


def test_records_without_an_arrival_still_count() -> None:
    """``record_request`` alone is the pre-existing collector API."""
    collector = _collector(sla_target=None)
    collector.record_request(_record("a", 0.0, 1.0))
    summary = collector.summary()
    assert summary["requests"] == 1
    assert summary["unfinished_requests"] == 0
    assert summary["overdue_requests"] == 0
