"""Contract for roadmap 3.1, engine half: the run can be watched as it happens
(`.agents/tasks/3.1-event-sink.md`).

Measured before these tests existed (2026-11-15, seed 42):

- The simulation is not the wait. A 120 s run of the 20-node example takes
  0.09 s; the Fix-it plan that follows takes 24-37 s. On the 15-node example
  it is 0.06 s against 6-12 s. A caller got nothing until both were done.
- A tick carried utilization and queue length only: not how many requests
  arrived, finished or failed in it, not the node's live capacity (so an
  outage was invisible), not its retries, not which edges carried traffic.
- A chaos window with a random victim named its victim nowhere.

So: ``CloudSimulator`` takes an ``event_sink``, a tick says what happened in
it, the engine reports each chaos window as it opens and closes, and the plan
can be attached after the summary instead of inside ``run()``.

Make these pass. Do not edit them.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from sim_core import CloudSimulator, SimulationConfig
from sim_core.config import (
    CacheConfig,
    ChaosEvent,
    ComponentConfig,
    Edge,
    GraphTopologyConfig,
    TrafficPattern,
)


def _config(**overrides: Any) -> SimulationConfig:
    """worker -> db, 20 s, a tick a second. The db is small enough to queue."""
    fields: dict[str, Any] = dict(
        seed=42,
        duration=20.0,
        metrics_interval=1.0,
        sla_target=10.0,
        topology=GraphTopologyConfig(
            nodes=[
                ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=0.5),
                ComponentConfig(name="db", role="database", max_capacity=4, service_time=1.0),
            ],
            edges=[Edge(source="worker", target="db", probability=1.0)],
        ),
        traffic=TrafficPattern(base_rps=3.0, duration=20.0),
        chaos=[],
    )
    fields.update(overrides)
    return SimulationConfig(**fields)


def _db_outage() -> ChaosEvent:
    """Half the db's slots gone from t=5 to t=11, once."""
    return ChaosEvent(
        event_type="component_failure",
        target="db",
        intensity=0.5,
        start_time=5.0,
        duration=6.0,
        interval=100.0,
    )


def _watch(config: SimulationConfig) -> tuple[CloudSimulator, list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    simulator = CloudSimulator(config, event_sink=events.append)
    summary = simulator.run(suggest=False)
    return simulator, events, summary


def _ticks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "tick"]


# ---------------------------------------------------------------------------
# the sink
# ---------------------------------------------------------------------------


def test_watching_a_run_does_not_change_it() -> None:
    """The sink observes. Same seed, same summary, with or without one."""
    config = _config(chaos=[_db_outage()])
    _, events, watched = _watch(config)
    assert events
    assert watched == CloudSimulator(config).run(suggest=False)


def test_the_sink_is_optional() -> None:
    simulator = CloudSimulator(_config())
    assert simulator.run(suggest=False)["requests"] > 0


def test_every_tick_is_sent_as_it_is_sampled() -> None:
    """One ``tick`` event per sample, and it is that sample's ``timeseries()``
    entry: the stream and the finished run can never disagree."""
    simulator, events, _ = _watch(_config(chaos=[_db_outage()]))
    ticks = _ticks(events)
    series = simulator.collector.timeseries()
    assert len(ticks) == len(series) == 20
    for sent, kept in zip(ticks, series, strict=True):
        assert {key: value for key, value in sent.items() if key != "type"} == kept


def test_events_arrive_in_simulated_time_order() -> None:
    _, events, _ = _watch(_config(chaos=[_db_outage()]))
    times = [event["time"] if event["type"] == "tick" else event["t"] for event in events]
    assert times == sorted(times)
    assert {event["type"] for event in events} == {"tick", "chaos"}


def test_every_event_is_plain_json() -> None:
    """Events go straight onto a wire: no enum, no numpy scalar, no model."""
    _, events, _ = _watch(_config(chaos=[_db_outage()]))
    assert json.loads(json.dumps(events)) == events


# ---------------------------------------------------------------------------
# what a tick says
# ---------------------------------------------------------------------------


def test_a_tick_counts_what_happened_in_it() -> None:
    """``arrived`` / ``completed`` / ``failed`` are counts for the window that
    ends at the tick, and they add up to the requests the collector holds."""
    # Traffic stops at 12 s, so every request has finished by the last tick.
    simulator, events, summary = _watch(
        _config(traffic=TrafficPattern(base_rps=3.0, duration=12.0))
    )
    ticks = _ticks(events)
    records = simulator.collector.requests
    assert max(r.end for r in records) < ticks[-1]["time"]
    assert all(isinstance(t[key], int) for t in ticks for key in ("arrived", "completed", "failed"))
    assert sum(t["arrived"] for t in ticks) == len(records) == summary["requests"]
    assert sum(t["completed"] for t in ticks) == sum(1 for r in records if r.success)
    assert sum(t["failed"] for t in ticks) == sum(1 for r in records if not r.success)
    assert ticks[0]["arrived"] == 0 and ticks[0]["completed"] == 0  # sampled at t=0
    # A window's counts are that window's: the requests that ended inside it.
    for before, tick in itertools.pairwise(ticks):
        ended = [r for r in records if before["time"] < r.end <= tick["time"]]
        assert tick["completed"] + tick["failed"] == len(ended)


