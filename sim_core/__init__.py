"""Eleven: a pre-deployment cloud-resilience simulator.

Public API of the :mod:`sim_core` package. A bare ``import sim_core`` gives you
the validated configuration models, the :class:`CloudSimulator` orchestrator,
the metrics primitives, and the live topology wrappers. The report renderer is
imported lazily (see ``__getattr__``) so that importing the package never
forces matplotlib onto the interpreter.

Quick start::

    from sim_core import (
        AppWorkerConfig, CacheConfig, ChaosEvent, ChaosEventType,
        CloudSimulator, DatabaseConfig, LoadBalancerConfig,
        SimulationConfig, TopologyConfig, TrafficPattern, render_report,
    )

    config = SimulationConfig(
        seed=42,
        duration=120.0,
        sla_target=20.0,
        topology=TopologyConfig(
            load_balancer=LoadBalancerConfig(name="lb", max_capacity=12, service_time=0.5),
            app_worker=AppWorkerConfig(name="worker", max_capacity=6, service_time=2.0),
            cache=CacheConfig(name="redis", max_capacity=8, service_time=0.3, hit_rate=0.85),
            database=DatabaseConfig(name="db", max_capacity=4, service_time=5.0),
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=120.0),
        chaos=[
            ChaosEvent(event_type=ChaosEventType.CACHE_OUTAGE, intensity=1.0,
                       start_time=50.0, interval=45.0, duration=10.0),
        ],
    )

    simulator = CloudSimulator(config)
    summary = simulator.run()
    render_report(simulator.collector)
"""

from __future__ import annotations

from typing import Any

from sim_core.config import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ChaosEventType,
    ComponentConfig,
    ComponentRole,
    DatabaseConfig,
    Edge,
    GraphTopologyConfig,
    LoadBalancerConfig,
    PatternType,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
)
from sim_core.engine import CloudSimulator, ComponentTimeoutError
from sim_core.metrics import MetricsCollector, RequestRecord, UtilizationSample
from sim_core.topology import Component, Topology

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Configuration models
    "PatternType",
    "ChaosEventType",
    "ComponentRole",
    "ComponentConfig",
    "LoadBalancerConfig",
    "AppWorkerConfig",
    "CacheConfig",
    "DatabaseConfig",
    "Edge",
    "GraphTopologyConfig",
    "TopologyConfig",
    "TrafficPattern",
    "ChaosEvent",
    "SimulationConfig",
    # Engine
    "CloudSimulator",
    "ComponentTimeoutError",
    # Metrics
    "MetricsCollector",
    "RequestRecord",
    "UtilizationSample",
    # Topology
    "Component",
    "Topology",
    # Visualization (imported lazily via __getattr__)
    "render_report",
]


def __getattr__(name: str) -> Any:
    """Lazily import the report renderer so ``import sim_core`` stays light.

    PEP 562 module attribute hook: resolves ``render_report`` on first access
    without eagerly pulling in the visualization module (and its matplotlib
    backend) at package import time.
    """
    if name == "render_report":
        from sim_core.viz import render_report

        return render_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
