"""STEP 7 preset tests: shape, values, customization, and an end-to-end run.

Run with::

    pytest tests/test_presets.py

All tests are deterministic (fixed seed where a simulation runs).
"""

from __future__ import annotations

from typing import Callable, List, Tuple, Type

import pytest

from sim_core import (
    CloudSimulator,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
)
from sim_core.config import (
    AppWorkerConfig,
    CacheConfig,
    ComponentConfig,
    ComponentRole,
    DatabaseConfig,
    LoadBalancerConfig,
)
from sim_core.presets import (
    PRESETS,
    aws_alb,
    aws_app_worker,
    aws_elasticache,
    aws_rds_small,
    azure_app_gateway,
    azure_app_worker,
    azure_cache,
    azure_sql_small,
    gcp_app_worker,
    gcp_cloud_sql_small,
    gcp_lb,
    gcp_memorystore,
)

# (factory, expected subclass, expected role, expected default name)
_PresetFactory = Callable[[], ComponentConfig]
EXPECTED: List[Tuple[_PresetFactory, Type[ComponentConfig], ComponentRole, str]] = [
    (aws_alb, LoadBalancerConfig, ComponentRole.LOAD_BALANCER, "aws-alb"),
    (aws_app_worker, AppWorkerConfig, ComponentRole.WORKER, "aws-worker"),
    (aws_elasticache, CacheConfig, ComponentRole.CACHE, "aws-elasticache"),
    (aws_rds_small, DatabaseConfig, ComponentRole.DATABASE, "aws-rds"),
    (azure_app_gateway, LoadBalancerConfig, ComponentRole.LOAD_BALANCER, "azure-app-gateway"),
    (azure_app_worker, AppWorkerConfig, ComponentRole.WORKER, "azure-worker"),
    (azure_cache, CacheConfig, ComponentRole.CACHE, "azure-cache"),
    (azure_sql_small, DatabaseConfig, ComponentRole.DATABASE, "azure-sql"),
    (gcp_lb, LoadBalancerConfig, ComponentRole.LOAD_BALANCER, "gcp-lb"),
    (gcp_app_worker, AppWorkerConfig, ComponentRole.WORKER, "gcp-worker"),
    (gcp_memorystore, CacheConfig, ComponentRole.CACHE, "gcp-memorystore"),
    (gcp_cloud_sql_small, DatabaseConfig, ComponentRole.DATABASE, "gcp-cloud-sql"),
]


@pytest.mark.parametrize("factory,config_cls,role,default_name", EXPECTED)
def test_preset_shape_and_values(
    factory: _PresetFactory,
    config_cls: Type[ComponentConfig],
    role: ComponentRole,
    default_name: str,
) -> None:
    """Every preset is a valid config of the right subclass, role, and sane values."""
    cfg = factory()
    assert isinstance(cfg, config_cls)
    assert cfg.role is role
    assert cfg.name == default_name
    assert cfg.max_capacity >= 1
    assert cfg.service_time > 0.0
    assert cfg.cost_per_hour > 0.0


def test_cache_presets_carry_a_hit_rate() -> None:
    """Cache presets pin a realistic hit rate (not the 0.9 model default)."""
    for factory in (aws_elasticache, azure_cache, gcp_memorystore):
        cfg = factory()
        assert 0.5 <= cfg.hit_rate <= 1.0


def test_preset_name_override() -> None:
    """An explicit name replaces the default (needed for multi-node graphs)."""
    cfg = aws_rds_small(name="prod-db")
    assert cfg.name == "prod-db"
    assert isinstance(cfg, DatabaseConfig)


def test_preset_is_customizable_via_model_copy() -> None:
    """Frozen presets stay safe to tune with model_copy(update=...)."""
    base = aws_alb()
    custom = base.model_copy(update={"max_capacity": 50, "cost_per_hour": 0.2})
    assert custom.max_capacity == 50
    assert custom.cost_per_hour == 0.2
    assert custom.name == "aws-alb"
    assert base.max_capacity == 200  # original untouched


def test_registry_covers_all_twelve_presets() -> None:
    from sim_core import presets as presets_module

    assert len(PRESETS) == 12
    for name, factory in PRESETS.items():
        assert name in presets_module.__all__
        assert isinstance(factory(), ComponentConfig)


def test_aws_preset_topology_runs_end_to_end() -> None:
    """The spec's DONE-WHEN: presets drop straight into a runnable simulation.

    At 1.5 rps the preset worker (~5 rps capacity) and DB (~3 rps) are
    comfortably sized, so the run must complete with a high completion rate,
    positive cost per component, and a sizing verdict for every component.
    """
    config = SimulationConfig(
        seed=42,
        duration=30.0,
        metrics_interval=1.0,
        sla_target=60.0,
        topology=TopologyConfig(
            load_balancer=aws_alb(),
            app_worker=aws_app_worker(),
            cache=aws_elasticache(),
            database=aws_rds_small(),
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=30.0),
        chaos=[],
    )
    summary = CloudSimulator(config).run()

    names = {"aws-alb", "aws-worker", "aws-elasticache", "aws-rds"}
    assert summary["requests"] > 0
    assert summary["completion_rate"] > 0.9
    assert summary["total_cost"] > 0.0
    assert set(summary["cost_breakdown_by_component"]) == names
    assert all(v > 0.0 for v in summary["cost_breakdown_by_component"].values())
    assert set(summary["component_sizing"]) == names
    assert summary["cache_hit_rate"] is not None
