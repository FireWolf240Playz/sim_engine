"""Contract for roadmap 3.1, the event feed (`.agents/tasks/3.1-event-sink.md`).

``sim_core/feed.py`` turns the engine's events (``tick`` and ``chaos``, see
``tests/test_event_sink.py``) into the lines a person reads while the run
plays: "db at 97%", "3 retries on payment-service", "component failure on db
ended". Like the findings, every word is the engine's: a client prints
``text`` as it is and never writes a line of its own.

Pure: no SimPy, no ``sim_core`` import, the same events always give the same
lines.

Make these pass. Do not edit them.
"""

from __future__ import annotations

import json
from typing import Any

from sim_core.feed import RECOVERED_BELOW, SATURATED_AT, Narrator, narrate

from sim_core import CloudSimulator, SimulationConfig
from sim_core.config import ChaosEvent, ComponentConfig, Edge, GraphTopologyConfig, TrafficPattern

LINE_KEYS = {"t", "kind", "severity", "node", "text"}


def _tick(
    t: float,
    util: dict[str, float],
    *,
    retries: dict[str, int] | None = None,
    failed: int = 0,
) -> dict[str, Any]:
    return {
        "type": "tick",
        "time": t,
        "p50_latency": None,
        "p95_latency": None,
        "sla_met": None,
        "arrived": 0,
        "completed": 0,
        "failed": failed,
        "per_component": {
            name: {
                "utilization": value,
                "queue_length": 0.0,
                "capacity": 4,
                "in_use": 0,
                "retries": (retries or {}).get(name, 0),
            }
            for name, value in util.items()
        },
        "edge_flow": [],
    }


def _chaos(
    t: float, phase: str, event_type: str, node: str | None, intensity: float
) -> dict[str, Any]:
    return {
        "type": "chaos",
        "t": t,
        "phase": phase,
        "event_type": event_type,
        "node": node,
        "intensity": intensity,
    }


def _texts(lines: list[dict[str, Any]]) -> list[str]:
    return [line["text"] for line in lines]


# ---------------------------------------------------------------------------
# the shape of a line
# ---------------------------------------------------------------------------


def test_a_line_has_five_fields() -> None:
    lines = narrate([_tick(4.0, {"db": 0.97})])
    assert lines == [
        {"t": 4.0, "kind": "saturated", "severity": "warn", "node": "db", "text": "db at 97%"}
    ]
    assert set(lines[0]) == LINE_KEYS


def test_a_quiet_tick_says_nothing() -> None:
    assert (
        narrate([_tick(0.0, {"db": 0.2, "worker": 0.5}), _tick(1.0, {"db": 0.4, "worker": 0.5})])
        == []
    )


def test_an_event_it_does_not_know_is_skipped() -> None:
    assert narrate([{"type": "something_new", "t": 1.0}]) == []


# ---------------------------------------------------------------------------
# saturation: said once, with hysteresis
# ---------------------------------------------------------------------------


def test_thresholds() -> None:
    assert SATURATED_AT == 0.9
    assert RECOVERED_BELOW == 0.75


def test_saturation_is_said_once_while_it_lasts() -> None:
    lines = narrate(
        [_tick(float(t), {"db": u}) for t, u in enumerate([0.5, 0.92, 0.99, 1.0, 0.95])]
    )
    assert _texts(lines) == ["db at 92%"]
    assert lines[0]["t"] == 1.0


def test_exactly_at_the_threshold_counts() -> None:
    assert _texts(narrate([_tick(1.0, {"db": 0.9})])) == ["db at 90%"]
    assert narrate([_tick(1.0, {"db": 0.899})]) == []


def test_a_dip_that_stays_busy_is_not_a_recovery() -> None:
    """Between the two thresholds nothing is said: 0.95 -> 0.8 -> 0.95 is one
    saturation, not three lines."""
    lines = narrate([_tick(float(t), {"db": u}) for t, u in enumerate([0.95, 0.8, 0.95, 0.76])])
    assert _texts(lines) == ["db at 95%"]


def test_recovery_then_a_second_saturation() -> None:
    lines = narrate([_tick(float(t), {"db": u}) for t, u in enumerate([0.95, 0.62, 0.3, 0.91])])
    assert [(line["t"], line["kind"], line["severity"], line["text"]) for line in lines] == [
        (0.0, "saturated", "warn", "db at 95%"),
        (1.0, "recovered", "info", "db back to 62%"),
        (3.0, "saturated", "warn", "db at 91%"),
    ]


def test_each_node_is_tracked_on_its_own() -> None:
    lines = narrate(
        [
            _tick(1.0, {"worker": 0.95, "db": 0.5}),
            _tick(2.0, {"worker": 0.95, "db": 0.93}),
            _tick(3.0, {"worker": 0.5, "db": 0.93}),
        ]
    )
    assert [(line["t"], line["node"], line["kind"]) for line in lines] == [
        (1.0, "worker", "saturated"),
        (2.0, "db", "saturated"),
        (3.0, "worker", "recovered"),
    ]


# ---------------------------------------------------------------------------
# retries and failures
# ---------------------------------------------------------------------------


def test_retries_are_counted_per_node() -> None:
    lines = narrate(
        [_tick(63.0, {"payment-service": 0.5, "db": 0.5}, retries={"payment-service": 3, "db": 1})]
    )
    assert [(line["kind"], line["severity"], line["node"], line["text"]) for line in lines] == [
        ("retries", "warn", "payment-service", "3 retries on payment-service"),
        ("retries", "warn", "db", "1 retry on db"),
    ]


