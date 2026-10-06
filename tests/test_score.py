"""Phase 2 tests: deterministic architecture score, cost grade, extrapolation.

Run with::

    pytest tests/test_score.py

The score/grade functions are pure math over summary dicts, so the bulk of
this file feeds *fixed* dicts and asserts *exact* values — no simulation,
no randomness. One end-to-end test at the bottom verifies that a real run's
``summary()`` surfaces the whole scoring block consistently.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from sim_core import (
    AppWorkerConfig,
    CacheConfig,
    CloudSimulator,
    DatabaseConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
)
from sim_core.score import (
    cost_extrapolation,
    cost_grade,
    cost_per_completed_request,
    resilience_score,
    score_band,
    score_explanation,
    score_headline,
)


def _summary(**overrides: Any) -> Dict[str, Any]:
    """A fixed, fully-populated summary dict for deterministic assertions.

    Baseline numbers (chosen so every formula term is non-zero):
    100 requests, 95 completed, 90 SLA-met, 3 retries, SLA target 20 s,
    p95 at 22 s (20% over target), total cost $100 split $60/$40 across a
    right-sized and an oversized component.
    """
    base: Dict[str, Any] = {
        "requests": 100,
        "completion_rate": 0.95,
        "failed_requests": 5,
        "sla_compliance": 0.9,
        "avg_latency": 4.0,
        "p50_latency": 3.0,
        "p95_latency": 22.0,
        "p99_latency": 30.0,
        "cache_hit_rate": 0.8,
        "total_retries": 3,
        "total_cost": 100.0,
        "cost_breakdown_by_component": {"a": 60.0, "b": 40.0},
        "component_sizing": {
            "a": {"status": "right_sized", "mean_utilization": 0.65},
            "b": {"status": "oversized", "mean_utilization": 0.30},
        },
        "sla_target": 20.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# resilience_score: exact values on fixed dicts
# ---------------------------------------------------------------------------

def test_resilience_score_exact_value() -> None:
    """Hand-computed against the documented formula, term by term."""
    # base      = 100 * (0.6 * 0.90 + 0.4 * 0.95) = 92.0
    # failed    = -30 * (5 / 100)                 = -1.5
    # retries   = -10 * min(1, 3 / 100)           = -0.3
    # headroom  = -10 * min(1, (22 - 20) / 20)    = -1.0
    # total     = 89.2
    assert resilience_score(_summary()) == 89.2


def test_resilience_score_is_deterministic() -> None:
    fixed = _summary()
    assert resilience_score(fixed) == resilience_score(dict(fixed))


def test_resilience_score_perfect_run_is_100() -> None:
    summary = _summary(
        sla_compliance=1.0,
        completion_rate=1.0,
        failed_requests=0,
        total_retries=0,
        p95_latency=10.0,  # well inside the 20 s target
    )
    assert resilience_score(summary) == 100.0


def test_resilience_score_clamps_at_zero() -> None:
    summary = _summary(
        sla_compliance=0.0,
        completion_rate=0.0,
        failed_requests=100,
        total_retries=50,
        p95_latency=100.0,
    )
    assert resilience_score(summary) == 0.0


def test_resilience_score_no_requests_is_none() -> None:
    assert resilience_score({"requests": 0}) is None
    assert resilience_score({}) is None


def test_resilience_score_headroom_capped_at_one() -> None:
    """Being 10x over the SLA target still costs only the full 10 points.

    10% over target costs 1.0 point; 1000% over is capped at the full 10 —
    so the two dicts differ by exactly 9 points.
    """
    mild_over = _summary(p95_latency=22.0)  # 10% over target
    far_over = _summary(p95_latency=200.0)  # 1000% over target
    delta = resilience_score(mild_over) - resilience_score(far_over)
    assert delta == pytest.approx(9.0)


def test_resilience_score_no_sla_target_skips_headroom() -> None:
    """Without an SLA target the headroom term contributes nothing."""
    with_target = _summary(sla_target=20.0, p95_latency=22.0)
    without = _summary(sla_target=None, p95_latency=22.0)
    # base 92 - 1.5 - 0.3 = 90.2 exactly
    assert resilience_score(without) == 90.2
    assert resilience_score(with_target) == 89.2


# ---------------------------------------------------------------------------
# score_band + score_explanation: "how the score is built", point by point
# ---------------------------------------------------------------------------

def test_score_band_boundaries() -> None:
    assert score_band(100.0) == "Resilient"
    assert score_band(80.0) == "Resilient"
    assert score_band(79.9) == "Solid"
    assert score_band(65.0) == "Solid"
    assert score_band(64.9) == "At risk"
    assert score_band(50.0) == "At risk"
    assert score_band(49.9) == "Fragile"
    assert score_band(0.0) == "Fragile"


def test_score_explanation_matches_resilience_score() -> None:
    """The decomposition can never disagree with the score itself."""
    fixed = _summary()
    explanation = score_explanation(fixed)
    assert explanation is not None
    assert explanation["score"] == resilience_score(fixed) == 89.2
    assert explanation["band"] == "Resilient"  # 89.2 >= 80


def test_score_explanation_term_decomposition_exact() -> None:
    """The baseline dict, term by term (inputs in the _summary docstring)."""
    explanation = score_explanation(_summary())
    assert explanation is not None
    points = {term["label"]: term["points"] for term in explanation["terms"]}
    assert points == {
        "SLA compliance": 54.0,   # 100 * 0.6 * 0.90
        "Completion": 38.0,       # 100 * 0.4 * 0.95
        "Failed requests": -1.5,  # -30 * 5/100
        "Retry pressure": -0.3,   # -10 * 3/100
        "P95 headroom": -1.0,     # -10 * (22-20)/20
    }
    assert explanation["clamped"] is False


def test_score_explanation_term_shape_and_detail() -> None:
    explanation = score_explanation(_summary())
    assert explanation is not None
    for term in explanation["terms"]:
        assert set(term) == {"label", "points", "detail"}
        assert isinstance(term["label"], str) and term["label"]
        assert isinstance(term["points"], (int, float))
        assert isinstance(term["detail"], str) and term["detail"]


def test_score_explanation_zero_penalty_terms_read_zero() -> None:
    clean = _summary(
        failed_requests=0,
        total_retries=0,
        p95_latency=10.0,  # well inside the 20 s target
    )
    explanation = score_explanation(clean)
    assert explanation is not None
    points = {term["label"]: term["points"] for term in explanation["terms"]}
    assert points["Failed requests"] == 0.0
    assert points["Retry pressure"] == 0.0
    assert points["P95 headroom"] == 0.0
    assert explanation["clamped"] is False
    # 100 * (0.6 * 0.90 + 0.4 * 0.95) = 92.0, nothing deducted
    assert explanation["score"] == resilience_score(clean) == 92.0


def test_score_explanation_flags_the_clamp() -> None:
    broken = _summary(
        sla_compliance=0.0,
        completion_rate=0.0,
        failed_requests=100,
        total_retries=50,
        p95_latency=100.0,
    )
    explanation = score_explanation(broken)
    assert explanation is not None
    assert explanation["score"] == 0.0
    assert explanation["clamped"] is True
    assert explanation["band"] == "Fragile"


def test_score_explanation_no_requests_is_none() -> None:
    assert score_explanation({"requests": 0}) is None
    assert score_explanation({}) is None


# ---------------------------------------------------------------------------
# cost_grade: exact bands on fixed dicts
# ---------------------------------------------------------------------------

def test_cost_grade_mixed_topology_is_c() -> None:
    """right + oversized, $40 of $100 on the oversized component.

    composite = 0.7 * 0.75 + 0.3 * 0.6 = 0.705  →  C band (0.55..0.75).
    """
    assert cost_grade(_summary()) == "C"


def test_cost_grade_all_right_sized_is_a() -> None:
    summary = _summary(
        total_cost=0.0,
        cost_breakdown_by_component={},
        component_sizing={
            "a": {"status": "right_sized"},
            "b": {"status": "right_sized"},
        },
    )
    assert cost_grade(summary) == "A"


def test_undersized_vs_oversized_grade_differently() -> None:
    """The plan's required discrimination test, both directions."""
    undersized = _summary(
        total_cost=90.0,
        cost_breakdown_by_component={"a": 30.0, "b": 30.0, "c": 30.0},
        component_sizing={
            "a": {"status": "undersized"},
            "b": {"status": "undersized"},
            "c": {"status": "undersized"},
        },
    )
    # 0.7 * 0.0 + 0.3 * (1 - 0/90) = 0.30  →  F
    assert cost_grade(undersized) == "F"

    oversized = _summary(
        total_cost=90.0,
        cost_breakdown_by_component={"a": 30.0, "b": 30.0, "c": 30.0},
        component_sizing={
            "a": {"status": "oversized"},
            "b": {"status": "oversized"},
            "c": {"status": "oversized"},
        },
    )
    # 0.7 * 0.5 + 0.3 * (1 - 90/90) = 0.35  →  D
    assert cost_grade(oversized) == "D"

    assert cost_grade(undersized) != cost_grade(oversized)