def test_a_tick_keeps_the_keys_it_always_had() -> None:
    _, events, _ = _watch(_config())
    tick = _ticks(events)[5]
    assert {"time", "p50_latency", "p95_latency", "sla_met", "per_component"} <= set(tick)
    assert {"utilization", "queue_length"} <= set(tick["per_component"]["db"])


def test_a_tick_shows_the_live_capacity() -> None:
    """An outage is visible while it lasts: the node's slots, as they are now."""
    _, events, _ = _watch(_config(chaos=[_db_outage()]))
    capacity = {t["time"]: t["per_component"]["db"]["capacity"] for t in _ticks(events)}
    assert capacity[4.0] == 4
    assert all(capacity[float(t)] == 2 for t in range(6, 11))
    assert capacity[12.0] == 4
    worker = {t["per_component"]["worker"]["capacity"] for t in _ticks(events)}
    assert worker == {6}


def test_a_tick_shows_slots_in_use() -> None:
    _, events, _ = _watch(_config())
    for tick in _ticks(events):
        for node in tick["per_component"].values():
            assert 0 <= node["in_use"] <= node["capacity"]
    assert any(t["per_component"]["db"]["in_use"] > 0 for t in _ticks(events))


def test_a_tick_counts_each_nodes_retries() -> None:
    """Retries per node, in the tick's window; over the run they are the
    collector's own count."""
    nodes = [
        ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=0.5),
        ComponentConfig(
            name="db",
            role="database",
            max_capacity=1,
            service_time=1.5,
            timeout=0.5,
            retry_limit=2,
            retry_backoff=0.2,
        ),
    ]
    config = _config(
        topology=GraphTopologyConfig(nodes=nodes, edges=[Edge(source="worker", target="db")]),
        # Traffic stops early so every retry has happened by the last tick.
        traffic=TrafficPattern(base_rps=3.0, duration=12.0),
    )
    simulator, events, summary = _watch(config)
    ticks = _ticks(events)
    assert summary["total_retries"] > 5
    assert (
        sum(t["per_component"]["db"]["retries"] for t in ticks)
        == simulator.collector.retry_counts["db"]
    )
    assert sum(t["per_component"]["worker"]["retries"] for t in ticks) == 0
    assert sum(t["failed"] for t in ticks) > 0


def test_a_tick_counts_the_traffic_on_each_edge() -> None:
    """``edge_flow[i]`` is how many requests set off along
    ``config.topology.edges[i]`` in the window."""
    simulator, events, _ = _watch(_config())
    ticks = _ticks(events)
    assert all(len(t["edge_flow"]) == 1 for t in ticks)
    flow = sum(t["edge_flow"][0] for t in ticks)
    last = ticks[-1]["time"]
    through = sum(1 for r in simulator.collector.requests if r.success and r.end <= last)
    assert through <= flow <= sum(t["arrived"] for t in ticks)
    assert flow > 30


def test_alternative_edges_share_the_flow() -> None:
    """A balancer picks one backend per request: the two edges' counts add up
    to the requests it sent on, and a third, untaken edge stays at zero."""
    nodes = [
        ComponentConfig(name="lb", role="load_balancer", max_capacity=20, service_time=0.05),
        ComponentConfig(name="a", role="worker", max_capacity=8, service_time=0.3),
        ComponentConfig(name="b", role="worker", max_capacity=8, service_time=0.3),
        ComponentConfig(name="audit", role="generic", max_capacity=2, service_time=0.1),
    ]
    edges = [
        Edge(source="lb", target="a", probability=0.5, group="workers"),
        Edge(source="lb", target="b", probability=0.5, group="workers"),
        Edge(source="a", target="audit", probability=1e-9),
    ]
    _, events, _ = _watch(_config(topology=GraphTopologyConfig(nodes=nodes, edges=edges)))
    ticks = _ticks(events)
    to_a = sum(t["edge_flow"][0] for t in ticks)
    to_b = sum(t["edge_flow"][1] for t in ticks)
    assert to_a > 10 and to_b > 10
    assert to_a + to_b <= sum(t["arrived"] for t in ticks)
    assert to_a + to_b >= sum(t["completed"] for t in ticks)
    assert sum(t["edge_flow"][2] for t in ticks) == 0


