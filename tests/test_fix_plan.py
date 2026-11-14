"""What "Fix it" costs to compute, and which run it describes.

Measured before these tests existed (demo, 60 s, seed 42):

- Every ``run()`` re-simulated the run it had just finished as the plan's
  "before", then tried doubling every node at every repair step, including
  nodes nobody had ever waited for, where more slots cannot change one event.
  The demo and its four incidents cost 152 verification runs between them;
  a 19-node example topology took 240 runs and 21.6 s for one ``run()``,
  against a docstring promising "well under half a second".
- With no seed in the config the shown run drew a seed from entropy while the
  plan verified on seed 42: ``fix_outcome.score_before`` read 77.0 next to a
  shown score of 77.7, 77.3, 78.2 on three tries.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_suggestions import _demo

import sim_core.rightsize as rightsize
from sim_core import CloudSimulator, SimulationConfig
from sim_core.playbooks import PLAYBOOKS, get_playbook

SCENARIOS = [None, *PLAYBOOKS]


def _scenario(playbook: str | None) -> SimulationConfig:
    return _demo() if playbook is None else get_playbook(playbook).apply(_demo())


def _sizes(config: SimulationConfig) -> tuple[int, ...]:
    return tuple(node.max_capacity for node in config.topology.nodes)


@pytest.fixture
def simulated(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, ...]]:
    """The sizing of every verification run the plan asks for, in order."""
    seen: list[tuple[int, ...]] = []
    real = rightsize._simulate

    def recording(config: SimulationConfig) -> dict[str, Any]:
        seen.append(_sizes(config))
        return real(config)

    monkeypatch.setattr(rightsize, "_simulate", recording)
    return seen


# ---------------------------------------------------------------------------
# no wasted runs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("playbook", SCENARIOS)
def test_the_shown_run_is_not_simulated_again(
    playbook: str | None, simulated: list[tuple[int, ...]]
) -> None:
    config = _scenario(playbook)
    summary = CloudSimulator(config).run()
    assert summary["suggestions"] and simulated  # the plan ran, and was seen
    assert _sizes(config) not in simulated
    assert len(simulated) == len(set(simulated))  # and no sizing twice


def test_the_plan_stays_inside_its_budget(simulated: list[tuple[int, ...]]) -> None:
    """Verification runs for the clean demo and its four incidents together:
    152 before the shown run was reused and runs that cannot differ were
    skipped, 116 after. The margin is for a search that changes its path."""
    for playbook in SCENARIOS:
        CloudSimulator(_scenario(playbook)).run()
    assert 0 < len(simulated) <= 130


def test_waited_requests_counts_the_acquisitions_that_queued() -> None:
    config = SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": 30.0,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", "max_capacity": 100,
                     "service_time": 0.01},
                    {"name": "worker", "role": "worker", "max_capacity": 1,
                     "service_time": 0.5},
                ],
                "edges": [{"source": "lb", "target": "worker"}],
            },
            "traffic": {"base_rps": 5.0, "duration": 30.0},
        }
    )
    sizing = CloudSimulator(config).run(suggest=False)["component_sizing"]
    assert sizing["lb"]["waited_requests"] == 0
    assert sizing["worker"]["waited_requests"] > 50


@pytest.mark.parametrize("playbook", SCENARIOS)
def test_raising_a_node_nobody_waited_for_changes_nothing(playbook: str | None) -> None:
    """The fact the repair phase relies on to skip a node: with no wait at
    this size, a larger size replays the same events."""
    config = _scenario(playbook)
    reference = CloudSimulator(config).run(suggest=False)
    idle = [
        name for name, info in reference["component_sizing"].items()
        if info["waited_requests"] == 0
    ]
    assert idle, "the demo's load balancer never queues"
    for name in idle:
        nodes = [
            n.model_copy(update={"max_capacity": n.max_capacity * 2}) if n.name == name else n
            for n in config.topology.nodes
        ]
        topology = config.topology.model_copy(update={"nodes": nodes})
        raised = CloudSimulator(config.model_copy(update={"topology": topology})).run(
            suggest=False
        )
        for key in ("requests", "failed_requests", "sla_compliance", "p50_latency",
                    "p95_latency", "p99_latency", "total_retries", "resilience_score"):
            assert raised[key] == reference[key], (name, key)
        assert [f["id"] for f in raised["findings"]] == [f["id"] for f in reference["findings"]]


# ---------------------------------------------------------------------------
# an unseeded run
# ---------------------------------------------------------------------------

def _unseeded() -> SimulationConfig:
    return get_playbook("db_failover").apply(_demo()).model_copy(update={"seed": None})


def test_an_unseeded_run_is_verified_on_its_own_seed() -> None:
    simulator = CloudSimulator(_unseeded())
    summary = simulator.run()
    outcome = summary["fix_outcome"]
    assert outcome is not None
    assert outcome["seed"] == simulator.seed
    assert outcome["score_before"] == summary["resilience_score"]
    assert f"seed {simulator.seed})" in summary["suggestions"][0]["reason"]


def test_an_unseeded_run_can_be_replayed_from_its_seed() -> None:
    first = CloudSimulator(_unseeded())
    summary = first.run(suggest=False)
    replay = CloudSimulator(_unseeded().model_copy(update={"seed": first.seed}))
    assert replay.run(suggest=False) == summary
    assert CloudSimulator(_unseeded()).seed != first.seed  # still drawn fresh each time


def test_a_seeded_run_keeps_its_seed() -> None:
    assert CloudSimulator(_demo()).seed == 42
