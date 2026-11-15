"""Contract for roadmap 3.1, API half: a run is started, then watched
(`.agents/tasks/3.1b-run-stream.md`).

``POST /runs`` starts a run and answers at once with its id.
``GET /runs/{id}/events`` is a server-sent-event stream of that run: the
engine's ticks and chaos windows, the feed lines narrated from them, the
summary as soon as the simulation is done (``measured``), and the full
``/simulate`` body once the Fix-it plan is verified (``result``).
``GET /runs/{id}`` is the same run without a stream.

Measured before these tests existed (2026-11-15): the simulation takes
0.02-0.10 s on every example; the plan takes 0.4 s on the 6-node ones and
6-37 s on the 15- and 20-node ones. ``/simulate`` answers after both.

``/simulate`` itself does not change.

Make these pass. Do not edit them.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from api.runs import RunStore
from fastapi.testclient import TestClient
from sim_core.feed import narrate
from test_api import _config

from api.limits import MAX_REQUESTS_PER_CALL
from api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _long_config() -> dict[str, Any]:
    """worker -> db for 20 s: long enough for an incident (5 s in) to matter."""
    config = _config().model_dump(mode="json")
    config["duration"] = 20.0
    config["traffic"]["duration"] = 20.0
    return config


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"config": _long_config()}
    body.update(overrides)
    return body


def _start(client: TestClient, **overrides: Any) -> str:
    response = client.post("/runs", json=_body(**overrides))
    assert response.status_code == 202, response.text
    return str(response.json()["run_id"])


def _parse(text: str) -> list[dict[str, Any]]:
    """An SSE body as ``[{id, event, data}]``. ``id`` is None when the event
    carries none."""
    events: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            fields[name] = value[1:] if value.startswith(" ") else value
        if "event" in fields:
            events.append(
                {
                    "id": int(fields["id"]) if "id" in fields else None,
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return events


def _events(
    client: TestClient, run_id: str, headers: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    response = client.get(f"/runs/{run_id}/events", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return [event for event in _parse(response.text) if event["event"] != "ping"]


def _of(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [event["data"] for event in events if event["event"] == name]


# ---------------------------------------------------------------------------
# starting a run
# ---------------------------------------------------------------------------


def test_a_run_is_started_and_named(client: TestClient) -> None:
    response = client.post("/runs", json=_body())
    assert response.status_code == 202
    payload = response.json()
    assert isinstance(payload["run_id"], str) and len(payload["run_id"]) >= 16
    assert payload["events_url"] == f"/runs/{payload['run_id']}/events"


def test_two_runs_get_two_ids(client: TestClient) -> None:
    assert _start(client) != _start(client)


def test_an_unknown_playbook_is_refused_before_anything_starts(client: TestClient) -> None:
    response = client.post("/runs", json=_body(playbook="nope"))
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown playbook: 'nope'"


def test_a_playbook_that_cannot_apply_is_refused(client: TestClient) -> None:
    """Same status and words as ``/simulate``: this stack has no cache."""
    simulate = client.post("/simulate", json=_body(playbook="cache_eviction_storm"))
    response = client.post("/runs", json=_body(playbook="cache_eviction_storm"))
    assert simulate.status_code == response.status_code == 400
    assert response.json()["detail"] == simulate.json()["detail"]


def test_a_run_over_the_budget_is_refused(client: TestClient) -> None:
    config = _long_config()
    config["duration"] = 400.0
    config["traffic"] = {"base_rps": 1000.0, "duration": 400.0}
    response = client.post("/runs", json={"config": config})
    assert response.status_code == 422
    assert f"{MAX_REQUESTS_PER_CALL:,}" in response.json()["detail"]


def test_an_invalid_config_is_refused(client: TestClient) -> None:
    config = _long_config()
    config["topology"]["nodes"][0]["max_capacity"] = 0
    assert client.post("/runs", json={"config": config}).status_code == 422


# ---------------------------------------------------------------------------
# the stream
# ---------------------------------------------------------------------------


def test_the_stream_opens_with_the_run_and_closes_with_the_result(client: TestClient) -> None:
    run_id = _start(client)
    events = _events(client, run_id)
    names = [event["event"] for event in events]
    assert names[0] == "started"
    assert names[-1] == "result"
    assert names.count("started") == names.count("measured") == names.count("result") == 1
    assert set(names) <= {"started", "tick", "chaos", "feed", "measured", "result"}
    assert [event["id"] for event in events] == list(range(len(events)))


def test_started_says_what_is_running(client: TestClient) -> None:
    run_id = _start(client, playbook="db_failover")
    started = _of(_events(client, run_id), "started")[0]
    assert set(started) == {"run_id", "seed", "seeds", "duration", "metrics_interval", "chaos"}
    assert started["run_id"] == run_id
    assert started["seed"] == 42 and started["seeds"] is None
    assert started["duration"] == 20.0 and started["metrics_interval"] == 1.0
    # The effective schedule: the playbook's events are in it.
    assert started["chaos"] and all(event["target"] == "db" for event in started["chaos"])


def test_the_ticks_are_the_runs_timeseries(client: TestClient) -> None:
    run_id = _start(client, playbook="db_failover")
    events = _events(client, run_id)
    ticks = _of(events, "tick")
    result = _of(events, "result")[0]
    assert len(ticks) == 20
    assert ticks == result["timeseries"]
    assert "edge_flow" in ticks[0] and "capacity" in ticks[0]["per_component"]["db"]


def test_the_result_is_what_simulate_returns(client: TestClient) -> None:
    """Same request, same seed, same body: a client can swap one call for
    the other."""
    body = _body(playbook="db_failover")
    run_id = _start(client, playbook="db_failover")
    result = _of(_events(client, run_id), "result")[0]
    simulated = client.post("/simulate", json={**body, "include_timeseries": True}).json()
    assert result == simulated


def test_the_summary_arrives_before_the_plan(client: TestClient) -> None:
    """``measured`` is the run without the Fix-it plan: after the last tick,
    before ``result``. The verdict does not wait for the plan."""
    run_id = _start(client, playbook="db_failover")
    events = _events(client, run_id)
    names = [event["event"] for event in events]
    last_tick = max(i for i, name in enumerate(names) if name == "tick")
    assert last_tick < names.index("measured") < names.index("result")

    measured = _of(events, "measured")[0]
    final = _of(events, "result")[0]["summary"]
    assert set(measured) == {"summary", "typical_seed"}
    assert measured["typical_seed"] is None
    assert "suggestions" not in measured["summary"] and "fix_outcome" not in measured["summary"]
    assert "suggestions" in final and "fix_outcome" in final
    for key in (
        "resilience_score",
        "requests",
        "failed_requests",
        "p95_latency",
        "findings",
        "verdict_headline",
    ):
        assert measured["summary"][key] == final[key], key


def test_chaos_windows_are_in_the_stream(client: TestClient) -> None:
    run_id = _start(client, playbook="db_failover")
    chaos = _of(_events(client, run_id), "chaos")
    assert chaos and chaos[0]["phase"] == "start" and chaos[0]["node"] == "db"
    assert set(chaos[0]) == {"t", "phase", "event_type", "node", "intensity"}


def test_the_feed_is_narrated_from_the_same_events(client: TestClient) -> None:
    """The feed lines in the stream are ``sim_core.feed.narrate`` over the
    stream's own ticks and chaos events: nothing added, nothing dropped."""
    run_id = _start(client, playbook="db_failover")
    events = _events(client, run_id)
    engine_events = [
        {"type": event["event"], **event["data"]}
        for event in events
        if event["event"] in ("tick", "chaos")
    ]
    feed = _of(events, "feed")
    assert feed and feed == narrate(engine_events)


