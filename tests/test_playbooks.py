"""Incident playbook tests (Phase 1): aimed chaos, live state, CLI surface.

Run with::

    pytest tests/test_playbooks.py

Deterministic: fixed seeds, and live-state assertions use the same
"run inside the window, continue past it" pattern as the chaos tests in
test_features.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sim_core.cli import main

from sim_core import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ChaosEventType,
    CloudSimulator,
    ComponentConfig,
    ComponentRole,
    DatabaseConfig,
    Edge,
    GraphTopologyConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
)
from sim_core.playbooks import PLAYBOOKS, get_playbook, list_playbooks


def _full_config() -> SimulationConfig:
    """Legacy 4-node topology (lb -> worker -> cache -> db), no chaos."""
    return SimulationConfig(
        seed=7,
        duration=70.0,
        metrics_interval=2.0,
        sla_target=20.0,
        topology=TopologyConfig(
            load_balancer=LoadBalancerConfig(name="lb", max_capacity=12, service_time=0.5),
            app_worker=AppWorkerConfig(name="worker", max_capacity=6, service_time=2.0),
            cache=CacheConfig(name="redis", max_capacity=8, service_time=0.3, hit_rate=0.85),
            database=DatabaseConfig(name="pg", max_capacity=4, service_time=5.0),
        ),
        traffic=TrafficPattern(base_rps=1.5, duration=70.0),
        chaos=[],
    )


# ---------------------------------------------------------------------------
# 1. Registry shape
# ---------------------------------------------------------------------------
def test_registry_has_the_four_built_in_playbooks() -> None:
    assert set(PLAYBOOKS) == {
        "db_failover",
        "cross_region_latency_spike",
        "cache_eviction_storm",
        "dependency_timeout_cascade",
    }
    for playbook in list_playbooks():
        assert playbook.name in PLAYBOOKS
        assert playbook.description


def test_unknown_playbook_raises_clear_error() -> None:
    with pytest.raises(KeyError, match="unknown playbook"):
        get_playbook("nope")
    # The error message must list the available names (helps sales demos).
    with pytest.raises(KeyError, match="db_failover"):
        get_playbook("nope")


# ---------------------------------------------------------------------------
# 2. apply(): valid targets, existing chaos preserved
# ---------------------------------------------------------------------------
def test_every_playbook_applies_to_a_full_topology() -> None:
    base = _full_config()
    names = {node.name for node in base.topology.nodes}
    for name in PLAYBOOKS:
        patched = get_playbook(name).apply(base)
        assert isinstance(patched, SimulationConfig)
        added = patched.chaos[len(base.chaos) :]
        assert added, "every playbook must inject at least one chaos event"
        for event in added:
            if event.target is not None:
                assert event.target in names, "targeted events must name a real node"


def test_apply_preserves_existing_chaos() -> None:
    base = _full_config().model_copy(
        update={
            "chaos": [
                ChaosEvent(
                    event_type=ChaosEventType.NETWORK_LATENCY,
                    intensity=0.3,
                    start_time=5.0,
                )
            ]
        }
    )
    patched = get_playbook("db_failover").apply(base)
    assert patched.chaos[0] == base.chaos[0], "existing chaos must be kept"
    assert len(patched.chaos) == 3, "1 existing + 2 db_failover events"


# ---------------------------------------------------------------------------
# 3. Per-playbook event shape
# ---------------------------------------------------------------------------
def test_db_failover_targets_the_database_and_shapes_windows() -> None:
    added = list(get_playbook("db_failover").apply(_full_config()).chaos)
    assert all(event.target == "pg" for event in added)
    assert all(event.event_type is ChaosEventType.COMPONENT_FAILURE for event in added)
    assert added[0].intensity == 1.0 and added[0].duration == 5.0, "5 s full outage first"
    assert added[1].intensity == 0.5 and added[1].duration == 30.0, "then 30 s degraded"
    assert added[1].start_time == added[0].start_time + 5.0, "degraded starts as outage ends"


def test_dependency_cascade_falls_back_to_the_database() -> None:
    added = list(get_playbook("dependency_timeout_cascade").apply(_full_config()).chaos)
    assert added[0].event_type is ChaosEventType.COMPONENT_FAILURE
    assert added[0].target == "pg", "no external_api node -> the DB is the dependency"
    assert added[1].event_type is ChaosEventType.NETWORK_LATENCY
    assert added[1].target is None, "the retry tail slows the whole path"


def test_playbook_without_required_node_fails_loudly() -> None:
    no_db = SimulationConfig(
        seed=1,
        duration=10.0,
        topology=GraphTopologyConfig(
            nodes=[
                ComponentConfig(
                    name="lb",
                    role=ComponentRole.LOAD_BALANCER,
                    max_capacity=5,
                    service_time=0.2,
                )
            ],
        ),
        traffic=TrafficPattern(base_rps=0.5, duration=10.0),
    )
    with pytest.raises(ValueError, match="database"):
        get_playbook("db_failover").apply(no_db)


def test_bad_target_is_rejected_at_config_validation() -> None:
    with pytest.raises(ValidationError, match="unknown component"):
        SimulationConfig(
            seed=1,
            duration=10.0,
            topology=GraphTopologyConfig(
                nodes=[ComponentConfig(name="solo", max_capacity=5, service_time=0.2)]
            ),
            traffic=TrafficPattern(base_rps=0.5, duration=10.0),
            chaos=[ChaosEvent(event_type=ChaosEventType.COMPONENT_FAILURE, target="ghost")],
        )


# ---------------------------------------------------------------------------
# 4. Live state: the targeted node drops, the others don't
# ---------------------------------------------------------------------------
def test_playbook_outage_reduces_only_the_targeted_component() -> None:
    """duration=8 -> incident start = max(5, 8*0.25) = 5 s.

    So the db_failover windows are: full outage [5, 10), degraded [10, 40).
    """
    config = get_playbook("db_failover").apply(
        SimulationConfig(
            seed=3,
            duration=8.0,
            metrics_interval=4.0,
            topology=GraphTopologyConfig(
                nodes=[
                    ComponentConfig(
                        name="lb",
                        role=ComponentRole.LOAD_BALANCER,
                        max_capacity=10,
                        service_time=0.2,
                    ),
                    ComponentConfig(
                        name="pg",
                        role=ComponentRole.DATABASE,
                        max_capacity=8,
                        service_time=1.0,
                    ),
                ],
                edges=[Edge(source="lb", target="pg")],
            ),
            traffic=TrafficPattern(base_rps=1.0, duration=8.0),
            chaos=[],
        )
    )
    sim = CloudSimulator(config)
    lb, pg = sim.topology.component("lb"), sim.topology.component("pg")
    assert lb.capacity == 10 and pg.capacity == 8

    sim.run()  # ends at t=8, inside the full-outage window [5, 10)
    assert pg.capacity == 0, "the targeted DB must be fully out in-window"
    assert lb.capacity == 10, "untargeted components keep their capacity"

    sim.env.run(until=11.0)  # inside the degraded window [10, 40): half capacity
    assert pg.capacity == 4, "degraded window must hold even though the outage restore fired"

    sim.env.run(until=41.0)  # past the restore point at t=40
    assert pg.capacity == 8, "capacity must be restored after the last window ends"


# ---------------------------------------------------------------------------
# 5. CLI surface
# ---------------------------------------------------------------------------
def test_cli_playbooks_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv=["playbooks", "list"]) == 0
    out = capsys.readouterr().out
    for name in PLAYBOOKS:
        assert name in out


def test_cli_run_with_playbook_end_to_end(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _full_config()
    config_yaml = tmp_path / "topo.yaml"
    config.to_yaml(str(config_yaml))
    report = tmp_path / "report.png"
    summary = tmp_path / "summary.json"

    assert (
        main(
            argv=[
                "run",
                str(config_yaml),
                "--playbook",
                "db_failover",
                "--report",
                str(report),
                "--json",
                str(summary),
            ]
        )
        == 0
    )
    assert report.is_file() and summary.is_file()
    assert "Playbook applied: db_failover" in capsys.readouterr().out


def test_cli_demo_with_playbook(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # PNG/JSON outputs land in the temp dir
    assert main(argv=["demo", "--playbook", "cache_eviction_storm"]) == 0
    assert (tmp_path / "eleven_report.png").is_file()


def test_cli_run_with_unknown_playbook_fails_cleanly(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _full_config()
    config_yaml = tmp_path / "topo.yaml"
    config.to_yaml(str(config_yaml))
    assert main(argv=["run", str(config_yaml), "--playbook", "nope"]) == 2
    err = capsys.readouterr().err
    assert "unknown playbook" in err
    assert "db_failover" in err, "the error must list the available playbooks"
