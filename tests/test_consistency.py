"""One answer per question: the grade agrees with the sizing label, and a node
is reported once.

Measured before these tests existed:

- The cost grade read the utilisation guess while the sizing table showed the
  verified label. A healthy three-node stack had every node labelled
  ``right_sized`` (no cut survives verification), nothing to fix, and a cost
  grade of D, because each node sat under 50% mean utilisation.
- A node past 90% mean utilisation raised two findings, ``util_weak_link``
  (crit) and ``undersized`` (warn), spending two of the five places on one
  fact.
"""

from __future__ import annotations

from typing import Any

from sim_core import CloudSimulator, SimulationConfig
from sim_core.findings import build_findings
from sim_core.score import cost_grade, resilience_score, score_explanation


def _healthy() -> SimulationConfig:
    """Low mean utilisation everywhere, yet no node can lose a slot safely."""
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": 120.0,
            "sla_target": 1.0,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", "max_capacity": 1,
                     "service_time": 0.01, "cost_per_hour": 1.0},
                    {"name": "worker", "role": "worker", "max_capacity": 2,
                     "service_time": 0.04, "cost_per_hour": 1.0},
                    {"name": "db", "role": "database", "max_capacity": 1,
                     "service_time": 0.02, "cost_per_hour": 1.0},
                ],
                "edges": [
                    {"source": "lb", "target": "worker"},
                    {"source": "worker", "target": "db"},
                ],
            },
            "traffic": {"base_rps": 20.0, "duration": 120.0},
        }
    )


# ---------------------------------------------------------------------------
# cost grade vs sizing label
# ---------------------------------------------------------------------------

def test_grade_follows_the_verified_label() -> None:
    summary = CloudSimulator(_healthy()).run()
    sizing = summary["component_sizing"]
    assert {info["status"] for info in sizing.values()} == {"oversized"}  # the guess
    assert {info["verified_status"] for info in sizing.values()} == {"right_sized"}
    assert summary["suggestions"] == []
    assert summary["cost_grade"] == "A"


def test_grade_in_the_summary_is_the_grade_of_the_summary() -> None:
    """Whatever the summary carries, ``cost_grade`` re-derives it from that dict."""
    summary = CloudSimulator(_healthy()).run()
    assert cost_grade(summary) == summary["cost_grade"]


def test_grade_prefers_the_verified_status_per_node() -> None:
    sizing = {
        "a": {"status": "oversized", "verified_status": "right_sized"},
        "b": {"status": "oversized", "verified_status": "right_sized"},
    }
    assert cost_grade({"component_sizing": sizing}) == "A"
    # a verified cut still costs the grade, on the cost share too
    sizing["b"]["verified_status"] = "oversized"
    priced = {
        "component_sizing": sizing,
        "total_cost": 10.0,
        "cost_breakdown_by_component": {"a": 1.0, "b": 9.0},
    }
    assert cost_grade(priced) == "C"  # 0.7 * 0.75 + 0.3 * 0.1 = 0.555


def test_grade_without_a_verified_status_reads_the_guess() -> None:
    """Verification runs (``suggest=False``) and stored payloads carry no label."""
    summary = CloudSimulator(_healthy()).run(suggest=False)
    assert summary["cost_grade"] == "D"


# ---------------------------------------------------------------------------
# the score and its explanation
# ---------------------------------------------------------------------------

def test_the_explanation_always_states_the_score() -> None:
    """``score_explanation`` promises ``["score"] == resilience_score``. The
    two summed the same terms in a different order, and on this grid 3,005 of
    53,734 summaries came out 0.1 apart (16.4 explained, 16.3 scored)."""
    apart = []
    for i in range(0, 401):
        for j in range(0, 401, 3):
            summary = {
                "requests": 400,
                "sla_compliance": i / 400,
                "completion_rate": j / 400,
                "failed_requests": 400 - j,
                "total_retries": (i * 7) % 90,
                "sla_target": 2.0,
                "p95_latency": 1.0 + (j % 50) / 20,
            }
            explained = score_explanation(summary)
            assert explained is not None
            if explained["score"] != resilience_score(summary):
                apart.append((i, j, explained["score"], resilience_score(summary)))
    assert apart == []


# ---------------------------------------------------------------------------
# one finding per node and fact
# ---------------------------------------------------------------------------

def _node_summary(mean_utilization: float) -> dict[str, Any]:
    return {
        "requests": 100,
        "completion_rate": 1.0,
        "sla_compliance": 1.0,
        "failed_requests": 0,
        "total_retries": 0,
        "component_sizing": {
            "worker": {
                "status": "undersized",
                "mean_utilization": mean_utilization,
                "p95_utilization": 1.0,
                "p95_queue": 3.0,
                "recommended_capacity": 8,
            }
        },
    }


def test_a_pinned_node_is_reported_once() -> None:
    findings = build_findings(_node_summary(0.95))
    assert [(f["id"], f["severity"]) for f in findings] == [("util_weak_link", "crit")]


def test_an_undersized_node_under_the_crit_line_still_warns() -> None:
    findings = build_findings(_node_summary(0.87))
    assert [(f["id"], f["severity"]) for f in findings] == [("undersized", "warn")]