def test_a_feed_line_follows_the_event_that_caused_it(client: TestClient) -> None:
    run_id = _start(client, playbook="db_failover")
    events = _events(client, run_id)
    first_chaos = next(i for i, event in enumerate(events) if event["event"] == "chaos")
    assert events[first_chaos + 1]["event"] == "feed"
    assert events[first_chaos + 1]["data"]["kind"] == "incident_start"
    assert events[first_chaos + 1]["data"]["t"] == events[first_chaos]["data"]["t"]


def test_a_clean_run_has_no_chaos_events(client: TestClient) -> None:
    run_id = _start(client)
    events = _events(client, run_id)
    assert _of(events, "chaos") == []
    assert _of(events, "started")[0]["chaos"] == []


def test_the_stream_can_be_read_again(client: TestClient) -> None:
    """The run keeps its events: a second reader, or one that arrives after
    the run finished, gets all of them."""
    run_id = _start(client)
    assert _events(client, run_id) == _events(client, run_id)


def test_a_reader_resumes_after_the_last_event_it_saw(client: TestClient) -> None:
    """``EventSource`` reconnects with ``Last-Event-ID``; the stream picks up
    at the next event."""
    run_id = _start(client)
    everything = _events(client, run_id)
    tail = _events(client, run_id, headers={"Last-Event-ID": "3"})
    assert tail[0]["id"] == 4
    assert tail == everything[4:]
    done = _events(client, run_id, headers={"Last-Event-ID": str(everything[-1]["id"])})
    assert done == []


