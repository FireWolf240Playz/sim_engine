"""Phase 4 tests: the FastAPI surface (stateless, config in → results out).

Run with::

    pytest tests/test_api.py

Uses FastAPI's TestClient (needs ``httpx``). Every simulation here is
tiny (6 s horizon, a handful of requests) to keep the suite fast — the
goal is the *contract* (status codes, response shape, error mapping),
not simulation fidelity.
"""

from __future__ import annotations

import base64
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
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _config() -> SimulationConfig:
    """Small worker→db chain — the edge is what puts the db on the request
    path, so the db_failover playbook genuinely degrades it."""
    return SimulationConfig(
        seed=42,
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
    """A valid /simulate body; overrides replace top-level fields."""
    body: dict[str, Any] = {"config": _config().model_dump(mode="json")}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------

def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------

def test_playbooks_listing(client: TestClient) -> None:
    response = client.get("/playbooks")
    assert response.status_code == 200
    payload = response.json()
    names = [p["name"] for p in payload["playbooks"]]
    assert payload["count"] == len(payload["playbooks"]) == 4
    for expected in ("db_failover", "cross_region_latency_spike",
                     "cache_eviction_storm", "dependency_timeout_cascade"):
        assert expected in names
    assert all(p["description"].strip() for p in payload["playbooks"])


def test_presets_listing(client: TestClient) -> None:
    response = client.get("/presets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == len(payload["presets"]) >= 12
    rds = payload["presets"]["aws_rds_small"]
    assert rds["name"] == "aws-rds"  # default name from the preset factory
    assert rds["max_capacity"] > 0
    assert rds["service_time"] > 0


# ---------------------------------------------------------------------------
# POST /simulate
# ---------------------------------------------------------------------------

def test_simulate_returns_full_summary(client: TestClient) -> None:
    response = client.post("/simulate", json=_body())
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["requests"] > 0
    assert summary["resilience_score"] is not None
    assert 0.0 <= summary["resilience_score"] <= 100.0
    assert summary["cost_grade"] in {"A", "B", "C", "D", "F"}
    assert "p95_latency" in summary
    assert response.json()["report_png_b64"] is None


def test_simulate_rejects_invalid_config(client: TestClient) -> None:
    response = client.post("/simulate", json={"config": "not-a-config"})
    assert response.status_code == 422
    # A structurally valid body with a bad field value is also a 422.
    bad = _body()
    bad["config"]["duration"] = -5
    assert client.post("/simulate", json=bad).status_code == 422


def test_simulate_with_playbook(client: TestClient) -> None:
    response = client.post("/simulate", json=_body(playbook="db_failover"))
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["requests"] > 0
    assert summary["resilience_score"] is not None


def test_simulate_unknown_playbook_is_404(client: TestClient) -> None:
    response = client.post("/simulate", json=_body(playbook="no_such_incident"))
    assert response.status_code == 404
    assert "unknown playbook" in response.json()["detail"]


def test_simulate_returns_report_png_when_asked(client: TestClient) -> None:
    response = client.post("/simulate", json=_body(include_report_png=True))
    assert response.status_code == 200
    encoded = response.json()["report_png_b64"]
    assert encoded
    decoded = base64.b64decode(encoded)
    assert decoded.startswith(PNG_MAGIC)


def test_simulate_include_timeseries(client: TestClient) -> None:
    response = client.post("/simulate", json=_body(include_timeseries=True))
    assert response.status_code == 200
    payload = response.json()
    ticks = payload["timeseries"]
    assert isinstance(ticks, list) and ticks
    for tick in ticks:
        assert {"time", "p50_latency", "p95_latency", "sla_met", "per_component"} <= set(tick)
        assert tick["per_component"]["worker"]["utilization"] >= 0.0
    times = [tick["time"] for tick in ticks]
    assert times == sorted(times)
    # Flag omitted ⇒ field stays null (backward compatible shape).
    default = client.post("/simulate", json=_body()).json()
    assert default["timeseries"] is None


def test_simulate_returns_effective_chaos_schedule(client: TestClient) -> None:
    # No playbook: the config's own (empty) schedule is echoed back.
    plain = client.post("/simulate", json=_body()).json()
    assert plain["chaos"] == []
    # With db_failover: the two playbook events are visible client-side.
    play = client.post("/simulate", json=_body(playbook="db_failover")).json()
    events = play["chaos"]
    assert len(events) == 2
    assert all(e["target"] == "db" for e in events)
    assert [e["intensity"] for e in events] == [1.0, 0.5]


# ---------------------------------------------------------------------------
# POST /compare
# ---------------------------------------------------------------------------

def test_compare_multi_cloud(client: TestClient) -> None:
    response = client.post("/compare", json=_body(mode="multi-cloud"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "multi-cloud"
    assert payload["knee"] is None
    diff = payload["diff"]
    assert diff["baseline"] == "baseline"
    labels = [run["label"] for run in diff["runs"]]
    assert labels == ["baseline", "aws", "azure", "gcp"]
    for run in diff["runs"]:
        assert "requests" in run["values"]
        assert "p95_latency" in run["deltas"]
        assert "sizing" in run


def test_compare_what_if_requires_set(client: TestClient) -> None:
    response = client.post("/compare", json=_body(mode="what-if"))
    assert response.status_code == 400
    assert "set" in response.json()["detail"]


def test_compare_what_if_applies_patches(client: TestClient) -> None:
    response = client.post(
        "/compare", json=_body(mode="what-if", set=["worker.max_capacity=2"])
    )
    assert response.status_code == 200
    diff = response.json()["diff"]
    labels = [run["label"] for run in diff["runs"]]
    assert labels == ["baseline", "worker.max_capacity=2"]


def test_compare_what_if_rejects_malformed_entry(client: TestClient) -> None:
    response = client.post("/compare", json=_body(mode="what-if", set=["worker.max_capacity"]))
    assert response.status_code == 400


def test_compare_sweep_returns_knee(client: TestClient) -> None:
    response = client.post(
        "/compare",
        json=_body(
            mode="sweep",
            param="worker.max_capacity",
            values=[2.0, 4.0, 6.0],
            metric="p95_latency",
            threshold=0.05,
        ),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "sweep"
    assert payload["knee"] in {2.0, 4.0, 6.0}
    labels = [run["label"] for run in payload["diff"]["runs"]]
    assert labels == [
        "baseline",
        "worker.max_capacity=2.0",
        "worker.max_capacity=4.0",
        "worker.max_capacity=6.0",
    ]


def test_compare_sweep_requires_param_and_values(client: TestClient) -> None:
    assert client.post("/compare", json=_body(mode="sweep")).status_code == 400
    assert client.post(
        "/compare", json=_body(mode="sweep", param="worker.max_capacity")
    ).status_code == 400