def test_failures_are_one_line_for_the_tick() -> None:
    assert narrate([_tick(8.0, {"db": 0.5}, failed=2)]) == [
        {
            "t": 8.0,
            "kind": "failures",
            "severity": "crit",
            "node": None,
            "text": "2 requests failed",
        }
    ]
    assert _texts(narrate([_tick(8.0, {"db": 0.5}, failed=1)])) == ["1 request failed"]


def test_the_order_inside_one_tick() -> None:
    """Load first, then retries, then failures: cause before effect. Nodes in
    the tick's own order."""
    lines = narrate([_tick(5.0, {"worker": 0.95, "db": 0.99}, retries={"db": 4}, failed=3)])
    assert _texts(lines) == ["worker at 95%", "db at 99%", "4 retries on db", "3 requests failed"]
    assert {line["t"] for line in lines} == {5.0}


# ---------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------


def test_a_component_failure() -> None:
    lines = narrate(
        [
            _chaos(20.0, "start", "component_failure", "order-db", 0.5),
            _chaos(35.0, "end", "component_failure", "order-db", 0.5),
        ]
    )
    assert lines == [
        {
            "t": 20.0,
            "kind": "incident_start",
            "severity": "crit",
            "node": "order-db",
            "text": "component failure on order-db: 50% of its capacity is gone",
        },
        {
            "t": 35.0,
            "kind": "incident_end",
            "severity": "info",
            "node": "order-db",
            "text": "component failure on order-db ended",
        },
    ]


def test_a_full_outage() -> None:
    line = narrate([_chaos(20.0, "start", "component_failure", "db", 1.0)])[0]
    assert line["text"] == "component failure on db: 100% of its capacity is gone"


def test_a_latency_spike_has_no_node() -> None:
    lines = narrate(
        [
            _chaos(10.0, "start", "network_latency", None, 0.5),
            _chaos(25.0, "end", "network_latency", None, 0.5),
        ]
    )
    assert [(line["node"], line["text"]) for line in lines] == [
        (None, "network latency spike: every node is 1.5x slower"),
        (None, "network latency spike ended"),
    ]
    assert narrate([_chaos(1.0, "start", "network_latency", None, 1.0)])[0]["text"].endswith(
        "2x slower"
    )


def test_a_cache_outage() -> None:
    lines = narrate(
        [
            _chaos(10.0, "start", "cache_outage", "redis", 0.8),
            _chaos(25.0, "end", "cache_outage", "redis", 0.8),
        ]
    )
    assert _texts(lines) == [
        "cache outage on redis: 80% of its hit rate is gone",
        "cache outage on redis ended",
    ]
    assert [line["kind"] for line in lines] == ["incident_start", "incident_end"]


# ---------------------------------------------------------------------------
# one event at a time, and on a real run
# ---------------------------------------------------------------------------


def test_the_narrator_reads_one_event_at_a_time() -> None:
    """A stream hands events over one by one; the lines are the same as
    narrating the finished list."""
    events = [
        _tick(0.0, {"db": 0.5}),
        _chaos(1.0, "start", "component_failure", "db", 0.5),
        _tick(1.0, {"db": 0.95}, retries={"db": 2}),
        _tick(2.0, {"db": 0.99}, failed=1),
        _chaos(2.5, "end", "component_failure", "db", 0.5),
        _tick(3.0, {"db": 0.4}),
    ]
    narrator = Narrator()
    one_by_one = [line for event in events for line in narrator.read(event)]
    assert one_by_one == narrate(events)
    assert _texts(one_by_one) == [
        "component failure on db: 50% of its capacity is gone",
        "db at 95%",
        "2 retries on db",
        "1 request failed",
        "component failure on db ended",
        "db back to 40%",
    ]


def test_two_narrators_do_not_share_state() -> None:
    first, second = Narrator(), Narrator()
    assert _texts(first.read(_tick(0.0, {"db": 0.95}))) == ["db at 95%"]
    assert _texts(second.read(_tick(0.0, {"db": 0.95}))) == ["db at 95%"]
    assert first.read(_tick(1.0, {"db": 0.95})) == []


def test_a_real_run_tells_its_story_in_order() -> None:
    """An outage on a small db, watched through the engine's own events: the
    incident opens, the db saturates, the incident closes."""
    config = SimulationConfig(
        seed=42,
        duration=30.0,
        metrics_interval=1.0,
        sla_target=10.0,
        topology=GraphTopologyConfig(
            nodes=[
                ComponentConfig(name="worker", role="worker", max_capacity=6, service_time=0.5),
                ComponentConfig(name="db", role="database", max_capacity=4, service_time=1.0),
            ],
            edges=[Edge(source="worker", target="db", probability=1.0)],
        ),
        traffic=TrafficPattern(base_rps=2.0, duration=30.0),
        chaos=[
            ChaosEvent(
                event_type="component_failure",
                target="db",
                intensity=0.75,
                start_time=8.0,
                duration=10.0,
                interval=100.0,
            )
        ],
    )
    events: list[dict[str, Any]] = []
    CloudSimulator(config, event_sink=events.append).run(suggest=False)
    lines = narrate(events)

    assert json.loads(json.dumps(lines)) == lines
    assert all(set(line) == LINE_KEYS for line in lines)
    assert [line["t"] for line in lines] == sorted(line["t"] for line in lines)
    kinds = [line["kind"] for line in lines]
    assert kinds[0] == "incident_start" and lines[0]["t"] == 8.0
    assert lines[0]["text"] == "component failure on db: 75% of its capacity is gone"
    saturated = next(line for line in lines if line["kind"] == "saturated")
    assert saturated["node"] == "db" and 8.0 < saturated["t"] <= 18.0
    assert kinds.index("incident_end") > kinds.index("saturated")
    assert narrate(events) == lines  # the same events, the same lines
