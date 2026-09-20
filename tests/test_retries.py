"""Deterministic tests for timeout + retry behaviour (STEP 1).

These tests drive arrivals *manually* through the public API
(``CloudSimulator.serve_request``) and hold a worker slot with a raw
``resource.request()``/``release()`` pair, so the critical timing is
independent of the traffic generator's random draws. With a fixed seed every
draw is deterministic, so the assertions are stable (no flakiness).

Run with::

    pytest tests/test_retries.py
"""

from __future__ import annotations

from typing import Any, Dict

from sim_core import (
    AppWorkerConfig,
    CloudSimulator,
    DatabaseConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
)


def _make_sim(
    worker_timeout: float | None,
    retry_limit: int,
    seed: int = 1,
) -> CloudSimulator:
    """Build a simulator with a *single-slot* worker that is the retry victim."""
    topology = TopologyConfig(
        load_balancer=LoadBalancerConfig(name="lb", max_capacity=5, service_time=0.05),
        app_worker=AppWorkerConfig(
            name="worker",
            max_capacity=1,
            service_time=0.05,
            timeout=worker_timeout,
            retry_limit=retry_limit,
            retry_backoff=0.1,
        ),
        cache=None,
        database=DatabaseConfig(name="db", max_capacity=5, service_time=0.05),
    )
    config = SimulationConfig(
        seed=seed,
        duration=100.0,  # not used: these tests drive env.run(until=...) directly
        metrics_interval=1.0,
        sla_target=50.0,
        topology=topology,
        traffic=TrafficPattern(base_rps=1.0, duration=1.0),
        chaos=[],
    )
    return CloudSimulator(config)


def _run_manual(
    sim: CloudSimulator,
    holder_release_at: float,
    request_arrival_at: float,
    until: float,
) -> Dict[str, Any]:
    """Hold the worker slot, admit one request at a fixed time, run to *until*.

    The holder uses raw SimPy primitives (no RNG, no timeouts), so the only
    randomness on the critical path is the small exponential service-time draw
    of the other components (fixed seed => deterministic).
    """
    worker = sim.topology.component("worker")

    def _holder() -> object:
        req = worker.resource.request()
        yield req
        yield sim.env.timeout(holder_release_at)
        worker.resource.release(req)
        return None

    def _request() -> object:
        yield sim.env.timeout(request_arrival_at)
        yield from sim.serve_request("req-b")
        return None

    sim.env.process(_holder())
    sim.env.process(_request())
    sim.env.run(until=until)
    return sim.collector.summary()


def _assert_no_resource_leaks(sim: CloudSimulator) -> None:
    """No pending requests and no lingering users after the run."""
    worker = sim.topology.component("worker")
    assert len(worker.resource.queue) == 0
    assert worker.resource.count == 0


def test_timeout_zero_retries_fails_immediately() -> None:
    """retry_limit=0: a request that cannot get a slot in time fails at once."""
    sim = _make_sim(worker_timeout=1.0, retry_limit=0)
    # Holder keeps the only worker slot from t=0 until t=3.0.
    # Request B arrives at t=0.2 and may wait at most 1.0 s -> fails at ~1.2.
    summary = _run_manual(
        sim, holder_release_at=3.0, request_arrival_at=0.2, until=4.0
    )

    assert summary["requests"] == 1
    assert summary["completion_rate"] == 0.0
    assert summary["failed_requests"] == 1
    assert summary["total_retries"] == 0
    _assert_no_resource_leaks(sim)


def test_retry_limit_recovers_request() -> None:
    """retry_limit=3: the same request recovers once the slot frees at t=3.0.

    Expected sequence (arrival 0.2, timeout 1.0, backoff 0.1):
      attempt 1: times out at ~1.2  -> retry #1
      attempt 2: times out at ~2.3  -> retry #2
      attempt 3: deadline ~3.4 > 3.0 -> slot granted when the holder releases
    so exactly two retries are recorded and the request completes.
    """
    sim = _make_sim(worker_timeout=1.0, retry_limit=3)
    summary = _run_manual(
        sim, holder_release_at=3.0, request_arrival_at=0.2, until=4.0
    )

    assert summary["requests"] == 1
    assert summary["completion_rate"] == 1.0
    assert summary["failed_requests"] == 0
    assert summary["total_retries"] == 2
    _assert_no_resource_leaks(sim)


def test_retry_exhaustion_fails_after_all_attempts() -> None:
    """All retries exhausted: 4 real attempts (3 retries) then a failure."""
    sim = _make_sim(worker_timeout=0.5, retry_limit=3)
    # Holder keeps the slot until t=10.0, far beyond every retry deadline.
    summary = _run_manual(
        sim, holder_release_at=10.0, request_arrival_at=0.2, until=11.0
    )

    assert summary["requests"] == 1
    assert summary["completion_rate"] == 0.0
    assert summary["failed_requests"] == 1
    assert summary["total_retries"] == 3  # retry_limit attempts, last one final
    _assert_no_resource_leaks(sim)


def test_retry_counts_are_per_component() -> None:
    """The retry counter is attributed to the component that actually timed out."""
    sim = _make_sim(worker_timeout=1.0, retry_limit=2)
    _run_manual(sim, holder_release_at=10.0, request_arrival_at=0.2, until=11.0)

    collector = sim.collector
    assert collector.retry_counts == {"worker": 2}
    assert "lb" not in collector.retry_counts
    assert "db" not in collector.retry_counts
