"""STEP 6 cost-modeling tests: accounting, breakdown, chaos vs healthy cost.

Run with::

    pytest tests/test_cost.py

All tests are deterministic (fixed seed). The chaos comparison uses
``NETWORK_LATENCY`` (deterministic effect on every component) rather than
``COMPONENT_FAILURE`` (random victim selection) so the assertion cannot flap.
"""

from __future__ import annotations

from typing import List

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
)


def _config(cost_per_hour: float, chaos: List[ChaosEvent]) -> SimulationConfig:
    """Comfortable 4-component topology at 1.5 rps for 30 s, same seed both runs.

    1.5 rps keeps the healthy run comfortably below capacity (worker offered
    load ~3 of 4 slots), so a 2x slowdown during the chaos window visibly
    pushes utilisation - and therefore cost - up instead of both runs
    saturating the worker from t=0.
    """
    return SimulationConfig(
        seed=7,
        duration=30.0,
        metrics_interval=1.0,
        sla_target=30.0,
        topology=TopologyConfig(
            load_balancer=LoadBalancerConfig(
                name="lb", max_capacity=10, service_time=0.3, cost_per_hour=cost_per_hour
            ),
            app_worker=AppWorkerConfig(
                name="worker", max_capacity=4, service_time=2.0, cost_per_hour=cost_per_hour
            ),
            cache=CacheConfig(
                name="redis", max_capacity=8, service_time=0.2, hit_rate=0.9,
                cost_per_hour=cost_per_hour,
            ),
            database=DatabaseConfig(
                name="db", max_capacity=3, service_time=4.0, cost_per_hour=cost_per_hour
            ),
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=30.0),
        chaos=chaos,
    )


def test_cost_accumulates_and_breaks_down_by_component() -> None:
    """Non-zero rates produce a sensible total equal to the sum of parts."""
    summary = CloudSimulator(_config(10.0, [])).run()

    assert summary["total_cost"] > 0.0
    breakdown = summary["cost_breakdown_by_component"]
    assert set(breakdown) == {"lb", "worker", "redis", "db"}
    assert all(value > 0.0 for value in breakdown.values())
    assert abs(sum(breakdown.values()) - summary["total_cost"]) < 1e-9


def test_cost_defaults_to_zero_and_is_absent_from_breakdown() -> None:
    """Default config (no rates set) keeps cost accounting disabled."""
    summary = CloudSimulator(_config(0.0, [])).run()

    assert summary["total_cost"] == 0.0
    assert summary["cost_breakdown_by_component"] == {}


def _sizing_config(worker_capacity: int, base_rps: float) -> SimulationConfig:
    """Sizing-test topology: only the worker's capacity varies; the rest is

    sized far above demand so it cannot interfere, and we assert on the
    worker's verdict alone. ``metrics_interval=0.5`` gives 60 samples over the
    30 s horizon, sharpening the p95/p99 estimates the verdict is based on.
    """
    return SimulationConfig(
        seed=11,
        duration=30.0,
        metrics_interval=0.5,
        sla_target=60.0,
        topology=TopologyConfig(
            load_balancer=LoadBalancerConfig(
                name="lb", max_capacity=40, service_time=0.3
            ),
            app_worker=AppWorkerConfig(
                name="worker", max_capacity=worker_capacity, service_time=2.0
            ),
            cache=CacheConfig(
                name="redis", max_capacity=40, service_time=0.2, hit_rate=0.9
            ),
            database=DatabaseConfig(
                name="db", max_capacity=40, service_time=0.5
            ),
        ),
        traffic=TrafficPattern(base_rps=base_rps, duration=30.0),
        chaos=[],
    )


def test_worker_is_flagged_oversized_when_capacity_far_exceeds_demand() -> None:
    """16 slots for ~3 offered load: mostly idle -> waste the report must show."""
    summary = CloudSimulator(_sizing_config(worker_capacity=16, base_rps=1.5)).run()

    sizing = summary["component_sizing"]["worker"]
    assert sizing["status"] == "oversized"
    # "Size to p99 demand" must recommend dropping most of the provisioned slots.
    assert sizing["recommended_capacity"] < 16


def test_worker_is_flagged_undersized_when_saturated_and_queuing() -> None:
    """2 slots for ~4 offered load: permanently saturated -> must be flagged."""
    summary = CloudSimulator(_sizing_config(worker_capacity=2, base_rps=2.0)).run()

    sizing = summary["component_sizing"]["worker"]
    assert sizing["status"] == "undersized"
    # Demand (in_use + queue) exceeds the provisioned 2 slots, so the
    # recommendation must be at least one slot above what was provisioned.
    assert sizing["recommended_capacity"] >= 3


def test_worker_is_flagged_right_sized_at_typical_load() -> None:
    """6 slots for ~4 offered load: mean utilisation ~0.67 sits in the

    healthy band (0.5, 0.85) -> neither oversized waste nor undersized pressure.
    """
    summary = CloudSimulator(_sizing_config(worker_capacity=6, base_rps=2.0)).run()

    sizing = summary["component_sizing"]["worker"]
    assert sizing["status"] == "right_sized"
    # p99 demand can exceed the mean (4) but never by much at this load.
    assert 4 <= sizing["recommended_capacity"] <= 12


def test_idle_capacity_is_still_billed() -> None:
    """Near-zero traffic still accrues the provisioned base (two-part billing).

    With ``cost_per_hour=10`` and capacities 10+4+8+3, the 30 s provisioned
    base alone is 10 * 25 * 30 = $7500 regardless of traffic; a couple of
    stray requests add only a few dollars on top.
    """
    config = _config(10.0, []).model_copy(
        update={"traffic": TrafficPattern(base_rps=0.1, duration=5.0)}
    )

    summary = CloudSimulator(config).run()

    assert summary["requests"] <= 2
    assert 7400.0 < summary["total_cost"] < 8000.0


def test_chaos_run_costs_more_than_healthy_run() -> None:
    """2x slower service keeps the worker saturated and queuing -> higher cost.

    The healthy run sits at ~75% offered load (no queue); the 2x slowdown
    pushes it past capacity, so its queue grows for most of the run. The
    window covers most of the horizon so the difference cannot be masked by
    arrival-pattern noise (the shared RNG interleaves service and arrival
    draws, so the two runs do not see identical arrival counts).
    """
    healthy = CloudSimulator(_config(10.0, [])).run()
    chaotic = CloudSimulator(
        _config(
            10.0,
            [
                ChaosEvent(
                    event_type=ChaosEventType.NETWORK_LATENCY,
                    intensity=1.0,  # service time x2 (intensity is multiplier minus one)
                    start_time=5.0,
                    interval=999.0,
                    duration=25.0,
                )
            ],
        )
    ).run()

    # Both runs serve a healthy number of requests (chaos slows, not fails).
    assert healthy["requests"] > 25
    assert chaotic["requests"] > 10
    # ...but the chaotic run holds workload active much longer, so it costs more.
    assert chaotic["total_cost"] > healthy["total_cost"]
    # The extra cost lands where the work happens (worker + db), not just lb.
    assert (
        chaotic["cost_breakdown_by_component"]["worker"]
        > healthy["cost_breakdown_by_component"]["worker"]
    )
