"""Roadmap 1.3 acceptance contract: right-sizing suggestions ("fix it").

Run with::

    pytest tests/test_suggestions.py -q

This file is the spec for the implementation; the task card is
``.agents/tasks/1.3-right-sizing.md``. Make these pass — do not edit them.

Rules pinned here (decided 2026-10-08):

- Only ``max_capacity`` in v1, driven by ``component_sizing[node]``.
- undersized → ``max(current + 1, ceil(current * mean_util / 0.8))``: size
  so the observed load sits at 80%. ``recommended_capacity`` is NOT the
  proposal — it is the p99 of in-use + *queued* demand, so a saturated
  node's backlog inflates it (a 2-slot worker reads 138).
- oversized → one 25% step down, ``max(1, floor(current * 0.75))``, only
  when it is a real cut and the predicted utilisation
  ``mean_util * current / proposed`` stays at or under 0.8. Repeated
  apply-and-re-run converges; nothing ever flips to undersized.
- right_sized → nothing.
- Order: raises first, then cuts; node name within each group.
- ``est_monthly_delta`` = ``cost_per_hour * (proposed - current) * 720``
  (the provisioned slots only — metered cost does not scale with
  capacity), ``None`` when the node has no rate.
- ``summary()["suggestions"]`` carries the list, so the CLI, every API
  run and every multi-seed run get it with no API shape change.

Revised 2026-10-09 (1.3d): the rules above are now the *flagging* formula
(``build_suggestions``, still pinned below). The sizes the summary carries
come from ``sim_core.rightsize.right_size``, which proves each one by
re-simulating on the same seed: a raise is the smallest size that clears
"undersized"; a cut is the smallest size that adds no crit/warn finding and
costs at most 0.5 score points in total. One apply lands on the end state.
The formula's 6 → 4 cut breached the demo's SLA; that is the bug this pins.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from sim_core import CloudSimulator, SimulationConfig
from sim_core.cli import main
from sim_core.cli.sim import default_config
from sim_core.compare import set_path
from sim_core.playbooks import get_playbook
from sim_core.rightsize import SCORE_TOLERANCE, right_size
from sim_core.score import capped_band
from sim_core.suggestions import build_suggestions

SUGGESTION_KEYS = {"node", "param", "current", "proposed", "reason", "est_monthly_delta"}


def _node(name: str, capacity: int, rate: float = 0.0) -> dict[str, Any]:
    return {
        "name": name,
        "role": "worker",
        "max_capacity": capacity,
        "service_time": 1.0,
        "cost_per_hour": rate,
    }


def _sizing(status: str, mean_util: float, recommended: int = 99) -> dict[str, Any]:
    return {
        "status": status,
        "mean_utilization": mean_util,
        "p95_utilization": min(1.0, mean_util + 0.1),
        "p95_queue": 0.0,
        "recommended_capacity": recommended,
    }


def _summary(**sizing: dict[str, Any]) -> dict[str, Any]:
    return {"component_sizing": sizing}


def _one(nodes: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    suggestions = build_suggestions(nodes, summary)
    assert len(suggestions) == 1, suggestions
    return dict(suggestions[0])


# ---------------------------------------------------------------------------
# fixed-dict rules
# ---------------------------------------------------------------------------

def test_undersized_raises_to_80_percent_target() -> None:
    s = _one([_node("worker", 6)], _summary(worker=_sizing("undersized", 0.92, recommended=62)))
    assert s["node"] == "worker"
    assert s["param"] == "max_capacity"
    assert s["current"] == 6
    assert s["proposed"] == 7  # ceil(6 * 0.92 / 0.8) = ceil(6.9)


def test_undersized_ignores_backlog_inflated_recommended_capacity() -> None:
    # The measured case from the default config with the worker at 2 slots.
    s = _one([_node("worker", 2)], _summary(worker=_sizing("undersized", 0.97, recommended=138)))
    assert s["proposed"] == 3  # ceil(2 * 0.97 / 0.8) = ceil(2.425)


def test_undersized_always_raises_by_at_least_one_slot() -> None:
    # An inconsistent dict (status says undersized, util says otherwise) must
    # still never produce a cut or a no-op for an undersized node.
    s = _one([_node("worker", 4)], _summary(worker=_sizing("undersized", 0.5)))
    assert s["proposed"] == 5


def test_oversized_steps_down_one_quarter() -> None:
    s = _one([_node("lb", 12)], _summary(lb=_sizing("oversized", 0.10, recommended=8)))
    assert (s["current"], s["proposed"]) == (12, 9)


def test_oversized_step_floors() -> None:
    s = _one([_node("cache", 5)], _summary(cache=_sizing("oversized", 0.30)))
    assert s["proposed"] == 3  # floor(5 * 0.75) = floor(3.75)


def test_oversized_cut_that_would_overload_is_dropped() -> None:
    # 2 -> 1 slot at 50% mean utilisation predicts 100%: never suggest it.
    assert build_suggestions([_node("db", 2)], _summary(db=_sizing("oversized", 0.5))) == []


def test_oversized_cut_within_target_is_kept() -> None:
    # 4 -> 3 at 45% predicts 60%: fine.
    s = _one([_node("db", 4)], _summary(db=_sizing("oversized", 0.45)))
    assert s["proposed"] == 3


def test_oversized_single_slot_has_nothing_to_cut() -> None:
    assert build_suggestions([_node("db", 1)], _summary(db=_sizing("oversized", 0.05))) == []


def test_right_sized_yields_nothing() -> None:
    assert build_suggestions([_node("db", 4)], _summary(db=_sizing("right_sized", 0.7))) == []


def test_order_is_raises_then_cuts_then_name() -> None:
    nodes = [_node("z-db", 2), _node("a-cache", 8), _node("m-worker", 3), _node("b-lb", 4)]
    summary = _summary(
        **{
            "z-db": _sizing("undersized", 0.95),
            "a-cache": _sizing("oversized", 0.1),
            "m-worker": _sizing("undersized", 0.9),
            "b-lb": _sizing("right_sized", 0.6),
        }
    )
    assert [s["node"] for s in build_suggestions(nodes, summary)] == ["m-worker", "z-db", "a-cache"]


def test_shape_is_exact() -> None:
    s = _one([_node("worker", 6)], _summary(worker=_sizing("undersized", 0.92)))
    assert set(s) == SUGGESTION_KEYS
    assert isinstance(s["current"], int) and isinstance(s["proposed"], int)
    assert isinstance(s["reason"], str) and s["reason"].strip()


def test_reason_quotes_the_observed_utilisation() -> None:
    s = _one([_node("worker", 6)], _summary(worker=_sizing("undersized", 0.92)))
    assert "92%" in s["reason"]


def test_monthly_delta_prices_the_provisioned_slots() -> None:
    up = _one([_node("worker", 6, rate=0.5)], _summary(worker=_sizing("undersized", 0.92)))
    assert up["est_monthly_delta"] == pytest.approx(0.5 * (7 - 6) * 720)
    down = _one([_node("lb", 12, rate=0.1)], _summary(lb=_sizing("oversized", 0.10)))
    assert down["est_monthly_delta"] == pytest.approx(0.1 * (9 - 12) * 720)
    assert down["est_monthly_delta"] < 0


def test_monthly_delta_is_none_without_a_rate() -> None:
    no_rate = _node("worker", 6)
    del no_rate["cost_per_hour"]
    for node in (no_rate, _node("worker", 6, rate=0.0)):
        s = _one([node], _summary(worker=_sizing("undersized", 0.92)))
        assert s["est_monthly_delta"] is None


def test_deterministic_and_pure() -> None:
    nodes = [_node("worker", 2, rate=0.2), _node("lb", 12)]
    summary = _summary(worker=_sizing("undersized", 0.97), lb=_sizing("oversized", 0.1))
    nodes_before, summary_before = copy.deepcopy(nodes), copy.deepcopy(summary)
    first = build_suggestions(nodes, summary)
    assert build_suggestions(nodes, summary) == first
    assert nodes == nodes_before and summary == summary_before


@pytest.mark.parametrize(
    "summary",
    [{}, {"component_sizing": None}, {"component_sizing": {}}, _summary(worker="garbage")],  # type: ignore[arg-type]
)
def test_degrades_to_empty(summary: dict[str, Any]) -> None:
    assert build_suggestions([_node("worker", 2)], summary) == []


def test_sizing_for_an_unknown_node_is_skipped() -> None:
    summary = _summary(ghost=_sizing("undersized", 0.95), worker=_sizing("undersized", 0.95))
    assert [s["node"] for s in build_suggestions([_node("worker", 2)], summary)] == ["worker"]


# ---------------------------------------------------------------------------
# engine: summary() carries the list, and applying it converges
# ---------------------------------------------------------------------------

def _squeezed() -> SimulationConfig:
    """The default config with the worker and db starved (measured: the worker
    reads undersized at ~97% with a recommended_capacity of 138)."""
    config = set_path(default_config(), "app-worker.max_capacity", 2)
    return set_path(config, "postgres.max_capacity", 1)


def _nodes(config: SimulationConfig) -> list[dict[str, Any]]:
    return [node.model_dump(mode="json") for node in config.topology.nodes]


def _apply(config: SimulationConfig, suggestions: list[Any], *, raises: bool) -> SimulationConfig:
    for s in suggestions:
        if (s["proposed"] > s["current"]) == raises:
            config = set_path(config, f"{s['node']}.{s['param']}", s["proposed"])
    return config


def _demo() -> SimulationConfig:
    """Mirror of ``web/src/core/lib/demo.ts`` (the calibrated clean run)."""
    nodes = [
        ("lb", "load_balancer", 10, 0.3, 0.04),
        ("worker", "worker", 6, 1.0, 0.05),
        ("cache", "cache", 8, 0.2, 0.03),
        ("db", "database", 3, 3.0, 0.06),
        ("pricing_api", "external_api", 4, 1.2, 0.02),
    ]
    return SimulationConfig.model_validate(
        {
            "seed": 42,
            "duration": 60,
            "metrics_interval": 2,
            "sla_target": 10,
            "topology": {
                "nodes": [
                    {
                        "name": name,
                        "role": role,
                        "max_capacity": cap,
                        "service_time": st,
                        "cost_per_hour": rate,
                        **({"hit_rate": 0.7} if role == "cache" else {}),
                    }
                    for name, role, cap, st, rate in nodes
                ],
                "edges": [
                    {"source": "lb", "target": "worker", "probability": 1.0},
                    {"source": "worker", "target": "cache", "probability": 1.0},
                    {"source": "worker", "target": "pricing_api", "probability": 1.0},
                    {"source": "cache", "target": "db", "probability": 1.0},
                ],
            },
            "traffic": {"base_rps": 2.0, "duration": 60},
            "chaos": [],
        }
    )


def _serious(summary: dict[str, Any]) -> set[str]:
    return {
        f"{f['id']}:{f.get('node') or ''}"
        for f in summary["findings"]
        if f["severity"] in ("crit", "warn")
    }


def test_summary_carries_verified_suggestions() -> None:
    config = _squeezed()
    summary = CloudSimulator(config).run()
    assert summary["suggestions"] == right_size(config, summary)
    raise_ = next(s for s in summary["suggestions"] if s["node"] == "app-worker")
    # 2 → 8, not just past "undersized" (4): the repair phase sizes it until
    # the run's SLA breach clears too.
    assert (raise_["current"], raise_["proposed"]) == (2, 8)
    assert "verified by simulation" in raise_["reason"]


def test_candidate_runs_do_not_suggest() -> None:
    """Verification re-runs the engine; without this flag it would recurse."""
    assert "suggestions" not in CloudSimulator(_demo()).run(suggest=False)


def test_right_size_is_deterministic() -> None:
    config = _demo()
    summary = CloudSimulator(config).run(suggest=False)
    assert right_size(config, summary) == right_size(config, summary)


def test_demo_cuts_never_break_the_run() -> None:
    """The bug: the formula cut the healthy demo's worker 6 → 4 and the
    re-run breached the SLA. A verified cut adds no crit/warn finding and
    costs at most SCORE_TOLERANCE points."""
    config = _demo()
    before = CloudSimulator(config).run()
    assert before["suggestions"], "the demo is over-provisioned; expect cuts"
    applied = _apply(config, before["suggestions"], raises=False)
    after = CloudSimulator(applied).run()
    assert _serious(after) <= _serious(before)
    assert after["resilience_score"] >= before["resilience_score"] - SCORE_TOLERANCE


def test_one_apply_reaches_the_end_state() -> None:
    """No 25% staircase: one apply, and the re-run has nothing left to change."""
    for config in (_demo(), _squeezed()):
        first = CloudSimulator(config).run()
        applied = config
        for s in first["suggestions"]:
            applied = set_path(applied, f"{s['node']}.{s['param']}", s["proposed"])
        assert CloudSimulator(applied).run()["suggestions"] == []


def _applied(config: SimulationConfig, suggestions: list[Any]) -> SimulationConfig:
    for s in suggestions:
        config = set_path(config, f"{s['node']}.{s['param']}", s["proposed"])
    return config


def test_fix_repairs_an_incident_not_just_trims() -> None:
    """The second bug: under every incident the formula offered only cuts and
    the SLA breach stayed. db_failover must now get a db raise that clears it."""
    config = get_playbook("db_failover").apply(_demo())
    summary = CloudSimulator(config).run()
    assert "crit:sla_breach" in {
        f"{f['severity']}:{f['id']}" for f in summary["findings"]
    }
    raises = {s["node"]: s for s in summary["suggestions"] if s["proposed"] > s["current"]}
    assert "db" in raises
    assert "sla breach" in raises["db"]["reason"]
    rerun = CloudSimulator(_applied(config, summary["suggestions"])).run(suggest=False)
    assert not _serious(rerun)


def test_fix_outcome_matches_the_rerun() -> None:
    """fix_outcome is the verification run itself: applying the suggestions
    and re-running reproduces it exactly (same pinned seed)."""
    config = get_playbook("db_failover").apply(_demo())
    summary = CloudSimulator(config).run()
    outcome = summary["fix_outcome"]
    rerun = CloudSimulator(_applied(config, summary["suggestions"])).run(suggest=False)
    assert outcome["score_before"] == summary["resilience_score"]
    assert outcome["score_after"] == rerun["resilience_score"]
    assert outcome["band_after"] == rerun["score_explanation"]["band"]
    assert {f["id"] for f in outcome["resolved"]} == {"sla_breach", "p95_headroom"}
    assert outcome["unfixed"] == []


def test_capacity_that_cannot_help_is_reported_unfixed() -> None:
    """Honesty: when no raise clears a finding, it is listed as unfixed
    instead of being hidden or 'fixed' by a pointless raise."""
    config = get_playbook("dependency_timeout_cascade").apply(_demo())
    outcome = CloudSimulator(config).run()["fix_outcome"]
    assert outcome is not None
    assert "sla_breach" in {f["id"] for f in outcome["unfixed"]}
    assert outcome["score_after"] >= outcome["score_before"]


def test_healthy_run_without_changes_has_no_outcome() -> None:
    config = _demo()
    for s in CloudSimulator(config).run()["suggestions"]:
        config = set_path(config, f"{s['node']}.{s['param']}", s["proposed"])
    summary = CloudSimulator(config).run()
    assert summary["suggestions"] == []
    assert summary["fix_outcome"] is None


def test_raise_that_cannot_help_is_not_suggested() -> None:
    """A raise is only offered when some size actually clears the node."""
    calls: list[int] = []

    def never_clears(config: SimulationConfig) -> dict[str, Any]:
        calls.append(1)
        cap = next(n.max_capacity for n in config.topology.nodes if n.name == "worker")
        return {
            "component_sizing": {"worker": _sizing("undersized", 0.99)},
            "findings": [],
            "resilience_score": 50.0,
            "_cap": cap,
        }

    config = _demo()
    summary = {"component_sizing": {"worker": _sizing("undersized", 0.99)}}
    assert right_size(config, summary, simulate=never_clears) == []
    assert len(calls) < 10  # bounded search, then give up


# ---------------------------------------------------------------------------
# verdict band: never "Resilient" next to a critical finding
# ---------------------------------------------------------------------------

def test_band_is_capped_by_the_worst_finding() -> None:
    crit = [{"id": "sla_breach", "severity": "crit", "text": "x"}]
    warn = [{"id": "retry_storm", "severity": "warn", "text": "x"}]
    info = [{"id": "healthy", "severity": "info", "text": "x"}]
    assert capped_band(95.4, crit) == ("At risk", "sla_breach")
    assert capped_band(95.4, warn) == ("Solid", "retry_storm")
    assert capped_band(95.4, info) == ("Resilient", None)
    assert capped_band(95.4, crit + warn) == ("At risk", "sla_breach")
    # The cap only ever lowers: a low score keeps its own (worse) band.
    assert capped_band(40.0, crit) == ("Fragile", None)
    assert capped_band(70.0, warn) == ("Solid", None)


def test_summary_band_agrees_with_findings() -> None:
    summary = CloudSimulator(default_config()).run(suggest=False)
    assert any(f["severity"] == "crit" for f in summary["findings"])
    explanation = summary["score_explanation"]
    assert explanation["band"] in ("At risk", "Fragile")
    if explanation["band_capped_by"] is not None:
        assert explanation["band_capped_by"] in {f["id"] for f in summary["findings"]}


def test_applying_raises_clears_every_undersized_node() -> None:
    """Accept: apply & re-run makes the flagged node leave the findings."""
    config = _squeezed()
    for _ in range(6):
        summary = CloudSimulator(config).run()
        undersized = [
            name
            for name, info in summary["component_sizing"].items()
            if info["status"] == "undersized"
        ]
        if not undersized:
            break
        config = _apply(config, summary["suggestions"], raises=True)
    else:
        pytest.fail(f"still undersized after 6 apply-and-re-run rounds: {undersized}")
    assert not [f for f in summary["findings"] if f["id"] == "undersized"]


def test_applying_cuts_never_creates_an_undersized_node() -> None:
    config = default_config()
    for _ in range(10):
        summary = CloudSimulator(config).run()
        statuses = {name: info["status"] for name, info in summary["component_sizing"].items()}
        assert "undersized" not in statuses.values(), statuses
        cuts = [s for s in summary["suggestions"] if s["proposed"] < s["current"]]
        if not cuts:
            break
        config = _apply(config, cuts, raises=False)
    else:
        pytest.fail("cut suggestions never converged in 10 rounds")


# ---------------------------------------------------------------------------
# API and CLI surfaces
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _body(**overrides: Any) -> dict[str, Any]:
    config = _squeezed().model_copy(update={"duration": 20.0})
    body: dict[str, Any] = {"config": config.model_dump(mode="json")}
    body.update(overrides)
    return body


def test_api_single_run_carries_suggestions_without_new_top_level_keys(
    client: TestClient,
) -> None:
    payload = client.post("/simulate", json=_body()).json()
    # 1.1's byte-identical single-run contract (tests/test_profile.py) holds.
    assert set(payload) == {"summary", "report_png_b64", "chaos", "timeseries"}
    assert isinstance(payload["summary"]["suggestions"], list)
    assert payload["summary"]["suggestions"]


def test_api_multi_seed_carries_suggestions_in_every_run(client: TestClient) -> None:
    payload = client.post("/simulate", json=_body(n_seeds=3)).json()
    for run in payload["runs"]:
        assert isinstance(run["summary"]["suggestions"], list), run["seed"]
    assert isinstance(payload["summary"]["suggestions"], list)


def test_cli_run_prints_a_fix_it_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "squeezed.json"
    _squeezed().to_json(str(config_path))
    out_json = tmp_path / "out.json"
    rc = main(
        ["run", str(config_path), "--json", str(out_json), "--report", str(tmp_path / "r.png")]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "Fix it" in stdout
    lines = stdout.splitlines()
    for s in json.loads(out_json.read_text(encoding="utf-8"))["suggestions"]:
        expected = f"{s['current']} → {s['proposed']}"
        assert any(s["node"] in line and expected in line for line in lines), (s, stdout)


# ---------------------------------------------------------------------------
# cross-language: the frontend's YAML must load as a real SimulationConfig
# ---------------------------------------------------------------------------

FE_YAML = Path(__file__).parent / "fixtures" / "suggestions_applied.yaml"


def test_frontend_yaml_round_trips_into_a_valid_config() -> None:
    """``web/tests/suggestions.test.ts`` writes this file from
    ``configToYaml(applySuggestions(...))``. Pydantic is the validator, so a
    YAML the UI hands to a user is proven loadable by the engine."""
    if not FE_YAML.exists():
        pytest.skip("run `cd web && npm run test:unit` first; it writes " + FE_YAML.name)
    config = SimulationConfig.from_yaml(str(FE_YAML))
    capacities = {node.name: node.max_capacity for node in config.topology.nodes}
    assert capacities == {"lb": 12, "app-worker": 3, "db": 4}
    edges = [(edge.source, edge.target) for edge in config.topology.edges]
    assert edges == [("lb", "app-worker"), ("app-worker", "db")]
    assert config.chaos[0].target == "db"
    assert config.sla_target == 2.5
