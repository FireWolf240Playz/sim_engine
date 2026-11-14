"""A config says what it means, or it is rejected.

Measured before these tests existed:

- A node dict with ``role: worker`` and a ``hit_rate`` became a cache: 90% of
  its requests stopped there and never reached the nodes behind it.
- A plain ``ComponentConfig(role=cache)`` got a hit rate of 1.0, so nothing
  ever passed it.
- A misspelt field (``hit_rte``, ``max_capcity`` next to a valid one, a
  ``duraton`` on the traffic block) was dropped without a word and the
  default used instead.
"""

from __future__ import annotations

from typing import Any

import pytest
import simpy
from pydantic import ValidationError

from sim_core import SimulationConfig
from sim_core.config import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ComponentConfig,
    ComponentRole,
    Edge,
    GraphTopologyConfig,
    TrafficPattern,
)
from sim_core.topology import Topology

_NODE = {"name": "n", "max_capacity": 2, "service_time": 0.1}


def _graph(*nodes: Any) -> GraphTopologyConfig:
    return GraphTopologyConfig.model_validate({"nodes": list(nodes)})


# ---------------------------------------------------------------------------
# caches
# ---------------------------------------------------------------------------

def test_a_hit_rate_on_a_non_cache_role_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"hit_rate.*only.*cache"):
        _graph({**_NODE, "role": "worker", "hit_rate": 0.5})


def test_a_node_dict_keeps_the_role_it_names() -> None:
    node = _graph({**_NODE, "role": "worker"}).nodes[0]
    assert isinstance(node, AppWorkerConfig)
    assert node.role is ComponentRole.WORKER


def test_a_hit_rate_without_a_role_still_means_a_cache() -> None:
    """Older configs wrote caches this way."""
    node = _graph({**_NODE, "hit_rate": 0.5}).nodes[0]
    assert isinstance(node, CacheConfig)
    assert (node.role, node.hit_rate) == (ComponentRole.CACHE, 0.5)


def test_a_null_hit_rate_on_a_non_cache_is_ignored() -> None:
    """A client that sends every field, empty or not, is not punished."""
    node = _graph({**_NODE, "role": "worker", "hit_rate": None}).nodes[0]
    assert isinstance(node, AppWorkerConfig)


def test_a_plain_component_with_the_cache_role_becomes_a_real_cache() -> None:
    plain = ComponentConfig(role=ComponentRole.CACHE, **_NODE)
    graph = GraphTopologyConfig(nodes=[plain])
    node = graph.nodes[0]
    assert isinstance(node, CacheConfig)
    assert node.hit_rate == 0.9  # the documented default, not "always hits"
    live = Topology(simpy.Environment(), graph).component("n")
    assert live.is_cache and live.hit_rate == 0.9


def test_a_cache_config_cannot_carry_another_role() -> None:
    with pytest.raises(ValidationError, match="cache"):
        CacheConfig(role=ComponentRole.WORKER, **_NODE)


# ---------------------------------------------------------------------------
# unknown fields
# ---------------------------------------------------------------------------

def test_a_misspelt_node_field_is_rejected_by_name() -> None:
    with pytest.raises(ValidationError, match="hit_rte"):
        _graph({**_NODE, "role": "cache", "hit_rte": 0.5})
    with pytest.raises(ValidationError, match="max_capcity"):
        ComponentConfig(max_capcity=4, **_NODE)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda: TrafficPattern(base_rps=1.0, duration=5.0, duraton=9.0), "duraton"),  # type: ignore[call-arg]
        (lambda: ChaosEvent(event_type="component_failure", intensty=0.9), "intensty"),  # type: ignore[call-arg]
        (lambda: Edge(source="a", target="b", probabilty=0.5), "probabilty"),  # type: ignore[call-arg]
        (lambda: GraphTopologyConfig.model_validate({"nodes": [_NODE], "entry": "n"}), "entry"),
    ],
)
def test_a_misspelt_field_is_rejected_everywhere(build: Any, field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        build()


def test_a_misspelt_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="sla_taget"):
        SimulationConfig.model_validate(
            {
                "sla_taget": 2.0,
                "topology": {"nodes": [_NODE]},
                "traffic": {"base_rps": 1.0, "duration": 5.0},
            }
        )


def test_a_saved_config_loads_back(tmp_path: Any) -> None:
    """Strictness must not reject what the library itself writes."""
    config = SimulationConfig.model_validate(
        {
            "seed": 3,
            "topology": {
                "nodes": [
                    {**_NODE, "name": "lb", "role": "load_balancer"},
                    {**_NODE, "name": "cache", "role": "cache", "hit_rate": 0.7},
                ],
                "edges": [{"source": "lb", "target": "cache"}],
            },
            "traffic": {"base_rps": 1.0, "duration": 5.0},
            "chaos": [{"event_type": "cache_outage", "target": "cache"}],
        }
    )
    for name, save, load in (
        ("c.yaml", config.to_yaml, SimulationConfig.from_yaml),
        ("c.json", config.to_json, SimulationConfig.from_json),
    ):
        path = str(tmp_path / name)
        save(path)
        assert load(path) == config
