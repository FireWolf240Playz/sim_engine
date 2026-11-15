"""Fix it converges: one apply, the re-run is the prediction, nothing left to change.

Measured before these tests (the demo, seed 42, 2026-11-15):

- 5-seed mode verified each plan on the typical seed alone. The re-run's
  typical seed moved (44 → 46 → 44 → 45 → 42 → 44 under
  cross_region_latency_spike), so no prediction held and the plans undid
  each other: db 3 → 4 → 3, lb 10 → 5 → 4 → 3 → 4 → 3, six applies and no
  end state.
- db_failover under ramp traffic stopped at 66.5 with the SLA breached and
  reported it "not fixable by capacity". Every single raise also brought a
  p95 headroom warning, so each was refused; worker 12, pricing_api 8 and
  db 12 together score 98.6 with no finding at all.
- Under diurnal traffic one apply was not the end state: the cuts made after
  the raises opened room for another raise, so the re-run offered more
  (cache_eviction_storm: 15.7 → 90.4, then a second plan to 97.1).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_suggestions import _demo

from api.main import create_app
from sim_core import CloudSimulator, SimulationConfig, typical_index
from sim_core.playbooks import get_playbook
from sim_core.rightsize import plan_fix_seeds

SEEDS = [42, 43, 44, 45, 46]
INCIDENTS = [
    "db_failover",
    "cross_region_latency_spike",
    "cache_eviction_storm",
    "dependency_timeout_cascade",
]
SHAPES = ["flat", "ramp", "diurnal", "black_friday"]


def _scenario(playbook: str | None, shape: str = "flat") -> SimulationConfig:
    config = _demo()
    if shape != "flat":
        traffic = config.traffic.model_copy(update={"shape": shape})
        config = config.model_copy(update={"traffic": traffic})
    return config if playbook is None else get_playbook(playbook).apply(config)


def _applied(config: SimulationConfig, suggestions: list[Any]) -> SimulationConfig:
    proposed = {s["node"]: s["proposed"] for s in suggestions}
    nodes = [
        n.model_copy(update={"max_capacity": proposed[n.name]}) if n.name in proposed else n
        for n in config.topology.nodes
    ]
    topology = config.topology.model_copy(update={"nodes": nodes})
    return config.model_copy(update={"topology": topology})


def _serious(summary: dict[str, Any]) -> set[str]:
    return {
        f"{f['id']}@{f.get('node') or ''}"
        for f in summary["findings"]
        if f["severity"] in ("crit", "warn")
    }


def _runs(config: SimulationConfig) -> list[dict[str, Any]]:
    """The page's 5-seed run: one summary per seed, no plan yet."""
    return [
        CloudSimulator(config.model_copy(update={"seed": seed})).run(suggest=False)
        for seed in SEEDS
    ]


# ---------------------------------------------------------------------------
# single seed: every incident under every traffic shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("playbook", INCIDENTS)
def test_one_apply_is_the_end_state(playbook: str, shape: str) -> None:
    """Apply everything once: the re-run scores what the plan said and has
    nothing left to change."""
    config = _scenario(playbook, shape)
    first = CloudSimulator(config).run()
    if not first["suggestions"]:
        pytest.skip("nothing to change")
    rerun = CloudSimulator(_applied(config, first["suggestions"])).run()
    assert rerun["resilience_score"] == first["fix_outcome"]["score_after"]
    assert rerun["suggestions"] == []


def test_a_raise_may_bring_a_warning_on_its_way_to_the_fix() -> None:
    """db_failover under ramp is fixable by capacity: the plan must find it,
    not stop at the breach and call it unfixable."""
    config = _scenario("db_failover", "ramp")
    summary = CloudSimulator(config).run()
    outcome = summary["fix_outcome"]
    assert "sla_breach" in {f["id"] for f in outcome["resolved"]}
    assert not [f for f in outcome["unfixed"] if f["severity"] == "crit"]
    assert outcome["score_after"] >= 90
    rerun = CloudSimulator(_applied(config, summary["suggestions"])).run(suggest=False)
    assert not [f for f in rerun["findings"] if f["severity"] == "crit"]


def test_the_plan_never_ends_worse_than_it_started() -> None:
    for playbook in INCIDENTS:
        for shape in SHAPES:
            outcome = CloudSimulator(_scenario(playbook, shape)).run()["fix_outcome"]
            if outcome is None:
                continue
            assert outcome["score_after"] >= outcome["score_before"], (playbook, shape)