def test_cost_grade_no_sizing_data_is_none() -> None:
    assert cost_grade({}) is None
    assert cost_grade({"component_sizing": {}}) is None


def test_cost_grade_falls_back_to_capacity_share_without_prices() -> None:
    """Zero total cost: efficiency = 1 - oversized share (not cost share)."""
    summary = _summary(
        total_cost=0.0,
        cost_breakdown_by_component={},
        component_sizing={
            "a": {"status": "right_sized"},
            "b": {"status": "oversized"},
        },
    )
    # 0.7 * 0.75 + 0.3 * 0.5 = 0.60 → C (same grade as the priced version)
    assert cost_grade(summary) == "C"


# ---------------------------------------------------------------------------
# cost_extrapolation: the money math
# ---------------------------------------------------------------------------

def test_cost_extrapolation_math() -> None:
    extrap = cost_extrapolation(120.0, 120.0)
    assert extrap["hour"] == pytest.approx(3600.0)
    # Exact identities: month is hour*24*30, year is hour*24*365, by
    # construction (no rounding in between).
    assert extrap["hour"] * 24 * 30 == extrap["month"]
    assert extrap["hour"] * 24 * 365 == extrap["year"]
    assert extrap["month"] == pytest.approx(2_592_000.0)
    assert extrap["year"] == pytest.approx(31_536_000.0)


