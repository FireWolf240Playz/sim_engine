"""Contract for roadmap 3.3, API half: fail the node you choose
(`.agents/tasks/3.3-click-to-fail.md`).

A request may carry ``custom_chaos``: chaos events added to the run on top
of the config's own and the playbook's. It is the incident the user built by
clicking a node, kept apart from ``config`` because the config is the
architecture and this is what is done to it.

Measured before these tests existed (2026-11-15): ``config.chaos`` already
takes a targeted event and the config validator already refuses an unknown
node, so the engine needs nothing. What was missing is the request field,
one merge every route uses, and a refusal that reads as a sentence: the
validator's own 422 is a list of Pydantic error objects.

Make these pass. Do not edit them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_api import _config

from api.main import create_app
from api.routes.simulate import effective_config
from sim_core import CloudSimulator, SimulationConfig
from sim_core.config import ChaosEvent

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _long_config() -> dict[str, Any]:
    config = _config().model_dump(mode="json")
    config["duration"] = 20.0
    config["traffic"]["duration"] = 20.0
    return config


def _fail(node: str, **overrides: Any) -> dict[str, Any]:
    """A full outage of ``node`` from t=5 to t=10, once."""
    event: dict[str, Any] = {
        "event_type": "component_failure",
        "target": node,
        "intensity": 1.0,
        "start_time": 5.0,
        "duration": 5.0,
        "interval": 20.0,
    }
    event.update(overrides)
    return event


def _simulate(client: TestClient, **overrides: Any) -> Any:
    body: dict[str, Any] = {"config": _long_config(), "include_timeseries": True}
    body.update(overrides)
    return client.post("/simulate", json=body)


# ---------------------------------------------------------------------------
# the failure really happens
# ---------------------------------------------------------------------------


def test_the_chosen_node_loses_its_capacity_in_the_window(client: TestClient) -> None:
    response = _simulate(client, custom_chaos=[_fail("db")])
    assert response.status_code == 200
    capacity = {
        t["time"]: t["per_component"]["db"]["capacity"] for t in response.json()["timeseries"]
    }
    assert capacity[4.0] == 4
    assert all(capacity[float(t)] == 0 for t in range(6, 10))
    assert capacity[11.0] == 4
    worker = {t["per_component"]["worker"]["capacity"] for t in response.json()["timeseries"]}
    assert worker == {6}


def test_the_run_is_worse_for_it(client: TestClient) -> None:
    clean = _simulate(client).json()["summary"]
    failed = _simulate(client, custom_chaos=[_fail("db")]).json()["summary"]
    # 1 rps against a 10 s budget: the outage slows the run without failing it.
    # Measured: p95 5.46 s clean, 6.14 s with the outage.
    assert failed["p95_latency"] > clean["p95_latency"] + 0.5
    assert failed["resilience_score"] <= clean["resilience_score"]


def test_order_service_fails_on_the_20_node_example() -> None:
    """The roadmap's own case. Through the merge the routes use, then the
    engine, without the Fix-it plan (it takes half a minute on this example)."""
    base = SimulationConfig.from_yaml(str(EXAMPLES / "microservice_platform.yaml"))
    event = ChaosEvent(
        event_type="component_failure",
        target="order-service",
        intensity=1.0,
        start_time=30.0,
        duration=15.0,
        interval=120.0,
    )
    config = effective_config(base, None, [event])
    assert config.chaos[-1] == event and len(config.chaos) == len(base.chaos) + 1

    configured = next(n.max_capacity for n in base.topology.nodes if n.name == "order-service")
    events: list[dict[str, Any]] = []
    CloudSimulator(config, event_sink=events.append).run(suggest=False)
    capacity = {
        e["time"]: e["per_component"]["order-service"]["capacity"]
        for e in events
        if e["type"] == "tick"
    }
    assert all(capacity[float(t)] == 0 for t in range(32, 45, 2))
    assert capacity[28.0] == configured and capacity[48.0] == configured
    starts = [
        e for e in events if e["type"] == "chaos" and e["phase"] == "start" and e["t"] == 30.0
    ]
    assert [e["node"] for e in starts] == ["order-service"]


# ---------------------------------------------------------------------------
# the merge
# ---------------------------------------------------------------------------


def test_the_response_lists_the_event_it_ran(client: TestClient) -> None:
    payload = _simulate(client, custom_chaos=[_fail("db")]).json()
    assert len(payload["chaos"]) == 1
    assert payload["chaos"][0]["target"] == "db" and payload["chaos"][0]["intensity"] == 1.0


def test_custom_events_come_after_the_playbooks(client: TestClient) -> None:
    """Order: the config's own events, the playbook's, then the custom ones."""
    alone = _simulate(client, playbook="db_failover").json()["chaos"]
    both = _simulate(client, playbook="db_failover", custom_chaos=[_fail("worker")]).json()["chaos"]
    assert both[: len(alone)] == alone
    assert [event["target"] for event in both[len(alone) :]] == ["worker"]


def test_no_custom_chaos_changes_nothing(client: TestClient) -> None:
    plain = _simulate(client, playbook="db_failover").json()
    assert _simulate(client, playbook="db_failover", custom_chaos=[]).json() == plain


def test_effective_config_without_anything_is_the_config() -> None:
    config = _config()
    assert effective_config(config, None, []) is config


# ---------------------------------------------------------------------------
# refusals read as sentences
# ---------------------------------------------------------------------------


def test_an_unknown_node_is_a_clean_422(client: TestClient) -> None:
    response = _simulate(client, custom_chaos=[_fail("nope")])
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "unknown component 'nope'" in detail
    assert "db" in detail and "worker" in detail  # it names the nodes that exist
    assert "Value error" not in detail


def test_a_cache_outage_on_a_node_that_is_no_cache(client: TestClient) -> None:
    response = _simulate(client, custom_chaos=[_fail("db", event_type="cache_outage")])
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str) and "not a cache" in response.json()["detail"]


def test_a_malformed_event_is_refused_by_validation(client: TestClient) -> None:
    assert _simulate(client, custom_chaos=[_fail("db", intensity=2.0)]).status_code == 422
    assert _simulate(client, custom_chaos=[_fail("db", surprise=1)]).status_code == 422


def test_at_most_eight_custom_events(client: TestClient) -> None:
    assert _simulate(client, custom_chaos=[_fail("db")] * 8).status_code == 200
    assert _simulate(client, custom_chaos=[_fail("db")] * 9).status_code == 422


def test_effective_config_raises_the_same_refusal() -> None:
    event = ChaosEvent(event_type="component_failure", target="nope", intensity=1.0)
    with pytest.raises(HTTPException) as caught:
        effective_config(_config(), None, [event])
    assert caught.value.status_code == 422 and "unknown component 'nope'" in caught.value.detail


# ---------------------------------------------------------------------------
# every route that names a run takes it
# ---------------------------------------------------------------------------


def test_a_streamed_run_takes_custom_chaos(client: TestClient) -> None:
    started = client.post("/runs", json={"config": _long_config(), "custom_chaos": [_fail("db")]})
    assert started.status_code == 202
    text = client.get(started.json()["events_url"]).text
    assert "component failure on db: 100% of its capacity is gone" in text
    refused = client.post("/runs", json={"config": _long_config(), "custom_chaos": [_fail("nope")]})
    assert refused.status_code == 422 and "unknown component 'nope'" in refused.json()["detail"]


def test_an_export_takes_custom_chaos(client: TestClient) -> None:
    """The file is the run on screen, so it must be told about the failure."""
    body = {"config": _long_config(), "format": "json", "custom_chaos": [_fail("db")]}
    response = client.post("/export", json=body)
    assert response.status_code == 200
    exported = json.loads(response.content)
    assert exported["config"]["chaos"][-1]["target"] == "db"
    simulated = _simulate(client, custom_chaos=[_fail("db")]).json()["summary"]
    assert exported["summary"]["resilience_score"] == simulated["resilience_score"]
    refused = client.post("/export", json={**body, "custom_chaos": [_fail("nope")]})
    assert refused.status_code == 422
