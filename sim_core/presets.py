"""STEP 7 — Provider-calibrated component presets.

Drop-in starting points for :mod:`sim_core.config` component models, seeded
from realistic, publicly published list prices and throughput ballpark
figures for common managed services (AWS, Azure, GCP). Each function returns
a fully-populated, ready-to-use config so you never have to guess
``max_capacity`` / ``service_time`` / ``cost_per_hour`` from scratch::

    from sim_core.presets import aws_rds_small, aws_elasticache, aws_alb, aws_app_worker
    from sim_core import TopologyConfig, SimulationConfig, TrafficPattern

    config = SimulationConfig(
        seed=42,
        duration=120.0,
        sla_target=20.0,
        topology=TopologyConfig(
            load_balancer=aws_alb(),
            app_worker=aws_app_worker(),
            cache=aws_elasticache(),
            database=aws_rds_small(),
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=120.0),
    )

**The numbers are directional estimates, not guaranteed benchmarks.** They
approximate small "on-demand / list-price" tiers (us-east-1 / eastus /
us-central1 class) and are chosen so a default preset topology has a
believable bottleneck ordering at ~1-2 rps: worker and database constrain,
load balancer and cache do not. Performance is also differentiated per
provider: each cloud's small tier has different concurrency and
per-request latency, which is exactly what ``eleven compare multi-cloud``
compares. Live prices move; for exact rates use the ``eleven aws-prices`` /
``eleven prices`` / ``eleven gcp-prices`` catalog commands instead.

Cost: ``cost_per_hour`` is a rate **per slot**. Each preset spreads its
instance's hourly list price over its slots, so an idle preset bills exactly
that list price and a busy one adds the metered part on top.

Customizing: the models are frozen, so tune a preset with
``model_copy(update={...})`` rather than re-inventing it (more slots at the
same per-slot rate is a proportionally larger instance)::

    db = aws_rds_small().model_copy(update={"max_capacity": 24})
"""

from __future__ import annotations

from typing import Any, Dict, Protocol, TypeVar

from sim_core.config import (
    AppWorkerConfig,
    CacheConfig,
    ComponentConfig,
    DatabaseConfig,
    LoadBalancerConfig,
    TrafficPattern,
)

_ConfigT = TypeVar("_ConfigT", bound=ComponentConfig)


class PresetFactory(Protocol):
    """A preset: called with an optional node name, returns a ready config."""

    def __call__(self, name: str | None = None) -> ComponentConfig: ...

__all__ = [
    "PRESETS",
    "aws_alb",
    "aws_app_worker",
    "aws_elasticache",
    "aws_rds_small",
    "azure_app_gateway",
    "azure_app_worker",
    "azure_cache",
    "azure_sql_small",
    "gcp_lb",
    "gcp_app_worker",
    "gcp_memorystore",
    "gcp_cloud_sql_small",
]


def _preset(
    config_cls: type[_ConfigT],
    *,
    name: str,
    max_capacity: int,
    service_time: float,
    list_price_per_hour: float,
    **extra: Any,
) -> _ConfigT:
    """Build one preset config (private helper shared by all presets).

    ``list_price_per_hour`` is what the provider charges for the whole
    instance. ``ComponentConfig.cost_per_hour`` is a rate *per slot*, so the
    list price is spread over the preset's slots: the provisioned part of the
    bill for an untouched preset is then exactly its list price, and the
    metered part scales with how busy those slots are.

    ``extra`` carries subclass-specific fields (e.g. ``hit_rate`` for cache
    presets). All values are validated by the frozen Pydantic models.
    """
    return config_cls(
        name=name,
        max_capacity=max_capacity,
        service_time=service_time,
        cost_per_hour=list_price_per_hour / max_capacity,
        **extra,
    )


# ---------------------------------------------------------------------------
# AWS (us-east-1 class, on-demand list prices)
# ---------------------------------------------------------------------------


def aws_alb(name: str | None = None) -> LoadBalancerConfig:
    """AWS Application Load Balancer, small tier (us-east-1).

    Approximates an ALB at light load: ~$0.0225/h per load balancer plus
    ~$0.008/LCU-h; a $0.05/h list price covers the hourly fee and a modest
    LCU load. Mid-pack per-request latency of the three providers. Directional
    estimate, not a benchmark.
    """
    return _preset(
        LoadBalancerConfig,
        name=name or "aws-alb",
        max_capacity=200,
        service_time=0.2,
        list_price_per_hour=0.05,
    )