# ---------------------------------------------------------------------------
# 5 seeds: one plan, verified on every seed the page shows
# ---------------------------------------------------------------------------

MULTI = [
    ("cross_region_latency_spike", "flat"),
    ("cross_region_latency_spike", "ramp"),
    ("db_failover", "ramp"),
]


@pytest.mark.parametrize(("playbook", "shape"), MULTI)
def test_five_seed_apply_lands_where_the_plan_said(playbook: str, shape: str) -> None:
    config = _scenario(playbook, shape)
    plan = plan_fix_seeds(config, SEEDS, _runs(config))
    outcome = plan["outcome"]
    assert plan["suggestions"] and outcome is not None
    assert outcome["seeds"] == SEEDS

    applied = _applied(config, plan["suggestions"])
    after = _runs(applied)
    # The headline after the apply is the typical run of the same five seeds.
    assert after[typical_index(after)]["resilience_score"] == outcome["score_after"]
    assert plan_fix_seeds(applied, SEEDS, after)["suggestions"] == []


def test_five_seed_score_before_is_the_headline() -> None:
    config = _scenario("cross_region_latency_spike")
    runs = _runs(config)
    outcome = plan_fix_seeds(config, SEEDS, runs)["outcome"]
    assert outcome is not None
    assert outcome["score_before"] == runs[typical_index(runs)]["resilience_score"]


def test_a_five_seed_cut_is_free_on_every_seed() -> None:
    """The clean demo only trims. No seed may gain a crit/warn finding."""
    config = _demo()
    before = _runs(config)
    plan = plan_fix_seeds(config, SEEDS, before)
    assert plan["suggestions"], "the demo is over-provisioned; expect cuts"
    assert all(s["proposed"] < s["current"] for s in plan["suggestions"])
    after = _runs(_applied(config, plan["suggestions"]))
    for seed, b, a in zip(SEEDS, before, after, strict=True):
        assert _serious(a) <= _serious(b), seed


def test_five_seed_reasons_name_every_seed() -> None:
    config = _scenario("db_failover", "ramp")
    plan = plan_fix_seeds(config, SEEDS, _runs(config))
    for s in plan["suggestions"]:
        assert "seeds 42–46" in s["reason"], s["reason"]  # noqa: RUF001


def test_one_seed_reasons_keep_their_seed() -> None:
    summary = CloudSimulator(_scenario("db_failover")).run()
    assert summary["fix_outcome"]["seeds"] == [42]
    for s in summary["suggestions"]:
        assert "seed 42)" in s["reason"], s["reason"]


# ---------------------------------------------------------------------------
# the API: the page's 5-seed apply → re-run, end to end
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _body(config: SimulationConfig, playbook: str) -> dict[str, Any]:
    return {"config": config.model_dump(mode="json"), "playbook": playbook, "n_seeds": 5}


def test_api_five_seed_rerun_matches_the_plan(client: TestClient) -> None:
    config = _scenario(None, "ramp")
    first = client.post("/simulate", json=_body(config, "cross_region_latency_spike")).json()
    summary = first["summary"]
    assert summary["suggestions"]
    assert summary["fix_outcome"]["seeds"] == SEEDS
    # One plan for the whole run: every seed carries the same one.
    for run in first["runs"]:
        assert run["summary"]["suggestions"] == summary["suggestions"], run["seed"]

    applied = _applied(config, summary["suggestions"])
    second = client.post("/simulate", json=_body(applied, "cross_region_latency_spike")).json()
    assert second["summary"]["resilience_score"] == summary["fix_outcome"]["score_after"]
    assert second["summary"]["suggestions"] == []


def test_api_runs_stream_carries_the_same_plan(client: TestClient) -> None:
    config = _scenario(None, "ramp")
    body = _body(config, "db_failover")
    simulated = client.post("/simulate", json=body).json()
    started = client.post("/runs", json=body)
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    with client.stream("GET", f"/runs/{run_id}/events") as stream:
        "".join(stream.iter_text())  # to the end: the run has finished
    result = client.get(f"/runs/{run_id}").json()["result"]
    assert result["summary"]["suggestions"] == simulated["summary"]["suggestions"]
    assert result["summary"]["fix_outcome"] == simulated["summary"]["fix_outcome"]
