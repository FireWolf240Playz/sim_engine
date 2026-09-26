"""Compare-runs engine tests: patching, provider calibration, diffs, knee, CLI.

Run with::

    pytest tests/test_compare.py

All simulations use fixed seeds; assertions are on structure, signs, or
preset equality — never on exact stochastic values.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sim_core.cli import main

from sim_core import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ChaosEventType,
    DatabaseConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
)
from sim_core import compare
from sim_core.compare import RunResult, knee_point, run_many
from sim_core.presets import (
    aws_alb,
    aws_app_worker,
    aws_elasticache,
    aws_rds_small,
)


def _base_config() -> SimulationConfig:
    """A small seeded 4-node topology with one chaos event."""
    topology = TopologyConfig(
        load_balancer=LoadBalancerConfig(name="lb", max_capacity=12, service_time=0.5),
        app_worker=AppWorkerConfig(name="app_worker", max_capacity=6, service_time=1.0),
        cache=CacheConfig(
            name="redis", max_capacity=8, service_time=0.3, hit_rate=0.85
        ),
        database=DatabaseConfig(name="db", max_capacity=4, service_time=2.0),
    )
    return SimulationConfig(
        seed=7,
        duration=40.0,
        metrics_interval=2.0,
        sla_target=10.0,
        topology=topology,
        traffic=TrafficPattern(base_rps=1.5, duration=40.0),
        chaos=[
            ChaosEvent(
                event_type=ChaosEventType.COMPONENT_FAILURE,
                intensity=0.5,
                start_time=20.0,
                interval=40.0,
                duration=5.0,
            )
        ],
    )


def _overloaded_config(worker_capacity: int) -> SimulationConfig:
    """Deliberately overloaded worker (2 rps, service ~1s) for sign checks."""
    topology = TopologyConfig(
        load_balancer=LoadBalancerConfig(name="lb", max_capacity=20, service_time=0.2),
        app_worker=AppWorkerConfig(
            name="app_worker", max_capacity=worker_capacity, service_time=1.0
        ),
        database=DatabaseConfig(name="db", max_capacity=20, service_time=0.5),
    )
    return SimulationConfig(
        seed=5,
        duration=40.0,
        topology=topology,
        traffic=TrafficPattern(base_rps=2.0, duration=40.0),
    )


# ---------------------------------------------------------------------------
# set_path
# ---------------------------------------------------------------------------


def test_set_path_node_field_and_immutability() -> None:
    cfg = _base_config()
    original_cap = cfg.topology.node("app_worker").max_capacity
    patched = compare.set_path(cfg, "app_worker.max_capacity", 20)
    assert patched.topology.node("app_worker").max_capacity == 20
    # The original frozen config must be untouched.
    assert cfg.topology.node("app_worker").max_capacity == original_cap


def test_set_path_string_value_coerced() -> None:
    cfg = _base_config()
    patched = compare.set_path(cfg, "app_worker.max_capacity", "12")
    assert patched.topology.node("app_worker").max_capacity == 12


def test_set_path_traffic_field() -> None:
    cfg = _base_config()
    patched = compare.set_path(cfg, "traffic.base_rps", 4.0)
    assert patched.traffic.base_rps == 4.0


def test_set_path_chaos_index_field() -> None:
    cfg = _base_config()
    patched = compare.set_path(cfg, "chaos.0.intensity", 0.25)
    assert patched.chaos[0].intensity == 0.25


def test_set_path_unknown_node_raises() -> None:
    cfg = _base_config()
    with pytest.raises(ValueError, match="unknown node"):
        compare.set_path(cfg, "nope.max_capacity", 4)


def test_set_path_invalid_value_raises_validation_error() -> None:
    cfg = _base_config()
    with pytest.raises(ValidationError):
        compare.set_path(cfg, "app_worker.max_capacity", 0)


# ---------------------------------------------------------------------------
# apply_provider
# ---------------------------------------------------------------------------


def test_apply_provider_matches_presets_and_keeps_names() -> None:
    cfg = _base_config()
    patched = compare.apply_provider(cfg, "aws")
    by_name = {node.name: node for node in patched.topology.nodes}
    expected = {
        "lb": aws_alb(name="lb"),
        "app_worker": aws_app_worker(name="app_worker"),
        "redis": aws_elasticache(name="redis"),
        "db": aws_rds_small(name="db"),
    }
    assert set(by_name) == set(expected)
    for name, want in expected.items():
        got = by_name[name]
        assert got.max_capacity == want.max_capacity
        assert got.service_time == want.service_time
        assert got.cost_per_hour == want.cost_per_hour
        if isinstance(want, CacheConfig):
            assert got.hit_rate == want.hit_rate


def test_apply_provider_preserves_retry_settings() -> None:
    cfg = _base_config()
    with_retry = compare.set_path(
        compare.set_path(cfg, "app_worker.timeout", 2.0), "app_worker.retry_limit", 3
    )
    patched = compare.apply_provider(with_retry, "aws")
    node = next(n for n in patched.topology.nodes if n.name == "app_worker")
    assert node.timeout == 2.0
    assert node.retry_limit == 3


def test_apply_provider_unknown_provider_raises() -> None:
    cfg = _base_config()
    with pytest.raises(ValueError, match="unknown provider"):
        compare.apply_provider(cfg, "oracle")


# ---------------------------------------------------------------------------
# run_many / diff_runs / what-if / multi-cloud
# ---------------------------------------------------------------------------


def test_run_many_and_diff_structure() -> None:
    cfg = _base_config()
    results = run_many(
        [
            ("base", cfg),
            ("big", compare.set_path(cfg, "app_worker.max_capacity", 20)),
        ]
    )
    diff = compare.diff_runs(results)
    assert diff["baseline"] == "base"
    assert [r["label"] for r in diff["runs"]] == ["base", "big"]
    baseline_row = diff["runs"][0]
    for key, delta in baseline_row["deltas"].items():
        assert delta == 0
    assert "sizing" in diff["runs"][1]


def test_what_if_bigger_capacity_lowers_p95() -> None:
    small = _overloaded_config(worker_capacity=1)
    big = _overloaded_config(worker_capacity=8)
    results = run_many([("small", small), ("big", big)])
    p95_small = results[0].summary["p95_latency"]
    p95_big = results[1].summary["p95_latency"]
    assert p95_big < p95_small


def test_multi_cloud_costs_differ() -> None:
    cfg = _base_config()
    results = run_many([(p, compare.apply_provider(cfg, p)) for p in compare.PROVIDERS])
    costs = {r.label: r.summary["total_cost"] for r in results}
    assert all(c > 0 for c in costs.values())
    assert len({round(c, 6) for c in costs.values()}) >= 2


# ---------------------------------------------------------------------------
# knee_point (synthetic data — no simulation needed)
# ---------------------------------------------------------------------------


def _fake_result(value: float, p95: float) -> RunResult:
    return RunResult(
        label=f"app_worker.max_capacity={value:g}",
        summary={"p95_latency": p95, "total_cost": value * 0.1},
        config=None,
        parameter_value=value,
    )


def test_knee_point_directional() -> None:
    # 100 -> 60 (40% pay-off), 60 -> 45 (25% pay-off), then <5% steps.
    results = [
        _fake_result(v, p)
        for v, p in ((1, 100.0), (2, 60.0), (4, 45.0), (8, 44.0), (16, 43.9))
    ]
    assert knee_point(results, threshold=0.05) == 4.0


def test_knee_point_monotonic_returns_last_value() -> None:
    results = [_fake_result(v, p) for v, p in ((1, 10.0), (2, 8.0), (4, 6.0), (8, 4.0))]
    assert knee_point(results, threshold=0.05) == 8.0


def test_knee_point_flat_returns_first_value() -> None:
    results = [_fake_result(1, 10.0), _fake_result(2, 9.9)]
    assert knee_point(results, threshold=0.05) == 1.0


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


def test_cli_compare_what_if_end_to_end(tmp_path) -> None:

    cfg_path = tmp_path / "topology.json"
    _base_config().to_json(str(cfg_path))
    out_json = tmp_path / "comparison.json"

    rc = main(
        [
            "compare",
            "what-if",
            "--topology",
            str(cfg_path),
            "--set",
            "app_worker.max_capacity=16",
            "--json",
            str(out_json),
        ]
    )
    assert rc == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["mode"] == "what-if"
    assert [r["label"] for r in data["runs"]] == ["baseline", "what-if"]
    assert "deltas" in data["runs"][1]
