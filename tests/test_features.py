"""STEP 5 feature tests: summary ranges, live chaos state, retries, graph branching.

Run with::

    pytest tests/test_features.py

Every test is deterministic: a fixed seed drives a single shared
``numpy.random.Generator`` inside the simulator, and the assertions use
*ranges* (not exact values) wherever the simulation has legitimate
randomness.
"""

from __future__ import annotations

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
    TopologyConfig,
    TrafficPattern,
)


# --------------------------------------------------------------------------
# 1. Small topology, fixed seed: summary metrics land in sane ranges
# --------------------------------------------------------------------------
def test_small_topology_summary_in_expected_ranges() -> None:
    """2 rps for 20 s (~40 Poisson arrivals) on a comfortable topology."""
    sim = CloudSimulator(
        SimulationConfig(
            seed=99,
            duration=20.0,
            metrics_interval=1.0,
            sla_target=15.0,
            topology=TopologyConfig(
                load_balancer=LoadBalancerConfig(name="lb", max_capacity=10, service_time=0.4),
                app_worker=AppWorkerConfig(name="worker", max_capacity=5, service_time=1.5),
                cache=CacheConfig(name="redis", max_capacity=8, service_time=0.2, hit_rate=0.9),
                database=DatabaseConfig(name="db", max_capacity=4, service_time=3.0),
            ),
            traffic=TrafficPattern(base_rps=2.0, duration=20.0),
            chaos=[],
        )
    )
    summary = sim.run()

    # ~40 expected arrivals (Poisson, sigma ~6): a 3-sigma band is very safe.
    assert 20 <= summary["requests"] <= 70
    # Comfortable capacity: essentially everything completes.
    assert summary["completion_rate"] >= 0.9
    assert summary["failed_requests"] < 10
    # Latencies: mean dominated by worker (1.5 s) + occasional DB fall-through (3 s).
    assert 0.5 <= summary["avg_latency"] <= 10.0
    # Percentiles are ordered and computed over the full sample.
    assert summary["p50_latency"] <= summary["p95_latency"] <= summary["p99_latency"]
    # Observed hit rate should sit near the configured 0.9 (binomial noise).
    assert 0.7 <= summary["cache_hit_rate"] <= 0.99
    # No timeouts configured, so no retries can happen.
    assert summary["total_retries"] == 0


# --------------------------------------------------------------------------
# 2. component_failure really reduces live capacity, then restores it
# --------------------------------------------------------------------------
def test_component_failure_reduces_and_restores_capacity() -> None:
    """A single-node topology makes the chaos victim deterministic.

    The chaos window is [5, 15); the run horizon (12.0) lands *inside* it, so
    right after ``run()`` the live capacity must still be degraded, and
    continuing the clock past 15 must restore it.
    """
    sim = CloudSimulator(
        SimulationConfig(
            seed=1,
            duration=12.0,
            metrics_interval=5.0,
            topology=GraphTopologyConfig(
                nodes=[ComponentConfig(name="solo", max_capacity=10, service_time=0.2)]
            ),
            traffic=TrafficPattern(base_rps=1.0, duration=12.0),
            chaos=[
                ChaosEvent(
                    event_type=ChaosEventType.COMPONENT_FAILURE,
                    intensity=0.5,  # 10 slots -> 5 slots
                    start_time=5.0,
                    interval=20.0,
                    duration=10.0,
                )
            ],
        )
    )

    solo = sim.topology.component("solo")
    assert solo.capacity == 10  # sanity: config value seeded into the live resource

    sim.run()  # ends at t=12, inside the [5, 15) disruption window
    assert solo.capacity == 5, "capacity must be reduced while the failure window is active"

    sim.env.run(until=16.0)  # cross the restore point at t=15
    assert solo.capacity == 10, "capacity must be restored after the window ends"


# --------------------------------------------------------------------------
# 3. cache_outage really drives the live hit-rate, then restores it
# --------------------------------------------------------------------------
def test_cache_outage_changes_and_restores_hit_rate() -> None:
    """Intensity 1.0 => hit_rate forced to 0.0 in-window (every lookup misses).

    Also asserts the *behavioural* consequence: requests that start inside the
    window must all miss the cache and fall through to the database.
    """
    sim = CloudSimulator(
        SimulationConfig(
            seed=2,
            duration=12.0,
            metrics_interval=5.0,
            topology=TopologyConfig(
                load_balancer=LoadBalancerConfig(name="lb", max_capacity=10, service_time=0.1),
                app_worker=AppWorkerConfig(name="worker", max_capacity=10, service_time=0.1),
                cache=CacheConfig(name="redis", max_capacity=10, service_time=0.1, hit_rate=0.9),
                database=DatabaseConfig(name="db", max_capacity=10, service_time=0.1),
            ),
            traffic=TrafficPattern(base_rps=1.0, duration=12.0),
            chaos=[
                ChaosEvent(
                    event_type=ChaosEventType.CACHE_OUTAGE,
                    intensity=1.0,
                    start_time=5.0,
                    interval=20.0,
                    duration=10.0,
                )
            ],
        )
    )

    cache = sim.topology.cache
    assert cache is not None
    assert cache.hit_rate == 0.9  # sanity: configured value is live

    sim.run()  # ends at t=12, inside the [5, 15) outage window
    assert cache.hit_rate == 0.0, "hit-rate must be driven to 0 while the outage is active"

    # Behavioural effect: every request that starts in the window misses and
    # reaches the database (start < 14 keeps the cache draw safely in-window).
    in_window = [r for r in sim.collector.requests if 5.0 <= r.start < 14.0]
    assert in_window, "expected some requests to start inside the outage window"
    for record in in_window:
        assert record.cache_hit is False
        assert "db" in record.component_times

    sim.env.run(until=16.0)  # cross the restore point at t=15
    assert cache.hit_rate == 0.9, "hit-rate must be restored after the window ends"


