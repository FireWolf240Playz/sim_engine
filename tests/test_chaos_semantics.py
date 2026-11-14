"""What a chaos window does to the numbers a node reports.

Measured before these tests existed:

- A node at capacity 0 read 0% utilisation however long its queue was, so an
  outage pulled its mean utilisation *down*: a worker dead for 40 of 60 s
  read 8% (26% when healthy) and stayed "oversized".
- ``recommended_capacity`` rebuilt demand as ``utilisation x capacity`` with
  the capacity the node had when the run *ended*, not the slots in use at
  each sample, overstating demand for any window where capacity was reduced.
- A capacity drop was rounded with Python's ``round`` (ties to even): half of
  3 slots left 2, half of 5 left 2. A tie now always goes to the failure.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import simpy

from sim_core import CloudSimulator, SimulationConfig
from sim_core.config import ComponentConfig
from sim_core.topology import Component

DURATION = 60.0


def _stack(
    chaos: list[dict[str, Any]], *, service_time: float = 0.05
) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": DURATION,
            "sla_target": 1.0,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", "max_capacity": 100,
                     "service_time": 0.005},
                    {"name": "worker", "role": "worker", "max_capacity": 10,
                     "service_time": service_time},
                    {"name": "db", "role": "database", "max_capacity": 10,
                     "service_time": 0.05},
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


def _worker_failure(intensity: float, start: float, duration: float) -> list[dict[str, Any]]:
    return [
        {"event_type": "component_failure", "target": "worker", "intensity": intensity,
         "start_time": start, "duration": duration, "interval": 1000.0}
    ]


# ---------------------------------------------------------------------------
# utilisation and sizing under an outage
# ---------------------------------------------------------------------------

def test_a_dead_node_with_a_queue_reads_saturated() -> None:
    simulator = CloudSimulator(_stack(_worker_failure(1.0, 20.0, 1000.0)))
    summary = simulator.run(suggest=False)
    down = [s.per_component["worker"] for s in simulator.collector.utilization if s.time > 21.0]
    assert down and all(node["capacity"] == 0.0 for node in down)
    assert all(node["queue_length"] > 0 for node in down)
    assert all(node["utilization"] == 1.0 for node in down)

    worker = summary["component_sizing"]["worker"]
    assert worker["mean_utilization"] > 0.6  # down for two thirds of the run
    assert worker["status"] != "oversized"


def test_an_outage_never_makes_a_node_look_less_used() -> None:
    healthy = CloudSimulator(_stack([])).run(suggest=False)
    outage = CloudSimulator(_stack(_worker_failure(1.0, 20.0, 1000.0))).run(suggest=False)
    assert (
        outage["component_sizing"]["worker"]["mean_utilization"]
        > healthy["component_sizing"]["worker"]["mean_utilization"]
    )


def test_an_idle_node_that_is_down_reads_idle() -> None:
    env = simpy.Environment()
    node = Component(env, ComponentConfig(name="db", max_capacity=4, service_time=1.0))
    node.apply_capacity_drop(1.0)
    assert (node.capacity, node.in_use, node.queue_length) == (0, 0, 0)
    assert node.utilization == 0.0


def test_recommended_capacity_is_the_demand_that_was_sampled() -> None:
    """p99 of ``in_use + queue`` per sample, whatever capacity was live then."""
    config = _stack(_worker_failure(0.5, 20.0, 20.0), service_time=0.15)
    simulator = CloudSimulator(config)
    summary = simulator.run(suggest=False)
    samples = [s.per_component["worker"] for s in simulator.collector.utilization]
    assert {node["capacity"] for node in samples} == {5.0, 10.0}  # the window was sampled
    demand = [node["in_use"] + node["queue_length"] for node in samples]
    expected = max(1, int(np.ceil(np.percentile(demand, 99))))
    assert summary["component_sizing"]["worker"]["recommended_capacity"] == expected


# ---------------------------------------------------------------------------
# how many slots a failure removes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("base", "intensity", "live"),
    [
        (1, 0.5, 0),   # a single slot cannot half fail: it fails
        (2, 0.5, 1),
        (3, 0.5, 1),   # ties go to the failure (was 2)
        (5, 0.5, 2),
        (7, 0.5, 3),   # (was 4)
        (4, 0.25, 3),
        (10, 0.15, 8),
        (4, 0.1, 4),   # under half a slot: nothing to remove
        (3, 1.0, 0),
        (3, 0.0, 3),
    ],
)
def test_capacity_drop_rounds_the_removed_slots_half_up(
    base: int, intensity: float, live: int
) -> None:
    env = simpy.Environment()
    node = Component(env, ComponentConfig(name="n", max_capacity=base, service_time=1.0))
    node.apply_capacity_drop(intensity)
    assert node.capacity == live
    node.release_capacity_drop(intensity)
    assert node.capacity == base


# ---------------------------------------------------------------------------
# when a repeating event fires
# ---------------------------------------------------------------------------

def _db_capacity_changes(event: dict[str, Any]) -> list[float]:
    config = _stack([{"event_type": "component_failure", "target": "db",
                      "intensity": 1.0, **event}])
    simulator = CloudSimulator(config)
    simulator.run(suggest=False)
    series = [(s.time, s.per_component["db"]["capacity"]) for s in simulator.collector.utilization]
    return [
        t for (t, now), (_, before) in zip(series[1:], series[:-1], strict=True) if now != before
    ]


def test_a_repeating_event_rests_for_interval_after_each_window() -> None:
    """``duration`` down, then ``interval`` up: the cycle is their sum."""
    changes = _db_capacity_changes({"start_time": 5.0, "duration": 3.0, "interval": 10.0})
    assert changes[:6] == [5.0, 8.0, 18.0, 21.0, 31.0, 34.0]


def test_without_a_duration_the_window_lasts_one_interval() -> None:
    changes = _db_capacity_changes({"start_time": 10.0, "interval": 10.0})
    assert changes == [10.0, 20.0, 30.0, 40.0, 50.0]