def test_cost_extrapolation_zero_cost_is_zero() -> None:
    extrap = cost_extrapolation(0.0, 60.0)
    assert extrap == {"hour": 0.0, "month": 0.0, "year": 0.0}


def test_cost_extrapolation_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        cost_extrapolation(10.0, 0.0)
    with pytest.raises(ValueError):
        cost_extrapolation(-1.0, 10.0)


def test_cost_per_completed_request() -> None:
    assert cost_per_completed_request(_summary()) == pytest.approx(100.0 / 95.0)
    assert cost_per_completed_request(_summary(total_cost=0.0)) is None
    assert cost_per_completed_request(_summary(failed_requests=100)) is None


# ---------------------------------------------------------------------------
# headline formatting
# ---------------------------------------------------------------------------

def test_score_headline_shape() -> None:
    summary = _summary(resilience_score=89.2, cost_grade="C")
    summary["cost_extrapolation"] = cost_extrapolation(120.0, 120.0)
    headline = score_headline(summary)
    assert "Resilience 89/100" in headline
    assert "Cost grade C" in headline
    assert "$2,592,000/mo" in headline
    assert "$31.5M/yr" in headline


def test_score_headline_degrades_gracefully() -> None:
    headline = score_headline({"requests": 0})
    assert "Resilience n/a" in headline
    assert "Cost grade n/a" in headline


# ---------------------------------------------------------------------------
# cost split: steady (provisioned base) vs under-load (base + metered)
# ---------------------------------------------------------------------------

def test_score_headline_shows_steady_split() -> None:
    """With ``steady_state_cost_extrapolation`` present, the headline shows
    the under-load monthly bill and the steady (base-only) one in parens."""
    summary = _summary(resilience_score=89.2, cost_grade="C")
    summary["cost_extrapolation"] = cost_extrapolation(120.0, 120.0)
    summary["steady_state_cost_extrapolation"] = cost_extrapolation(100.0, 120.0)
    headline = score_headline(summary)
    assert "Resilience 89/100 | Cost grade C" in headline
    assert "$2,592,000/mo (steady $2,160,000/mo)" in headline
    assert "$31.5M/yr" in headline