def aws_app_worker(name: str | None = None) -> AppWorkerConfig:
    """AWS EC2 app worker, small general-purpose tier (us-east-1).

    Approximates a t3.small/t3.medium-class instance (2 vCPU, 2-4 GiB,
    ~$0.02-0.04/h); $0.05/h is a rounded-up list price for the instance.
    Dedicated vCPUs give the most concurrent slots of the three small tiers
    (8) at the fastest per-request service (1.2s). Directional estimate, not
    a benchmark.
    """
    return _preset(
        AppWorkerConfig,
        name=name or "aws-worker",
        max_capacity=8,
        service_time=1.2,
        list_price_per_hour=0.05,
    )


def aws_elasticache(name: str | None = None) -> CacheConfig:
    """AWS ElastiCache for Redis, small tier (us-east-1).

    Approximates a cache.t3.small-class node (2 vCPU, 2 GiB, ~$0.054/h) at a
    typical 85% read hit rate. The largest of the three small cache tiers,
    so it carries the most concurrent lookups (400) at the lowest per-request
    service time (0.05s). Directional estimate, not a benchmark.
    """
    return _preset(
        CacheConfig,
        name=name or "aws-elasticache",
        max_capacity=400,
        service_time=0.05,
        list_price_per_hour=0.05,
        hit_rate=0.85,
    )


def aws_rds_small(name: str | None = None) -> DatabaseConfig:
    """AWS RDS for PostgreSQL, small single-AZ tier (us-east-1).

    Approximates a db.t3.small instance (2 vCPU, 2 GiB, ~$0.046/h). The
    largest of the three small DB tiers: most concurrent queries (12) and the
    fastest per-query service (2.5s). Directional estimate, not a benchmark.
    """
    return _preset(
        DatabaseConfig,
        name=name or "aws-rds",
        max_capacity=12,
        service_time=2.5,
        list_price_per_hour=0.05,
    )


# ---------------------------------------------------------------------------
# Azure (eastus / westeurope class, pay-as-you-go list prices)
# ---------------------------------------------------------------------------


def azure_app_gateway(name: str | None = None) -> LoadBalancerConfig:
    """Azure Application Gateway, Basic v2 tier (eastus class).

    Application Gateway bills an instance hourly fee plus LCU-hours;
    $0.08/h approximates a small gateway at light load.
    Deeper per-request L7 inspection makes it the slowest gateway per request
    of the three (0.25s). Directional estimate, not a benchmark.
    """
    return _preset(
        LoadBalancerConfig,
        name=name or "azure-app-gateway",
        max_capacity=200,
        service_time=0.25,
        list_price_per_hour=0.08,
    )


def azure_app_worker(name: str | None = None) -> AppWorkerConfig:
    """Azure burstable VM app worker, B-series (eastus class).

    Approximates a B2s v2 VM (2 vCPU, 4 GiB, ~$0.0832/h in eastus). Burst
    credits throttle sustained load, so it carries mid-pack concurrency (6)
    with the slowest per-request service of the three (1.6s). Directional
    estimate, not a benchmark.
    """
    return _preset(
        AppWorkerConfig,
        name=name or "azure-worker",
        max_capacity=6,
        service_time=1.6,
        list_price_per_hour=0.08,
    )


def azure_cache(name: str | None = None) -> CacheConfig:
    """Azure Cache for Redis, Basic C1 tier (1 GiB, eastus class).

    ~$0.033/h at a typical 85% read hit rate. The 1 GiB tier is smaller than
    cache.t3.small, so it carries fewer concurrent lookups (300) with higher
    per-request contention (0.08s). Directional estimate, not a benchmark.
    """
    return _preset(
        CacheConfig,
        name=name or "azure-cache",
        max_capacity=300,
        service_time=0.08,
        list_price_per_hour=0.03,
        hit_rate=0.85,
    )


def azure_sql_small(name: str | None = None) -> DatabaseConfig:
    """Azure SQL Database, Basic S0 tier (eastus class).

    ~$0.04-0.05/h. S0's hard 10-DTU transaction ceiling makes per-query
    service the slowest of the three (3.5s), though 4 GiB RAM keeps
    concurrency mid-pack (8). Directional estimate, not a benchmark.
    """
    return _preset(
        DatabaseConfig,
        name=name or "azure-sql",
        max_capacity=8,
        service_time=3.5,
        list_price_per_hour=0.05,
    )


# ---------------------------------------------------------------------------
# GCP (us-central1 class, on-demand list prices)
# ---------------------------------------------------------------------------


