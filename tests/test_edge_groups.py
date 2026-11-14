"""Edges that share a group are alternatives: a request takes one of them.

Edges are followed independently, which models fan-out (a worker calling two
services). It cannot model a load balancer choosing one backend, and the
Terraform importer used it for exactly that. Measured before these tests
existed, on an imported load balancer in front of two workers at 0.5 each:
25% of requests visited no worker at all and came back as fast successes,
25% visited both. The importer also linked the load balancer straight to the
cache, so every request looked the cache up twice.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from test_importers import TF_AWS_STACK, _tf_text

from sim_core import CloudSimulator, SimulationConfig
from sim_core.importers import import_architecture


def _two_workers(edges: list[dict[str, Any]]) -> SimulationConfig:
    node = {"max_capacity": 50, "service_time": 0.01}
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": 60.0,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", **node},
                    {"name": "a", "role": "worker", **node},
                    {"name": "b", "role": "worker", **node},
                ],
                "edges": edges,
            },
            "traffic": {"base_rps": 50.0, "duration": 60.0},
        }
    )


def _visits(config: SimulationConfig, nodes: tuple[str, ...]) -> dict[int, float]:
    """Share of requests by how many of ``nodes`` they visited."""
    simulator = CloudSimulator(config)
    simulator.run(suggest=False)
    records = [r for r in simulator.collector.requests if r.success]
    counts: dict[int, int] = {}
    for record in records:
        visited = sum(1 for name in nodes if name in record.component_times)
        counts[visited] = counts.get(visited, 0) + 1
    return {visited: n / len(records) for visited, n in counts.items()}


def _split(group: str | None, pa: float, pb: float) -> list[dict[str, Any]]:
    extra = {} if group is None else {"group": group}
    return [
        {"source": "lb", "target": "a", "probability": pa, **extra},
        {"source": "lb", "target": "b", "probability": pb, **extra},
    ]


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

def test_grouped_edges_send_each_request_to_exactly_one_target() -> None:
    config = _two_workers(_split("pool", 0.5, 0.5))
    assert _visits(config, ("a", "b")) == {1: 1.0}
    assert _visits(config, ("a",))[1] == pytest.approx(0.5, abs=0.03)


def test_grouped_edges_follow_their_weights() -> None:
    assert _visits(_two_workers(_split("pool", 0.8, 0.2)), ("a",))[1] == pytest.approx(
        0.8, abs=0.03
    )


def test_a_group_that_sums_under_one_leaves_the_rest_untaken() -> None:
    shares = _visits(_two_workers(_split("pool", 0.3, 0.3)), ("a", "b"))
    assert set(shares) == {0, 1}
    assert shares[0] == pytest.approx(0.4, abs=0.03)


def test_ungrouped_edges_stay_independent() -> None:
    """Fan-out, unchanged: both, one or neither."""
    shares = _visits(_two_workers(_split(None, 0.5, 0.5)), ("a", "b"))
    assert shares[0] == pytest.approx(0.25, abs=0.03)
    assert shares[2] == pytest.approx(0.25, abs=0.03)


def test_a_group_and_a_fan_out_edge_can_leave_the_same_node() -> None:
    node = {"max_capacity": 50, "service_time": 0.01}
    config = _two_workers(_split("pool", 0.5, 0.5))
    data = config.model_dump(mode="json")
    data["topology"]["nodes"].append({"name": "audit", "role": "generic", **node})
    data["topology"]["edges"].append({"source": "lb", "target": "audit", "probability": 1.0})
    mixed = SimulationConfig.model_validate(data)
    assert _visits(mixed, ("a", "b")) == {1: 1.0}
    assert _visits(mixed, ("audit",)) == {1: 1.0}


def test_a_group_whose_weights_exceed_one_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"group 'pool'.*sum to 1\.2"):
        _two_workers(_split("pool", 0.7, 0.5))


def test_a_group_survives_a_round_trip() -> None:
    config = _two_workers(_split("pool", 0.5, 0.5))
    again = SimulationConfig.model_validate(config.model_dump(mode="json"))
    assert again == config
    assert {edge.group for edge in again.topology.edges} == {"pool"}


# ---------------------------------------------------------------------------
# the Terraform importer
# ---------------------------------------------------------------------------

def test_an_imported_load_balancer_picks_one_worker_per_request() -> None:
    result = import_architecture(_tf_text(TF_AWS_STACK), filename="tf.json")
    config = result.config
    to_workers = [e for e in config.topology.edges if e.source == "web"]
    assert {e.target for e in to_workers} == {"api", "billing"}
    assert len({e.group for e in to_workers}) == 1 and to_workers[0].group is not None
    assert [e.probability for e in to_workers] == [0.5, 0.5]

    runnable = config.model_copy(update={"seed": 7, "duration": 60.0})
    assert _visits(runnable, ("api", "billing")) == {1: 1.0}
    assert any("exactly one" in line for line in result.report.assumptions)


def test_an_imported_load_balancer_reaches_the_cache_through_a_worker() -> None:
    """One lookup per request: the balancer does not also call the cache."""
    config = import_architecture(_tf_text(TF_AWS_STACK), filename="tf.json").config
    assert ("web", "session") not in {(e.source, e.target) for e in config.topology.edges}
    simulator = CloudSimulator(config.model_copy(update={"seed": 7, "duration": 60.0}))
    summary = simulator.run(suggest=False)
    assert summary["cache_hit_rate"] == pytest.approx(0.8, abs=0.08)
