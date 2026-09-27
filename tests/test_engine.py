"""End-to-end and unit tests for the Eleven simulator.

Run with::

    pytest

The tests are deterministic: every run uses a fixed seed and the simulator
derives all of its randomness from a single ``numpy.random.Generator``, so the
results are reproducible and the assertions are stable (no flakiness).
"""

from __future__ import annotations

import simpy
import pytest
from pydantic import ValidationError

from sim_core import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ChaosEventType,
    CloudSimulator,
    ComponentConfig,
    DatabaseConfig,
    Edge,
    GraphTopologyConfig,
    LoadBalancerConfig,
    SimulationConfig,
    Topology,
    TopologyConfig,
    TrafficPattern,
)
from sim_core.metrics import MetricsCollector


def make_config(
    seed: int = 7,
    duration: float = 20.0,
    base_rps: float = 2.0,
    with_cache: bool = True,
    with_chaos: bool = True,
) -> SimulationConfig:
    """Build a small, fast configuration for tests."""
    cache = (
        CacheConfig(name="redis", max_capacity=8, service_time=0.2, hit_rate=0.9)
        if with_cache
        else None
    )
    topology = TopologyConfig(
        load_balancer=LoadBalancerConfig(name="lb", max_capacity=10, service_time=0.4),
        app_worker=AppWorkerConfig(name="worker", max_capacity=5, service_time=1.5),
        cache=cache,
        database=DatabaseConfig(name="db", max_capacity=4, service_time=3.0),
    )
    traffic = TrafficPattern(base_rps=base_rps, duration=duration)

    chaos: list[ChaosEvent] = []
    if with_chaos:
        chaos = [
            ChaosEvent(
                event_type=ChaosEventType.CACHE_OUTAGE,
                intensity=1.0,
                start_time=5.0,
                interval=10.0,
                duration=3.0,
            ),
            ChaosEvent(
                event_type=ChaosEventType.NETWORK_LATENCY,
                intensity=0.6,
                start_time=8.0,
                interval=12.0,
                duration=3.0,
            ),
        ]

    return SimulationConfig(
        seed=seed,
        duration=duration,
        metrics_interval=1.0,
        sla_target=15.0,
        topology=topology,
        traffic=traffic,
        chaos=chaos,
    )


def test_run_produces_requests_and_percentiles() -> None:
    """A run completes, serves requests, and populates ordered percentiles."""
    simulator = CloudSimulator(make_config())
    summary = simulator.run()

    assert summary["requests"] > 0
    for key in ("avg_latency", "p50_latency", "p95_latency", "p99_latency"):
        assert summary[key] is not None
    assert summary["p99_latency"] >= summary["p95_latency"] >= summary["p50_latency"]
    assert 0.0 <= summary["completion_rate"] <= 1.0
    assert 0.0 <= summary["sla_compliance"] <= 1.0


def test_dataframes_have_expected_columns() -> None:
    """Both DataFrames expose their documented columns and cover all components."""
    simulator = CloudSimulator(make_config())
    simulator.run()
    collector = simulator.collector

    requests_df = collector.requests_df()
    assert not requests_df.empty
    for column in ("request_id", "start", "end", "latency", "success", "sla_met"):
        assert column in requests_df.columns

    utilization_df = collector.utilization_df()
    assert not utilization_df.empty
    for column in (
        "time",
        "component",
        "queue_length",
        "in_use",
        "capacity",
        "utilization",
    ):
        assert column in utilization_df.columns

    components = set(utilization_df["component"].unique())
    assert {"lb", "worker", "db", "redis"} <= components


def test_reproducible_with_same_seed() -> None:
    """A fixed seed yields a bit-for-bit reproducible headline summary."""
    config = make_config(seed=123)
    first = CloudSimulator(config).run()
    second = CloudSimulator(config).run()
    assert first == second


def test_no_cache_path_still_runs() -> None:
    """Without a cache, the path is lb -> worker -> db and cache rate is None."""
    simulator = CloudSimulator(make_config(with_cache=False))
    summary = simulator.run()

    assert summary["requests"] > 0
    assert summary["cache_hit_rate"] is None


def test_downstream_node_is_actually_served() -> None:
    """worker→db chain: the db is genuinely on the request path.

    Regression guard for the "orphan node" trap: a db listed in the topology
    but never linked by an edge serves zero traffic and its utilisation
    stays 0%. With the edge, steady-state demand is ≈ base_rps × service_time
    = 1.5 × 2.5 = 3.75 of 4 slots, so the db must read clearly loaded.
    """
    config = SimulationConfig(
        seed=42,
        duration=60.0,
        metrics_interval=2.0,
        topology=GraphTopologyConfig(
            nodes=[
                ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=1.2),
                ComponentConfig(name="db", role="database", max_capacity=4, service_time=2.5),
            ],
            edges=[Edge(source="worker", target="db", probability=1.0)],
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=60.0),
    )
    summary = CloudSimulator(config).run()
    db = summary["component_sizing"]["db"]
    assert db["mean_utilization"] > 0.5  # ≈ 3.75/4 in steady state
    assert db["status"] in {"right_sized", "undersized"}


def test_unreachable_node_rejected_at_config_validation() -> None:
    """A node no request can reach (missing edge) fails fast with a clear message."""
    with pytest.raises(ValidationError, match="unreachable"):
        SimulationConfig(
            seed=1,
            duration=10.0,
            topology=GraphTopologyConfig(
                nodes=[
                    ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=1.0),
                    ComponentConfig(name="db", role="database", max_capacity=4, service_time=2.0),
                ]
            ),
            traffic=TrafficPattern(base_rps=1.0, duration=10.0),
        )


def test_config_validation_rejects_bad_capacity() -> None:
    with pytest.raises(ValidationError):
        LoadBalancerConfig(name="lb", max_capacity=0, service_time=0.5)


def test_config_validation_rejects_negative_rps() -> None:
    with pytest.raises(ValidationError):
        TrafficPattern(base_rps=-1.0, duration=10.0)


def test_collector_empty_summary() -> None:
    """An empty collector reports zero requests and all-None headline metrics."""
    config = make_config()
    topology = Topology(simpy.Environment(), config.topology)
    collector = MetricsCollector(topology)

    assert collector.requests == []
    empty = collector.summary()
    assert empty["requests"] == 0
    assert empty["avg_latency"] is None
    assert empty["p99_latency"] is None