def gcp_lb(name: str | None = None) -> LoadBalancerConfig:
    """Google Cloud external HTTP(S) load balancer, small tier.

    ~$0.021/h for the forwarding rule + backend, plus data-processing fees;
    $0.03/h is a small-LB ballpark. The lightest per-request
    processing of the three (0.15s). Directional estimate, not a benchmark.
    """
    return _preset(
        LoadBalancerConfig,
        name=name or "gcp-lb",
        max_capacity=200,
        service_time=0.15,
        list_price_per_hour=0.03,
    )


def gcp_app_worker(name: str | None = None) -> AppWorkerConfig:
    """Google Compute Engine app worker, e2-small class (us-central1).

    e2-small is 2 *shared* vCPU + 2 GiB; cores bill ~$0.025/h each plus RAM,
    so $0.06/h for the instance. Shared vCPUs mean the fewest
    concurrent slots of the three small tiers (4). Directional estimate, not
    a benchmark.
    """
    return _preset(
        AppWorkerConfig,
        name=name or "gcp-worker",
        max_capacity=4,
        service_time=1.5,
        list_price_per_hour=0.06,
    )


def gcp_memorystore(name: str | None = None) -> CacheConfig:
    """Cloud Memorystore for Redis, Basic 256 MB tier (us-central1 class).

    ~$0.034/h at a typical 85% read hit rate. The smallest of the three small
    cache tiers (256 MB), so it carries the fewest concurrent lookups (150)
    with the most contention (0.06s). Directional estimate, not a benchmark.
    """
    return _preset(
        CacheConfig,
        name=name or "gcp-memorystore",
        max_capacity=150,
        service_time=0.06,
        list_price_per_hour=0.035,
        hit_rate=0.85,
    )


def gcp_cloud_sql_small(name: str | None = None) -> DatabaseConfig:
    """Cloud SQL for PostgreSQL, micro/Basic tier (us-central1 class).

    db-f1-micro-class (0.25 shared vCPU, 0.6 GiB, ~$0.05/h). The smallest of
    the three small DB tiers, so it carries the fewest concurrent queries
    (6); per-query service is mid-pack (3.0s). Directional estimate, not a
    benchmark.
    """
    return _preset(
        DatabaseConfig,
        name=name or "gcp-cloud-sql",
        max_capacity=6,
        service_time=3.0,
        list_price_per_hour=0.05,
    )


#: Registry of every preset, keyed by preset name. Handy for tooling
#: (e.g. a frontend dropdown or a CLI ``--preset`` flag) and for asserting
#: the library surface in tests.
PRESETS: Dict[str, PresetFactory] = {
    "aws_alb": aws_alb,
    "aws_app_worker": aws_app_worker,
    "aws_elasticache": aws_elasticache,
    "aws_rds_small": aws_rds_small,
    "azure_app_gateway": azure_app_gateway,
    "azure_app_worker": azure_app_worker,
    "azure_cache": azure_cache,
    "azure_sql_small": azure_sql_small,
    "gcp_lb": gcp_lb,
    "gcp_app_worker": gcp_app_worker,
    "gcp_memorystore": gcp_memorystore,
    "gcp_cloud_sql_small": gcp_cloud_sql_small,
}


# ---------------------------------------------------------------------------
# Traffic presets (roadmap 3.5): a named day, laid over any base rate.
# ---------------------------------------------------------------------------

#: Named days: the shape and its peak (how many times ``base_rps``).
TRAFFIC_PRESETS: dict[str, dict[str, Any]] = {
    # A normal day, then a surge to five times the rate.
    "black_friday": {"shape": "black_friday", "shape_peak": 5.0},
    # Quiet at both ends of the window, three times busier at midday.
    "eu_business_hours": {"shape": "diurnal", "shape_peak": 3.0},
}


def traffic_preset(
    name: str, base_rps: float, duration: float, start_time: float = 0.0
) -> TrafficPattern:
    """The named day at ``base_rps`` (its quiet rate) over ``duration`` seconds.

    An unknown name raises :class:`KeyError` naming the days that exist.
    """
    if name not in TRAFFIC_PRESETS:
        raise KeyError(f"unknown traffic preset {name!r}; known: {sorted(TRAFFIC_PRESETS)}")
    window: dict[str, Any] = {"base_rps": base_rps, "duration": duration}
    if start_time:
        window["start_time"] = start_time
    return TrafficPattern(**window, **TRAFFIC_PRESETS[name])