# --------------------------------------------------------------------------
# 4+5. Retry semantics: retry_limit=0 fails fast; retry_limit=3 succeeds more
# --------------------------------------------------------------------------
def _saturated_worker_sim(retry_limit: int) -> CloudSimulator:
    """Two-slot worker, 3.5 rps load, 0.5 s acquisition timeout.

    Load is high enough that some requests genuinely time out (so the
    retry_limit=0 config has failures to compare against) but low enough
    that extra retry attempts (4 total, ~1.9 s budget) usually win a slot.
    """
    topology = TopologyConfig(
        load_balancer=LoadBalancerConfig(name="lb", max_capacity=5, service_time=0.05),
        app_worker=AppWorkerConfig(
            name="worker",
            max_capacity=2,
            service_time=0.6,
            timeout=0.5,
            retry_limit=retry_limit,
            retry_backoff=0.3,
        ),
        cache=None,
        database=DatabaseConfig(name="db", max_capacity=5, service_time=0.05),
    )
    return CloudSimulator(
        SimulationConfig(
            seed=5,
            duration=20.0,
            metrics_interval=1.0,
            topology=topology,
            traffic=TrafficPattern(base_rps=3.5, duration=20.0),
            chaos=[],
        )
    )


def test_retry_limit_zero_fails_immediately_on_timeout() -> None:
    """With no retries a timed-out acquisition fails the request at once:
    there *are* timeouts under this load, and zero retries were recorded."""
    summary = _saturated_worker_sim(retry_limit=0).run()

    assert summary["failed_requests"] > 0, "this load must produce timeouts"
    assert summary["total_retries"] == 0, "retry_limit=0 must never retry"


def test_retry_limit_three_succeeds_more_often_under_same_load() -> None:
    """Identical topology/traffic/seed; only retry_limit differs.

    Retries must actually happen (recorded per timed-out attempt) and the
    extra attempts must convert some would-be failures into successes.
    """
    no_retry = _saturated_worker_sim(retry_limit=0).run()
    with_retry = _saturated_worker_sim(retry_limit=3).run()

    assert with_retry["total_retries"] > 0, "timed-out attempts must be retried"
    assert with_retry["failed_requests"] < no_retry["failed_requests"]
    assert with_retry["completion_rate"] > no_retry["completion_rate"]


# --------------------------------------------------------------------------
# 6. Graph traversal: branching edges are followed with their probabilities
# --------------------------------------------------------------------------
def test_branching_edges_follow_configured_probabilities() -> None:
    """Entry fans out to A (p=0.75) and B (p=0.25), followed *independently*.

    Over 4000 requests the visit fractions must converge to the configured
    probabilities (bands are ~10-12 sigma of binomial noise, so stable), and
    the joint visit fraction must match pA*pB (independence check).
    """
    n_requests = 4000
    p_a, p_b = 0.75, 0.25
    sim = CloudSimulator(
        SimulationConfig(
            seed=42,
            duration=1000.0,  # not reached: we bound the run below
            metrics_interval=100.0,
            topology=GraphTopologyConfig(
                nodes=[
                    ComponentConfig(name="entry", max_capacity=100, service_time=0.05),
                    ComponentConfig(name="a", max_capacity=100, service_time=0.05),
                    ComponentConfig(name="b", max_capacity=100, service_time=0.05),
                ],
                edges=[
                    Edge(source="entry", target="a", probability=p_a),
                    Edge(source="entry", target="b", probability=p_b),
                ],
            ),
            traffic=TrafficPattern(base_rps=1.0, duration=1.0),  # unused; requests spawned manually
            chaos=[],
        )
    )

    for i in range(n_requests):
        sim.env.process(sim.serve_request(f"req-{i}"))
    sim.env.run(until=30.0)

    records = sim.collector.requests
    assert len(records) == n_requests
    visits_a = sum(1 for r in records if "a" in r.component_times) / len(records)
    visits_b = sum(1 for r in records if "b" in r.component_times) / len(records)
    visits_both = sum(1 for r in records if "a" in r.component_times and "b" in r.component_times) / len(records)

    assert 0.70 <= visits_a <= 0.80, f"edge entry->a visited {visits_a:.3f}, expected ~{p_a}"
    assert 0.20 <= visits_b <= 0.30, f"edge entry->b visited {visits_b:.3f}, expected ~{p_b}"
    # Independent edge evaluation: P(both) = pA * pB = 0.1875
    assert 0.15 <= visits_both <= 0.22, f"both visited {visits_both:.3f}, expected ~{p_a * p_b}"
