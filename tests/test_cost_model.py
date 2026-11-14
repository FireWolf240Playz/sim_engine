"""The bill means what it says: a preset costs its list price, and an outage
is not a discount.

Measured before these tests existed:

- ``cost_per_hour`` is a rate per slot, but every preset stored its
  instance's hourly list price there. A small load balancer ($0.05/h, 200
  slots) was billed 200 x $0.05 = $10/h, about $7,200 a month; the app
  worker came out roughly ten times its list price.
- The provisioned part of the bill followed the *live* capacity, so taking a
  node down made the "steady" bill cheaper: the same 60 s run projected
  $86,400/month healthy and $81,480/month with the worker dead for 40 s.
"""

from __future__ import annotations

from typing import Any

import pytest

from sim_core import CloudSimulator, SimulationConfig, TopologyConfig, TrafficPattern
from sim_core.presets import (
    PRESETS,
    aws_alb,
    aws_app_worker,
    aws_elasticache,
    aws_rds_small,
)

#: Hourly list price of the whole instance each preset stands for (USD), as
#: its docstring cites it.
INSTANCE_PRICE = {
    "aws_alb": 0.05,
    "aws_app_worker": 0.05,
    "aws_elasticache": 0.05,
    "aws_rds_small": 0.05,
    "azure_app_gateway": 0.08,
    "azure_app_worker": 0.08,
    "azure_cache": 0.03,
    "azure_sql_small": 0.05,
    "gcp_lb": 0.03,
    "gcp_app_worker": 0.06,
    "gcp_memorystore": 0.035,
    "gcp_cloud_sql_small": 0.05,
}


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------

def test_every_preset_has_a_list_price() -> None:
    assert set(INSTANCE_PRICE) == set(PRESETS)


@pytest.mark.parametrize("name", sorted(INSTANCE_PRICE))
def test_an_idle_preset_bills_its_list_price(name: str) -> None:
    """Provisioned slots x the per-slot rate is the instance's hourly price."""
    preset = PRESETS[name]()
    assert preset.cost_per_hour * preset.max_capacity == pytest.approx(INSTANCE_PRICE[name])


def test_presets_accept_a_name() -> None:
    """The registry's factories take ``name`` (multi-cloud compare relies on it)."""
    for factory in PRESETS.values():
        assert factory(name="node-1").name == "node-1"


def test_an_idle_aws_stack_costs_its_list_price_per_month() -> None:
    config = SimulationConfig(
        seed=42,
        duration=30.0,
        topology=TopologyConfig(
            load_balancer=aws_alb(),
            app_worker=aws_app_worker(),
            cache=aws_elasticache(),
            database=aws_rds_small(),
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=30.0),
    )
    summary = CloudSimulator(config).run(suggest=False)
    steady = summary["steady_state_cost_extrapolation"]
    assert steady["hour"] == pytest.approx(0.20)  # four $0.05/h instances
    assert steady["month"] == pytest.approx(144.0)
    # under load the metered part adds to it, but a small stack stays small
    assert steady["month"] <= summary["cost_extrapolation"]["month"] < 2 * 144.0


# ---------------------------------------------------------------------------
# provisioned base
# ---------------------------------------------------------------------------

DURATION = 60.0
SLOTS = 100 + 10 + 10


def _priced(chaos: list[dict[str, Any]]) -> SimulationConfig:
    node = {"cost_per_hour": 1.0, "timeout": 0.5}
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": DURATION,
            "sla_target": 1.0,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", "max_capacity": 100,
                     "service_time": 0.005, **node},
                    {"name": "worker", "role": "worker", "max_capacity": 10,
                     "service_time": 0.05, **node},
                    {"name": "db", "role": "database", "max_capacity": 10,
                     "service_time": 0.05, **node},
                ],
                "edges": [
                    {"source": "lb", "target": "worker"},
                    {"source": "worker", "target": "db"},
                ],
            },
            "traffic": {"base_rps": 50.0, "duration": DURATION},
            "chaos": chaos,
        }
    )


_WORKER_DOWN = [
    {"event_type": "component_failure", "target": "worker", "intensity": 1.0,
     "start_time": 20.0, "duration": 1000.0, "interval": 1000.0}
]


def test_an_outage_does_not_lower_the_provisioned_bill() -> None:
    """You pay for the slots you sized whether or not they are up."""
    expected = 1.0 * SLOTS * DURATION / 3600.0
    healthy = CloudSimulator(_priced([])).run(suggest=False)
    outage = CloudSimulator(_priced(_WORKER_DOWN)).run(suggest=False)
    assert healthy["cost_base"] == pytest.approx(expected)
    assert outage["cost_base"] == pytest.approx(expected)
    assert (
        outage["steady_state_cost_extrapolation"]["month"]
        == pytest.approx(healthy["steady_state_cost_extrapolation"]["month"])
    )


def test_base_and_metered_still_sum_to_the_total() -> None:
    for chaos in ([], _WORKER_DOWN):
        summary = CloudSimulator(_priced(chaos)).run(suggest=False)
        assert summary["cost_metered"] >= 0.0
        assert summary["cost_base"] + summary["cost_metered"] == pytest.approx(
            summary["total_cost"]
        )
        assert sum(summary["cost_breakdown_by_component"].values()) == pytest.approx(
            summary["total_cost"]
        )