def test_score_headline_without_steady_key_is_backward_compatible() -> None:
    """Old summaries (no split key) render the original one-number headline."""
    summary = _summary(resilience_score=89.2, cost_grade="C")
    summary["cost_extrapolation"] = cost_extrapolation(120.0, 120.0)
    headline = score_headline(summary)
    assert "steady" not in headline
    assert "$2,592,000/mo | $31.5M/yr" in headline


# ---------------------------------------------------------------------------
# end-to-end: a real run surfaces the whole scoring block
# ---------------------------------------------------------------------------

def test_summary_surfaces_scoring_block_end_to_end() -> None:
    sim = CloudSimulator(
        SimulationConfig(
            seed=7,
            duration=30.0,
            metrics_interval=1.0,
            sla_target=15.0,
            topology=TopologyConfig(
                load_balancer=LoadBalancerConfig(
                    name="lb", max_capacity=10, service_time=0.4, cost_per_hour=0.02
                ),
                app_worker=AppWorkerConfig(
                    name="worker", max_capacity=5, service_time=1.5, cost_per_hour=0.05
                ),
                cache=CacheConfig(
                    name="redis", max_capacity=8, service_time=0.2, hit_rate=0.9,
                    cost_per_hour=0.01,
                ),
                database=DatabaseConfig(
                    name="db", max_capacity=4, service_time=3.0, cost_per_hour=0.08
                ),
            ),
            traffic=TrafficPattern(base_rps=2.0, duration=30.0),
            chaos=[],
        )
    )
    summary = sim.run()

    # The configured SLA is carried for consumers of the dict.
    assert summary["sla_target"] == 15.0

    score = summary["resilience_score"]
    assert score is not None
    assert 0.0 <= score <= 100.0
    # Recomputing from the same dict gives the identical number.
    assert resilience_score(summary) == score

    assert summary["cost_grade"] in {"A", "B", "C", "D", "F"}

    extrap = summary["cost_extrapolation"]
    assert extrap is not None
    assert extrap["hour"] * 24 * 30 == extrap["month"]
    assert extrap["hour"] * 24 * 365 == extrap["year"]
    # The run horizon was 30 s: hourly rate = total_cost / 30 * 3600.
    assert extrap["hour"] == summary["total_cost"] / 30.0 * 3600.0
    assert summary["total_cost"] > 0.0

    # Cost split: provisioned base + metered part == total, exactly.
    assert summary["cost_base"] + summary["cost_metered"] == pytest.approx(
        summary["total_cost"], rel=1e-9
    )
    assert summary["cost_base"] > 0.0
    assert summary["cost_metered"] >= 0.0

    steady = summary["steady_state_cost_extrapolation"]
    assert steady is not None
    # Same 30 s horizon: steady hourly rate == base cost / 30 * 3600.
    assert steady["hour"] == pytest.approx(summary["cost_base"] / 30.0 * 3600.0)
    # month == hour * 24 * 30 by construction (approx: non-round floats).
    assert steady["hour"] * 24 * 30 == pytest.approx(steady["month"])
    # The steady (base-only) bill can never exceed the under-load bill.
    assert steady["month"] <= extrap["month"]

    completed = summary["requests"] - summary["failed_requests"]
    if completed > 0 and summary["total_cost"] > 0.0:
        assert summary["cost_per_completed_request"] == pytest.approx(
            summary["total_cost"] / completed
        )

    # Determinism: rerunning with the same seed yields the same score.
    again = CloudSimulator(sim.config).run()
    assert again["resilience_score"] == score
    assert again["cost_grade"] == summary["cost_grade"]

    # Rich verdict pass: the explanation and the headline ride along, and
    # the explanation can never disagree with the score on the same dict.
    explanation = summary["score_explanation"]
    assert explanation is not None
    assert explanation["score"] == score
    assert isinstance(summary["verdict_headline"], str)
    assert summary["verdict_headline"]
