"""Wave 1.2 tests: deterministic findings — "why this score".

Run with::

    pytest tests/test_findings.py

Covers the roadmap 1.2 accept criteria:

- ``build_findings`` exact finding ids/severities/texts on fixed dicts
  (one test per rule, verbatim strings).
- Ordering (crit → warn → info, stable within a tier) and the cap of 5.
- Edge cases: zero requests → no verdict; ``sla_target=None`` → no
  headroom finding; healthy run → single ``info`` line; ``ValueError``
  on non-dict input.
- Determinism: the same summary dict always yields the same list.
- Seed-pinned e2e: two runs of the same config at the same seed produce
  identical ``summary()["findings"]``.
- Surface: the CLI report contains the Verdict block; ``POST /simulate``
  single- and multi-seed responses carry ``summary.findings``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from sim_core import (
    CloudSimulator,
    ComponentConfig,
    Edge,
    GraphTopologyConfig,
    SimulationConfig,
    TrafficPattern,
    build_findings,
)
from sim_core.cli.sim import _format_summary

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers — fixed summary dicts (the findings input contract)
# ---------------------------------------------------------------------------

def _sizing(**per_node: Any) -> dict[str, Any]:
    return per_node


def _summary(**overrides: Any) -> dict[str, Any]:
    """A healthy single-run summary; override fields per test.

    Healthy baseline: 100 requests, 99% SLA, zero retries, p95 well
    under 1.5x the 2s SLA target, all nodes right-sized.
    """
    base: dict[str, Any] = {
        "requests": 100,
        "completion_rate": 1.0,
        "failed_requests": 0,
        "sla_compliance": 0.99,
        "avg_latency": 0.5,
        "p50_latency": 0.4,
        "p95_latency": 1.0,
        "p99_latency": 1.2,
        "cache_hit_rate": None,
        "total_retries": 0,
        "total_cost": 10.0,
        "sla_target": 2.0,
        "resilience_score": 88.0,
        "cost_grade": "B",
        "component_sizing": _sizing(
            worker={
                "status": "right_sized",
                "mean_utilization": 0.60,
                "p95_utilization": 0.80,
                "p95_queue": 0.0,
                "recommended_capacity": 6,
            }
        ),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# one rule per test — exact ids, severities, texts, node
# ---------------------------------------------------------------------------

def test_util_weak_link_is_crit_with_node() -> None:
    findings = build_findings(
        _summary(
            component_sizing=_sizing(
                worker={
                    "status": "right_sized",
                    "mean_utilization": 0.97,
                    "p95_utilization": 1.0,
                    "p95_queue": 4.0,
                    "recommended_capacity": 6,
                }
            )
        )
    )
    assert findings == [
        {
            "id": "util_weak_link",
            "severity": "crit",
            "text": "worker is your weakest link (avg 97% util)",
            "node": "worker",
        }
    ]


def test_sla_breach_is_crit() -> None:
    findings = build_findings(_summary(sla_compliance=0.912))
    assert findings == [
        {
            "id": "sla_breach",
            "severity": "crit",
            "text": "SLA compliance 91.2% is below the 95% floor",
        }
    ]


def test_retry_storm_is_warn() -> None:
    findings = build_findings(_summary(total_retries=3))
    assert findings == [
        {
            "id": "retry_storm",
            "severity": "warn",
            "text": "3.0% of requests needed a retry — a retry storm amplifies the load",
        }
    ]


def test_retry_ratio_at_threshold_is_not_a_storm() -> None:
    # Exactly 2% → the rule fires only strictly above the ratio.
    findings = build_findings(_summary(total_retries=2))
    assert findings == [
        {"id": "healthy", "severity": "info", "text": "No structural weaknesses found in this run"}
    ]


def test_p95_headroom_is_warn_with_math() -> None:
    findings = build_findings(_summary(p95_latency=4.0, sla_target=2.0))
    assert findings == [
        {
            "id": "p95_headroom",
            "severity": "warn",
            "text": "P95 4.00s is 2.0x the 2.00s SLA target — no headroom left",
        }
    ]


def test_p95_at_ratio_is_not_flagged() -> None:
    # Exactly 1.5x → the rule fires only strictly above the multiple.
    findings = build_findings(_summary(p95_latency=3.0, sla_target=2.0))
    assert [f["id"] for f in findings] == ["healthy"]


def test_no_sla_target_means_no_headroom_finding() -> None:
    findings = build_findings(_summary(p95_latency=4.0, sla_target=None))
    assert [f["id"] for f in findings] == ["healthy"]


def test_undersized_node_is_warn_with_node() -> None:
    findings = build_findings(
        _summary(
            component_sizing=_sizing(
                db={
                    "status": "undersized",
                    "mean_utilization": 0.85,
                    "p95_utilization": 1.0,
                    "p95_queue": 6.0,
                    "recommended_capacity": 12,
                }
            )
        )
    )
    assert findings == [
        {
            "id": "undersized",
            "severity": "warn",
            "text": "db looks undersized (recommend size-to 12)",
            "node": "db",
        }
    ]


def test_healthy_run_is_single_info_line() -> None:
    findings = build_findings(_summary())
    assert findings == [
        {"id": "healthy", "severity": "info", "text": "No structural weaknesses found in this run"}
    ]


# ---------------------------------------------------------------------------
# ordering + cap
# ---------------------------------------------------------------------------

def test_sorted_by_severity_stable_within_tier_capped_at_5() -> None:
    summary = _summary(
        sla_compliance=0.80,  # crit: sla_breach
        total_retries=10,  # warn: retry_storm (10%)
        p95_latency=6.0,  # warn: p95_headroom (3x)
        component_sizing=_sizing(
            worker={
                "status": "undersized",
                "mean_utilization": 0.95,  # crit: util_weak_link + warn: undersized
                "p95_utilization": 1.0,
                "p95_queue": 2.0,
                "recommended_capacity": 8,
            },
            db={
                "status": "undersized",
                "mean_utilization": 0.50,
                "p95_utilization": 0.6,
                "p95_queue": 0.0,
                "recommended_capacity": 4,
            },
        ),
    )
    findings = build_findings(summary)
    # 6 findings fire → capped at 5.
    assert len(findings) == 5
    assert [f["id"] for f in findings] == [
        "util_weak_link",  # crit, rule order
        "sla_breach",  # crit, rule order
        "retry_storm",  # warn, rule order
        "p95_headroom",  # warn, rule order
        "undersized",  # warn, first undersized node (worker before db)
    ]
    assert [f["severity"] for f in findings] == ["crit", "crit", "warn", "warn", "warn"]
    # Node findings keep their node; global ones omit it entirely.
    assert findings[0]["node"] == "worker"
    assert "node" not in findings[1]
    assert findings[4]["node"] == "worker"


# ---------------------------------------------------------------------------
# edges + determinism
# ---------------------------------------------------------------------------

def test_zero_requests_yields_no_verdict() -> None:
    summary = _summary()
    summary["requests"] = 0
    assert build_findings(summary) == []


def test_non_dict_raises() -> None:
    with pytest.raises(ValueError, match="expects a summary dict"):
        build_findings("nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="expects a summary dict"):
        build_findings(None)  # type: ignore[arg-type]


def test_same_dict_same_findings_always() -> None:
    summary = _summary(sla_compliance=0.90, total_retries=5, p95_latency=5.0)
    first = build_findings(summary)
    for _ in range(5):
        assert build_findings(dict(summary)) == first


def _config(seed: int = 7) -> SimulationConfig:
    """Small overloaded worker->db chain; tiny horizon keeps the suite fast."""
    return SimulationConfig(
        seed=seed,
        duration=6.0,
        metrics_interval=1.0,
        sla_target=10.0,
        topology=GraphTopologyConfig(
            nodes=[
                ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=1.0),
                ComponentConfig(name="db", role="database", max_capacity=4, service_time=2.0),
            ],
            edges=[Edge(source="worker", target="db", probability=1.0)],
        ),
        traffic=TrafficPattern(base_rps=1.0, duration=6.0),
        chaos=[],
    )


def test_seed_pinned_runs_produce_identical_findings() -> None:
    first = CloudSimulator(_config(seed=7)).run()["findings"]
    second = CloudSimulator(_config(seed=7)).run()["findings"]
    assert first == second
    # And every finding is well-formed.
    for finding in first:
        assert finding["id"]
        assert finding["severity"] in ("info", "warn", "crit")
        assert finding["text"]
        if "node" in finding:
            assert isinstance(finding["node"], str)


# ---------------------------------------------------------------------------
# surface — CLI report + API response
# ---------------------------------------------------------------------------

def test_cli_report_contains_verdict_block() -> None:
    summary = _summary(sla_compliance=0.90, p95_latency=5.0)
    summary["findings"] = build_findings(summary)
    report = _format_summary(summary)
    assert "Verdict (deterministic — same input, same words)" in report
    assert "[CRIT] SLA compliance 90.0% is below the 95% floor" in report
    assert "[WARN] P95 5.00s is 2.5x the 2.00s SLA target — no headroom left" in report


def test_cli_report_without_findings_still_prints() -> None:
    # Pre-1.2 shaped summary (no findings key) must not break the report.
    report = _format_summary(_summary())
    assert "Requests served" in report
    assert "Verdict" not in report


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"config": _config().model_dump(mode="json")}
    body.update(overrides)
    return body


def test_api_single_run_summary_carries_findings(client: TestClient) -> None:
    response = client.post("/simulate", json=_body())
    assert response.status_code == 200
    findings = response.json()["summary"]["findings"]
    assert isinstance(findings, list) and findings
    for finding in findings:
        assert set(finding) >= {"id", "severity", "text"}
        assert finding["severity"] in ("info", "warn", "crit")


def test_api_multi_run_findings_match_solo_runs(client: TestClient) -> None:
    multi = client.post("/simulate", json=_body(seeds=[11, 22])).json()
    for entry in multi["runs"]:
        solo_body = _body()
        solo_body["config"]["seed"] = entry["seed"]
        solo = client.post("/simulate", json=solo_body).json()
        assert entry["summary"]["findings"] == solo["summary"]["findings"]
    # The headline (typical run) findings come from one of the runs.
    run_findings = {json.dumps(r["summary"]["findings"], sort_keys=True) for r in multi["runs"]}
    assert json.dumps(multi["summary"]["findings"], sort_keys=True) in run_findings
