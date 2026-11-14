"""Live-state rules: a restored node serves its queue, and every cache decides
for itself.

Measured before these tests existed:

- Restoring capacity did not wake the queue. SimPy re-checks a resource's
  queue only on an arrival or a release, one grant per check, so four
  requests queued behind a 4-slot node were served one at a time after the
  outage ended, and with no further traffic 238 requests sat forever next to
  10 free slots. On the demo it was most of the ``db_failover`` damage:
  81/100 with the stall, 94/100 without it.
- One hit/miss decision was shared by every cache on a request's path. Two
  caches in series (hit rates 0.5 then 0.9) sent 49.7% of requests to the
  database; the second cache's hit rate was never used. Expected: 5%.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
import simpy
from pydantic import ValidationError
from test_suggestions import _demo

from sim_core import CloudSimulator, SimulationConfig
from sim_core.config import ComponentConfig
from sim_core.playbooks import PLAYBOOKS, get_playbook
from sim_core.topology import Component

# ---------------------------------------------------------------------------
# capacity restore
# ---------------------------------------------------------------------------

def _queue_behind_an_outage(
    capacity: int, waiting: int
) -> tuple[simpy.Environment, Component, list[float]]:
    """A node in full outage from t=0 with ``waiting`` requests queued."""
    env = simpy.Environment()
    node = Component(env, ComponentConfig(name="db", max_capacity=capacity, service_time=1.0))
    granted: list[float] = []

    def user() -> Generator[Any]:
        with node.resource.request() as request:
            yield request
            granted.append(env.now)
            yield env.timeout(1.0)

    node.apply_capacity_drop(1.0)
    for _ in range(waiting):
        env.process(user())
    return env, node, granted


def test_restore_admits_the_waiting_queue_at_once() -> None:
    env, node, granted = _queue_behind_an_outage(capacity=4, waiting=4)
    env.run(until=5.0)
    assert granted == []
    node.release_capacity_drop(1.0)
    env.run(until=5.5)
    assert granted == [5.0, 5.0, 5.0, 5.0]
    assert (node.in_use, node.queue_length) == (4, 0)


def test_restore_admits_no_more_than_the_free_slots() -> None:
    env, node, granted = _queue_behind_an_outage(capacity=4, waiting=6)
    node.apply_capacity_drop(0.5)  # a longer half-capacity window, still open
    env.run(until=5.0)
    node.release_capacity_drop(1.0)
    env.run(until=5.5)
    assert node.capacity == 2
    assert granted == [5.0, 5.0]
    assert (node.in_use, node.queue_length) == (2, 4)


@pytest.mark.parametrize("playbook", [None, *PLAYBOOKS])
def test_no_node_idles_on_a_queue(playbook: str | None) -> None:
    """At every sample tick: a node with a free slot has nobody waiting."""
    config = _demo() if playbook is None else get_playbook(playbook).apply(_demo())
    simulator = CloudSimulator(config)
    simulator.run(suggest=False)
    for sample in simulator.collector.utilization:
        for name, node in sample.per_component.items():
            idle_slots = node["capacity"] - node["in_use"]
            assert not (node["queue_length"] > 0 and idle_slots > 0), (sample.time, name, node)


# ---------------------------------------------------------------------------
# caches
# ---------------------------------------------------------------------------

def _two_caches(chaos: list[dict[str, Any]] | None = None) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": 60.0,
            "topology": {
                "nodes": [
                    {"name": "edge_cache", "role": "cache", "max_capacity": 100,
                     "service_time": 0.001, "hit_rate": 0.5},
                    {"name": "app_cache", "role": "cache", "max_capacity": 100,
                     "service_time": 0.001, "hit_rate": 0.9},
                    {"name": "db", "role": "database", "max_capacity": 100,
                     "service_time": 0.01},
                ],
                "edges": [
                    {"source": "edge_cache", "target": "app_cache"},
                    {"source": "app_cache", "target": "db"},
                ],
            },
            "traffic": {"base_rps": 50.0, "duration": 60.0},
            "chaos": chaos or [],
        }
    )


def _shares(config: SimulationConfig) -> tuple[float, float, dict[str, Any]]:
    """(share reaching app_cache, share reaching db, summary)."""
    simulator = CloudSimulator(config)
    summary = simulator.run(suggest=False)
    records = [r for r in simulator.collector.requests if r.success]
    reached = lambda node: sum(1 for r in records if node in r.component_times) / len(records)  # noqa: E731
    return reached("app_cache"), reached("db"), summary


def test_each_cache_decides_from_its_own_hit_rate() -> None:
    app_cache, db, summary = _shares(_two_caches())
    assert app_cache == pytest.approx(0.5, abs=0.03)
    assert db == pytest.approx(0.05, abs=0.015)  # 0.5 miss x 0.1 miss
    # the request's cache_hit is the first decision on its path
    assert summary["cache_hit_rate"] == pytest.approx(0.5, abs=0.03)


def test_cache_outage_hits_the_cache_it_names() -> None:
    outage = [{"event_type": "cache_outage", "target": "app_cache", "intensity": 1.0,
               "duration": 1000.0, "interval": 1000.0}]
    app_cache, db, _ = _shares(_two_caches(outage))
    assert app_cache == pytest.approx(0.5, abs=0.03)  # the edge cache is untouched
    assert db == pytest.approx(app_cache, abs=0.002)  # every app_cache lookup misses


def test_cache_outage_without_a_target_hits_the_first_cache() -> None:
    outage = [{"event_type": "cache_outage", "intensity": 1.0,
               "duration": 1000.0, "interval": 1000.0}]
    app_cache, db, _ = _shares(_two_caches(outage))
    assert app_cache > 0.99
    assert db == pytest.approx(0.1, abs=0.02)


def test_cache_outage_aimed_at_a_non_cache_is_rejected() -> None:
    outage = [{"event_type": "cache_outage", "target": "db", "intensity": 1.0}]
    with pytest.raises(ValidationError, match="not a cache"):
        _two_caches(outage)