def test_an_unknown_run(client: TestClient) -> None:
    for path in ("/runs/nope", "/runs/nope/events"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"] == "unknown run: 'nope'"


# ---------------------------------------------------------------------------
# the run, without a stream
# ---------------------------------------------------------------------------


def test_a_finished_run_can_be_fetched(client: TestClient) -> None:
    run_id = _start(client)
    result = _of(_events(client, run_id), "result")[0]
    payload = client.get(f"/runs/{run_id}").json()
    assert payload == {"run_id": run_id, "status": "done", "result": result, "error": None}


def test_a_run_reports_running_until_it_is_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sim_core import CloudSimulator

    real = CloudSimulator.attach_fix_plan

    def slow(self: CloudSimulator, summary: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.4)
        return real(self, summary)

    monkeypatch.setattr(CloudSimulator, "attach_fix_plan", slow)
    run_id = _start(client)
    early = client.get(f"/runs/{run_id}").json()
    assert early["status"] == "running" and early["result"] is None and early["error"] is None
    _events(client, run_id)  # reads to the end
    assert client.get(f"/runs/{run_id}").json()["status"] == "done"


# ---------------------------------------------------------------------------
# multi-seed
# ---------------------------------------------------------------------------


def test_a_multi_seed_run_streams_the_typical_run(client: TestClient) -> None:
    """Every seed is run first; then the stream carries the typical run's
    ticks, the one the result's headline and timeline come from."""
    run_id = _start(client, playbook="db_failover", n_seeds=3)
    events = _events(client, run_id)
    names = [event["event"] for event in events]
    result = _of(events, "result")[0]

    assert _of(events, "started")[0]["seeds"] == [42, 43, 44]
    seeds = _of(events, "seed")
    assert [(s["seed"], s["index"], s["of"]) for s in seeds] == [(42, 1, 3), (43, 2, 3), (44, 3, 3)]
    assert all(isinstance(s["resilience_score"], (int, float)) for s in seeds)
    assert max(i for i, name in enumerate(names) if name == "seed") < names.index("tick")

    assert _of(events, "tick") == result["timeseries"]
    measured = _of(events, "measured")[0]
    assert measured["typical_seed"] == result["typical_seed"]
    assert measured["summary"]["resilience_score"] == result["summary"]["resilience_score"]


def test_a_multi_seed_result_is_what_simulate_returns(client: TestClient) -> None:
    body = _body(playbook="db_failover", n_seeds=3)
    run_id = _start(client, playbook="db_failover", n_seeds=3)
    result = _of(_events(client, run_id), "result")[0]
    assert result == client.post("/simulate", json={**body, "include_timeseries": True}).json()


def test_an_explicit_seed_list_wins(client: TestClient) -> None:
    run_id = _start(client, n_seeds=5, seeds=[7, 8])
    events = _events(client, run_id)
    assert _of(events, "started")[0]["seeds"] == [7, 8]
    assert _of(events, "result")[0]["seeds"] == [7, 8]


# ---------------------------------------------------------------------------
# when it goes wrong, and while it is slow
# ---------------------------------------------------------------------------


def test_a_run_that_breaks_ends_the_stream_with_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sim_core import CloudSimulator

    def broken(self: CloudSimulator, summary: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the plan fell over")

    monkeypatch.setattr(CloudSimulator, "attach_fix_plan", broken)
    run_id = _start(client)
    events = _events(client, run_id)
    assert events[-1]["event"] == "error"
    assert events[-1]["data"] == {"detail": "the run failed: the plan fell over"}
    assert "result" not in [event["event"] for event in events]
    # What was measured before it broke was still delivered.
    assert _of(events, "measured")
    payload = client.get(f"/runs/{run_id}").json()
    assert payload["status"] == "failed" and payload["result"] is None
    assert payload["error"] == "the run failed: the plan fell over"


def test_a_quiet_stream_sends_pings(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """While the plan is being verified nothing else is sent; a ping (an
    event with no id, outside the run's own numbering) shows the stream is
    alive."""
    import api.runs

    from sim_core import CloudSimulator

    real = CloudSimulator.attach_fix_plan

    def slow(self: CloudSimulator, summary: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.5)
        return real(self, summary)

    monkeypatch.setattr(CloudSimulator, "attach_fix_plan", slow)
    monkeypatch.setattr(api.runs, "HEARTBEAT_SECONDS", 0.05)
    run_id = _start(client)
    response = client.get(f"/runs/{run_id}/events")
    everything = _parse(response.text)
    pings = [event for event in everything if event["event"] == "ping"]
    assert len(pings) >= 2
    assert all(ping["id"] is None and ping["data"] == {} for ping in pings)
    numbered = [event for event in everything if event["event"] != "ping"]
    assert [event["id"] for event in numbered] == list(range(len(numbered)))
    assert numbered[-1]["event"] == "result"


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_finished_run_is_kept_for_its_ttl() -> None:
    clock = _Clock()
    store = RunStore(max_runs=8, ttl_seconds=60.0, clock=clock)
    run = store.create()
    run.finish({"summary": {}})
    clock.now += 59.0
    assert store.get(run.run_id) is run
    clock.now += 2.0
    assert store.get(run.run_id) is None


def test_a_running_run_is_never_dropped_for_age() -> None:
    clock = _Clock()
    store = RunStore(max_runs=8, ttl_seconds=60.0, clock=clock)
    run = store.create()
    clock.now += 3600.0
    assert store.get(run.run_id) is run
    assert run.status == "running"


def test_a_full_store_drops_its_oldest_finished_run() -> None:
    clock = _Clock()
    store = RunStore(max_runs=2, ttl_seconds=600.0, clock=clock)
    first, second = store.create(), store.create()
    first.finish({"summary": {}})
    clock.now += 1.0
    second.fail("boom")
    third = store.create()
    assert store.get(first.run_id) is None
    assert store.get(second.run_id) is second and second.status == "failed"
    assert store.get(third.run_id) is third


def test_a_store_full_of_running_runs_refuses_another() -> None:
    store = RunStore(max_runs=2, ttl_seconds=600.0, clock=_Clock())
    store.create(), store.create()
    assert store.create() is None


def test_the_api_says_it_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """No room for another run is a 429 with a sentence, not a hang."""
    app = create_app()
    app.state.runs = RunStore(max_runs=0, ttl_seconds=600.0)
    response = TestClient(app).post("/runs", json=_body())
    assert response.status_code == 429
    assert "busy" in response.json()["detail"]