def test_timeseries_carries_the_same_ticks_without_a_sink() -> None:
    """The new fields are part of ``timeseries()`` itself, sink or no sink."""
    config = _config(chaos=[_db_outage()])
    plain = CloudSimulator(config)
    plain.run(suggest=False)
    watched, _, _ = _watch(config)
    assert plain.collector.timeseries() == watched.collector.timeseries()
    assert "edge_flow" in plain.collector.timeseries()[3]


# ---------------------------------------------------------------------------
# chaos, as it happens
# ---------------------------------------------------------------------------


def test_a_chaos_window_is_reported_when_it_opens_and_closes() -> None:
    _, events, _ = _watch(_config(chaos=[_db_outage()]))
    chaos = [event for event in events if event["type"] == "chaos"]
    assert chaos == [
        {
            "type": "chaos",
            "t": 5.0,
            "phase": "start",
            "event_type": "component_failure",
            "node": "db",
            "intensity": 0.5,
        },
        {
            "type": "chaos",
            "t": 11.0,
            "phase": "end",
            "event_type": "component_failure",
            "node": "db",
            "intensity": 0.5,
        },
    ]


def test_the_start_comes_before_the_tick_that_shows_it() -> None:
    """The window opens at t=5 and the tick at t=6 is the first to show two
    slots, so a viewer reads the cause before the effect."""
    _, events, _ = _watch(_config(chaos=[_db_outage()]))
    start = next(i for i, e in enumerate(events) if e["type"] == "chaos" and e["phase"] == "start")
    shown = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "tick" and e["per_component"]["db"]["capacity"] == 2
    )
    assert start < shown


def test_a_random_victim_is_named() -> None:
    """Without a target the engine draws the victim; the event says who."""
    event = ChaosEvent(
        event_type="component_failure", intensity=0.5, start_time=3.0, duration=2.0, interval=4.0
    )
    _, events, _ = _watch(_config(chaos=[event]))
    starts = [e for e in events if e["type"] == "chaos" and e["phase"] == "start"]
    ends = [e for e in events if e["type"] == "chaos" and e["phase"] == "end"]
    assert [e["t"] for e in starts] == [3.0, 9.0, 15.0]
    assert all(e["node"] in {"worker", "db"} for e in starts)
    assert [e["node"] for e in ends] == [e["node"] for e in starts]


def test_a_latency_spike_names_no_node() -> None:
    event = ChaosEvent(
        event_type="network_latency", intensity=0.5, start_time=4.0, duration=3.0, interval=100.0
    )
    _, events, _ = _watch(_config(chaos=[event]))
    chaos = [e for e in events if e["type"] == "chaos"]
    assert [(e["phase"], e["t"], e["node"]) for e in chaos] == [
        ("start", 4.0, None),
        ("end", 7.0, None),
    ]


def test_a_cache_outage_names_the_cache_it_hit() -> None:
    nodes = [
        ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=0.5),
        CacheConfig(name="redis", role="cache", max_capacity=8, service_time=0.1, hit_rate=0.8),
        ComponentConfig(name="db", role="database", max_capacity=4, service_time=1.0),
    ]
    edges = [Edge(source="worker", target="redis"), Edge(source="redis", target="db")]
    event = ChaosEvent(
        event_type="cache_outage", intensity=1.0, start_time=4.0, duration=3.0, interval=100.0
    )
    config = _config(topology=GraphTopologyConfig(nodes=nodes, edges=edges), chaos=[event])
    _, events, _ = _watch(config)
    chaos = [e for e in events if e["type"] == "chaos"]
    assert [(e["phase"], e["node"]) for e in chaos] == [("start", "redis"), ("end", "redis")]


def test_a_window_still_open_at_the_end_has_no_end_event() -> None:
    event = ChaosEvent(
        event_type="component_failure",
        target="db",
        intensity=0.5,
        start_time=15.0,
        duration=30.0,
        interval=100.0,
    )
    _, events, _ = _watch(_config(chaos=[event]))
    assert [e["phase"] for e in events if e["type"] == "chaos"] == ["start"]


# ---------------------------------------------------------------------------
# the plan, after the summary
# ---------------------------------------------------------------------------


def test_the_plan_can_be_attached_after_the_summary() -> None:
    """``run(suggest=False)`` then ``attach_fix_plan`` is ``run()``: a caller
    can show the verdict first and the verified plan when it is ready."""
    config = _config(chaos=[_db_outage()])
    simulator = CloudSimulator(config)
    summary = simulator.run(suggest=False)
    assert "suggestions" not in summary and "fix_outcome" not in summary
    returned = simulator.attach_fix_plan(summary)
    assert returned is summary
    assert summary == CloudSimulator(config).run()
    assert "suggestions" in summary and "fix_outcome" in summary


def test_the_plan_needs_the_run_first() -> None:
    simulator = CloudSimulator(_config())
    with pytest.raises(RuntimeError, match="run"):
        simulator.attach_fix_plan({})
