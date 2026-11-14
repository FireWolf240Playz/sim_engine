"""The API refuses work it should not start, and says why.

Before these tests existed nothing bounded a request: any duration and rate,
times up to 20 seeds, or a sweep over any number of values, ran to completion
inside the request. CORS allowed every origin.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.limits import MAX_REQUESTS_PER_CALL, MAX_SWEEP_VALUES
from api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _config(base_rps: float = 2.0, duration: float = 10.0) -> dict[str, Any]:
    return {
        "seed": 1,
        "duration": duration,
        "topology": {"nodes": [{"name": "solo", "max_capacity": 8, "service_time": 0.05}]},
        "traffic": {"base_rps": base_rps, "duration": duration},
    }


# ---------------------------------------------------------------------------
# work budget
# ---------------------------------------------------------------------------

def test_a_small_run_is_served(client: TestClient) -> None:
    response = client.post("/simulate", json={"config": _config()})
    assert response.status_code == 200
    assert response.json()["summary"]["requests"] > 0


def test_a_run_over_the_budget_is_refused_with_its_numbers(client: TestClient) -> None:
    heavy = _config(base_rps=500.0, duration=1200.0)  # 600,000 requests in one run
    response = client.post("/simulate", json={"config": heavy})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "600,000" in detail and f"{MAX_REQUESTS_PER_CALL:,}" in detail


def test_seeds_multiply_the_work(client: TestClient) -> None:
    per_run = MAX_REQUESTS_PER_CALL / 10  # fine alone, too much twenty times over
    config = _config(base_rps=per_run / 100.0, duration=100.0)
    response = client.post("/simulate", json={"config": config, "n_seeds": 20})
    assert response.status_code == 422
    assert "20 runs" in response.json()["detail"]


def test_compare_counts_every_variant(client: TestClient) -> None:
    per_run = MAX_REQUESTS_PER_CALL / 3  # baseline + three providers is four runs
    config = _config(base_rps=per_run / 100.0, duration=100.0)
    response = client.post("/compare", json={"config": config, "mode": "multi-cloud"})
    assert response.status_code == 422
    assert "4 runs" in response.json()["detail"]


def test_a_sweep_is_bounded_in_length(client: TestClient) -> None:
    body = {
        "config": _config(),
        "mode": "sweep",
        "param": "solo.max_capacity",
        "values": list(range(1, MAX_SWEEP_VALUES + 2)),
    }
    response = client.post("/compare", json=body)
    assert response.status_code == 422
    assert str(MAX_SWEEP_VALUES) in str(response.json()["detail"])


def test_a_sweep_inside_the_bounds_runs(client: TestClient) -> None:
    body = {"config": _config(), "mode": "sweep", "param": "solo.max_capacity",
            "values": [2, 4, 8]}
    assert client.post("/compare", json=body).status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def _allowed_origin(client: TestClient, origin: str) -> str | None:
    response = client.options(
        "/simulate",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    return response.headers.get("access-control-allow-origin")


@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "http://127.0.0.1:3001", "http://localhost"]
)
def test_local_frontends_are_allowed_by_default(client: TestClient, origin: str) -> None:
    assert _allowed_origin(client, origin) == origin


def test_other_origins_are_not_allowed_by_default(client: TestClient) -> None:
    assert _allowed_origin(client, "https://example.com") is None
    assert _allowed_origin(client, "http://localhost.example.com") is None


def test_origins_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_CORS_ORIGINS", "https://app.example.com, https://b.example.com")
    client = TestClient(create_app())
    assert _allowed_origin(client, "https://app.example.com") == "https://app.example.com"
    assert _allowed_origin(client, "https://b.example.com") == "https://b.example.com"
    assert _allowed_origin(client, "http://localhost:3000") is None
