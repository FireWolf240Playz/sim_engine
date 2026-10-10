"""Contract for 1.3h (`.agents/tasks/1.3h-verified-labels.md`): the sizing
label is the verified answer, never the utilisation guess.

Measured on the demo before this card: 9 of 25 node labels (5 nodes, clean
run plus 4 incidents) disagreed with "Fix it". Under every incident the db
read "right_sized" while Fix it raised it; the worker read "oversized" where
no cut was safe. Each ``component_sizing`` entry now carries
``verified_status`` and ``verified_capacity`` from the plan. The utilisation
``status`` stays, unchanged, as the evidence the score reads, so every
documented score stands.
Make these pass — do not edit them.
"""

from __future__ import annotations

import pytest
from test_suggestions import _demo

from sim_core import CloudSimulator
from sim_core.cli.sim import _format_summary
from sim_core.compare import diff_runs, run_one
from sim_core.playbooks import PLAYBOOKS, get_playbook
from sim_core.rightsize import verified_sizing

SCENARIOS = [None, *PLAYBOOKS]


def _run(playbook: str | None) -> tuple[object, dict]:
    config = _demo() if playbook is None else get_playbook(playbook).apply(_demo())
    return config, CloudSimulator(config).run()


@pytest.mark.parametrize("playbook", SCENARIOS)
def test_label_never_disagrees_with_fix_it(playbook: str | None) -> None:
    config, summary = _run(playbook)
    proposed = {s["node"]: s["proposed"] for s in summary["suggestions"]}
    for n in config.topology.nodes:
        info = summary["component_sizing"][n.name]
        after = proposed.get(n.name, n.max_capacity)
        expected = (
            "oversized" if after < n.max_capacity
            else "undersized" if after > n.max_capacity
            else "right_sized"
        )
        assert info["verified_status"] == expected, n.name
        assert info["verified_capacity"] == after, n.name


def test_db_failover_names_the_db_undersized_and_keeps_the_worker() -> None:
    _, summary = _run("db_failover")
    sizing = summary["component_sizing"]
    assert sizing["db"]["verified_status"] == "undersized"
    assert sizing["db"]["verified_capacity"] > 3
    # 29% mean utilisation, but no cut is safe under the incident.
    assert sizing["worker"]["verified_status"] == "right_sized"
    assert sizing["worker"]["verified_capacity"] == 6


def test_clean_worker_cut_is_the_verified_one_not_the_formula() -> None:
    _, summary = _run(None)
    worker = summary["component_sizing"]["worker"]
    assert worker["verified_status"] == "oversized"
    # The formula said 4, which breaches the SLA; the verified cut is 5.
    assert worker["verified_capacity"] == 5


@pytest.mark.parametrize("playbook", SCENARIOS)
def test_evidence_and_score_are_unchanged(playbook: str | None) -> None:
    """The utilisation status and the score do not move (scope: label only)."""
    config, summary = _run(playbook)
    plain = CloudSimulator(config).run(suggest=False)
    for name, info in summary["component_sizing"].items():
        evidence = plain["component_sizing"][name]
        assert info["status"] == evidence["status"]
        assert info["recommended_capacity"] == evidence["recommended_capacity"]
    assert summary["resilience_score"] == plain["resilience_score"]


def test_clean_demo_still_scores_98() -> None:
    _, summary = _run(None)
    assert round(summary["resilience_score"]) == 98


def test_verification_runs_carry_no_verified_fields() -> None:
    plain = CloudSimulator(_demo()).run(suggest=False)
    for info in plain["component_sizing"].values():
        assert "verified_status" not in info
        assert "verified_capacity" not in info


def test_verified_sizing_is_pure() -> None:
    config = _demo()
    cut = {"node": "worker", "param": "max_capacity", "current": 6, "proposed": 5,
           "reason": "", "est_monthly_delta": None}
    raise_ = {**cut, "node": "db", "current": 3, "proposed": 6}
    out = verified_sizing(config, [cut, raise_])
    assert out["worker"] == ("oversized", 5)
    assert out["db"] == ("undersized", 6)
    assert out["lb"] == ("right_sized", 10)
    assert list(out) == [n.name for n in config.topology.nodes]


def test_cli_prints_the_verified_label() -> None:
    _, summary = _run("db_failover")
    text = _format_summary(summary)
    block = text.split("Right-sizing check (verified by simulation)", 1)[1]
    db_line = next(line for line in block.splitlines() if line.strip().startswith("- db "))
    assert "undersized" in db_line
    assert f"size-to {summary['component_sizing']['db']['verified_capacity']}" in db_line


def test_compare_shows_the_verified_label() -> None:
    config = get_playbook("db_failover").apply(_demo())
    diff = diff_runs([run_one("failover", config)])
    assert diff["runs"][0]["sizing"]["db"] == "undersized"
