"""Wave 1.1 tests: multi-seed confidence profile (pure) + API + CLI surface.

Run with::

    pytest tests/test_profile.py

Covers the roadmap 1.1 accept criteria:

- ``resilience_profile`` exact-value math on fixed dicts (directionality,
  median, None handling, single-run collapse, error cases).
- ``POST /simulate`` single-run response key set is byte-identical to the
  pre-1.1 contract (no new keys leak in).
- ``n_seeds=3`` multi-run: every run's summary dict-equals the same
  config simulated individually with that seed (the core accept criterion).
- ``seeds`` list wins over ``n_seeds``; 422 validation bounds.
- CLI ``run --seeds 3`` end-to-end (exit 0, profile JSON + PNG written).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from sim_core import (
    ComponentConfig,
    Edge,
    GraphTopologyConfig,
    SimulationConfig,
    TrafficPattern,
    resilience_profile,
    typical_index,
)
from sim_core.profile import timeseries_band

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# pure profile math — fixed dicts, exact values
# ---------------------------------------------------------------------------

def _summary(
    score: float,
    p95: float,
    sla: float,
    cost: float,
    completion: float = 1.0,
) -> dict[str, Any]:
    """A minimal summary dict with the four profiled metrics + completion."""
    return {
        "resilience_score": score,
        "p95_latency": p95,
        "sla_compliance": sla,
        "cost_per_completed_request": cost,
        "completion_rate": completion,
        "requests": 100,
        "failed_requests": 0,
    }


FIXED_RUNS = [
    _summary(90.0, 2.0, 0.99, 0.05, 0.99),  # best overall
    _summary(70.0, 5.0, 0.90, 0.10, 0.90),  # worst overall
    _summary(80.0, 3.0, 0.95, 0.08, 0.95),  # median
]


def test_profile_tri_stats_exact() -> None:
    profile = resilience_profile(FIXED_RUNS, seeds=[7, 8, 9])
    assert profile["n_runs"] == 3
    assert profile["seeds"] == [7, 8, 9]

    # higher-is-better metrics: worst = min, best = max
    assert profile["worst"]["resilience_score"] == 70.0
    assert profile["typical"]["resilience_score"] == 80.0
    assert profile["best"]["resilience_score"] == 90.0
    assert profile["worst"]["sla_compliance"] == 0.90
    assert profile["typical"]["sla_compliance"] == 0.95
    assert profile["best"]["sla_compliance"] == 0.99

    # higher-is-worse metrics: worst = the SLOWEST / most expensive run
    assert profile["worst"]["p95_latency"] == 5.0
    assert profile["typical"]["p95_latency"] == 3.0
    assert profile["best"]["p95_latency"] == 2.0
    assert profile["worst"]["cost_per_completed_request"] == 0.10
    assert profile["typical"]["cost_per_completed_request"] == 0.08
    assert profile["best"]["cost_per_completed_request"] == 0.05

    assert profile["score_spread"] == pytest.approx(20.0)
    assert profile["per_run"] == [
        {"seed": 7, "score": 90.0, "p95": 2.0, "sla": 0.99, "completion": 0.99},
        {"seed": 8, "score": 70.0, "p95": 5.0, "sla": 0.90, "completion": 0.90},
        {"seed": 9, "score": 80.0, "p95": 3.0, "sla": 0.95, "completion": 0.95},
    ]


def test_single_run_collapses_to_one_value() -> None:
    profile = resilience_profile([FIXED_RUNS[0]], seeds=[1])
    for key in (
        "resilience_score",
        "sla_compliance",
        "p95_latency",
        "cost_per_completed_request",
    ):
        assert profile["worst"][key] == profile["typical"][key] == profile["best"][key]
        assert profile["best"][key] == FIXED_RUNS[0][key]
    assert profile["score_spread"] == pytest.approx(0.0)
    assert profile["n_runs"] == 1


def test_even_count_median_is_mean_of_middle_two() -> None:
    runs = [
        _summary(60.0, 6.0, 0.80, 0.06),
        _summary(70.0, 4.0, 0.90, 0.04),
        _summary(80.0, 3.0, 0.95, 0.07),
        _summary(90.0, 2.0, 0.99, 0.03),
    ]
    profile = resilience_profile(runs)
    assert profile["typical"]["resilience_score"] == pytest.approx(75.0)
    assert profile["typical"]["p95_latency"] == pytest.approx(3.5)
    assert profile["worst"]["resilience_score"] == 60.0
    assert profile["best"]["resilience_score"] == 90.0
    assert profile["seeds"] is None  # no seeds given -> per_run seeds are None
    assert all(row["seed"] is None for row in profile["per_run"])


def test_empty_summaries_raise() -> None:
    with pytest.raises(ValueError, match="at least one"):
        resilience_profile([])


def test_seeds_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="seeds"):
        resilience_profile(FIXED_RUNS, seeds=[1, 2])


def test_non_dict_summary_raises() -> None:
    with pytest.raises(ValueError, match="not a dict"):
        resilience_profile([{"resilience_score": 1.0}, "nope"])  # type: ignore[list-item]


def test_none_metrics_are_filtered_per_metric() -> None:
    zero_requests = _summary(50.0, 4.0, 0.80, 0.10, 0.80)
    zero_requests["p95_latency"] = None  # e.g. a run with no completed requests
    zero_requests["cost_per_completed_request"] = None
    healthy = _summary(70.0, 3.0, 0.90, 0.20, 0.90)

    profile = resilience_profile([zero_requests, healthy])
    # p95 computed over the one run that has it
    assert profile["typical"]["p95_latency"] == pytest.approx(3.0)
    # cost computed over the one run that has it (0.20; the other run is None)
    assert profile["best"]["cost_per_completed_request"] == pytest.approx(0.20)
    assert profile["worst"]["cost_per_completed_request"] == pytest.approx(0.20)
    # score/sla are present in both -> normal tri
    assert profile["worst"]["resilience_score"] == 50.0
    assert profile["best"]["resilience_score"] == 70.0


def test_all_none_metric_yields_none_tri() -> None:
    runs = [
        _summary(50.0, 4.0, 0.80, 0.10),
        _summary(70.0, 3.0, 0.90, 0.20),
    ]
    for run in runs:
        run["p95_latency"] = None
    profile = resilience_profile(runs)
    assert profile["worst"]["p95_latency"] is None
    assert profile["typical"]["p95_latency"] is None
    assert profile["best"]["p95_latency"] is None


def test_typical_index_picks_middle_by_score() -> None:
    runs = [
        _summary(90.0, 1.0, 0.99, 0.05),  # index 0
        _summary(50.0, 9.0, 0.70, 0.30),  # index 1 (lowest)
        _summary(70.0, 5.0, 0.90, 0.10),  # index 2 (median)
    ]
    assert typical_index(runs) == 2
    # single run is always itself
    assert typical_index([runs[0]]) == 0
    with pytest.raises(ValueError):
        typical_index([])


# ---------------------------------------------------------------------------
# API — single-run contract unchanged + multi-run accept criteria
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _config(seed: int | None = 42) -> SimulationConfig:
    """Small worker->db chain; tiny horizon keeps the suite fast."""
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


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"config": _config().model_dump(mode="json")}
    body.update(overrides)
    return body


def test_api_single_run_key_set_unchanged(client: TestClient) -> None:
    """Accept: n_seeds==1 response is byte-identical to the pre-1.1 shape."""
    response = client.post("/simulate", json=_body())
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"summary", "report_png_b64", "chaos", "timeseries"}
    assert "seeds" not in payload and "runs" not in payload and "profile" not in payload


def test_api_multi_run_matches_individual_runs(client: TestClient) -> None:
    """Accept: profile values exactly match the individual run summaries."""
    multi = client.post("/simulate", json=_body(seeds=[101, 202, 303])).json()
    assert multi["seeds"] == [101, 202, 303]
    assert [r["seed"] for r in multi["runs"]] == [101, 202, 303]

    # Core accept: each run == the same config simulated alone at that seed.
    for entry in multi["runs"]:
        solo_body = _body()
        solo_body["config"]["seed"] = entry["seed"]
        solo = client.post("/simulate", json=solo_body).json()
        assert entry["summary"] == solo["summary"]

    # Profile values == min/median/max over those individual summaries.
    scores = [r["summary"]["resilience_score"] for r in multi["runs"]]
    profile = multi["profile"]
    assert profile["worst"]["resilience_score"] == min(scores)
    assert profile["typical"]["resilience_score"] == statistics.median(scores)
    assert profile["best"]["resilience_score"] == max(scores)
    assert profile["score_spread"] == max(scores) - min(scores)
    assert profile["n_runs"] == 3
    assert profile["seeds"] == [101, 202, 303]

    # The headline summary is the typical run's (middle by score).
    ordered = sorted(multi["runs"], key=lambda r: r["summary"]["resilience_score"])
    assert multi["summary"] == ordered[1]["summary"]


def test_api_n_seeds_derives_seeds_from_config(client: TestClient) -> None:
    multi = client.post("/simulate", json=_body(n_seeds=3)).json()
    assert multi["seeds"] == [42, 43, 44]
    assert len(multi["runs"]) == 3


def test_api_seedless_config_defaults_to_base_42(client: TestClient) -> None:
    body = _body(n_seeds=3)
    body["config"]["seed"] = None
    multi = client.post("/simulate", json=body).json()
    assert multi["seeds"] == [42, 43, 44]


def test_api_seeds_list_wins_over_n_seeds(client: TestClient) -> None:
    multi = client.post("/simulate", json=_body(n_seeds=5, seeds=[55])).json()
    assert multi["seeds"] == [55]
    assert len(multi["runs"]) == 1


def test_api_multi_run_omits_png_and_timeline_unless_asked(client: TestClient) -> None:
    multi = client.post("/simulate", json=_body(n_seeds=2, include_report_png=True)).json()
    # The PNG stays single-run only; no timeline keys unless requested.
    assert set(multi) == {"seeds", "runs", "profile", "chaos", "summary", "typical_seed"}


def test_api_multi_run_timeline_is_the_typical_runs(client: TestClient) -> None:
    """The 5-seed run used to render an empty timeline: multi-mode dropped
    ``include_timeseries`` silently. The timeline follows the headline."""
    body = _body(seeds=[101, 202, 303], include_timeseries=True)
    multi = client.post("/simulate", json=body).json()
    assert multi["typical_seed"] in (101, 202, 303)
    typical = next(r for r in multi["runs"] if r["seed"] == multi["typical_seed"])
    assert typical["summary"] == multi["summary"]

    solo_body = _body(include_timeseries=True)
    solo_body["config"]["seed"] = multi["typical_seed"]
    solo = client.post("/simulate", json=solo_body).json()
    assert multi["timeseries"] == solo["timeseries"]
    assert multi["timeseries"]  # non-empty: the chart has something to draw

    band = multi["timeseries_band"]
    assert [b["time"] for b in band] == [t["time"] for t in multi["timeseries"]]
    for b, t in zip(band, multi["timeseries"], strict=True):
        if t["p95_latency"] is not None:
            assert b["p95_min"] <= t["p95_latency"] <= b["p95_max"]


def test_timeseries_band_fixed_dicts() -> None:
    a = [{"time": 1.0, "p95_latency": 2.0}, {"time": 2.0, "p95_latency": None}]
    b = [{"time": 1.0, "p95_latency": 5.0}, {"time": 2.0, "p95_latency": None}]
    c = [{"time": 1.0, "p95_latency": 3.0}, {"time": 2.0, "p95_latency": 4.0}]
    assert timeseries_band([a, b, c]) == [
        {"time": 1.0, "p95_min": 2.0, "p95_max": 5.0},
        {"time": 2.0, "p95_min": 4.0, "p95_max": 4.0},
    ]
    assert timeseries_band([a, b]) == [
        {"time": 1.0, "p95_min": 2.0, "p95_max": 5.0},
        {"time": 2.0, "p95_min": None, "p95_max": None},
    ]
    assert timeseries_band([]) == []


def test_api_multi_run_validations(client: TestClient) -> None:
    assert client.post("/simulate", json=_body(n_seeds=0)).status_code == 422
    assert client.post("/simulate", json=_body(n_seeds=21)).status_code == 422
    assert client.post("/simulate", json=_body(seeds=[])).status_code == 422


# ---------------------------------------------------------------------------
# CLI — run --seeds 3 end-to-end
# ---------------------------------------------------------------------------


def test_cli_seeds_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # default outputs land in the temp dir
    from sim_core.cli import main

    code = main(
        [
            "run",
            str(REPO_ROOT / "my_topology.yaml"),  # seed 7 -> seeds 7, 8, 9
            "--seeds",
            "3",
        ]
    )
    assert code == 0

    payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert payload["seeds"] == [7, 8, 9]
    assert len(payload["runs"]) == 3
    profile = payload["profile"]
    assert profile["n_runs"] == 3
    assert profile["seeds"] == [7, 8, 9]
    assert len(profile["per_run"]) == 3
    assert (tmp_path / "eleven_report.png").is_file()

    # The profile math agrees with the runs it summarizes.
    scores = [r["summary"]["resilience_score"] for r in payload["runs"]]
    assert profile["worst"]["resilience_score"] == min(scores)
    assert profile["best"]["resilience_score"] == max(scores)
    assert profile["typical"]["resilience_score"] == statistics.median(scores)
