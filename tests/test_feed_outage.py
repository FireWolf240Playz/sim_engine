"""The feed does not call a node "recovered" while it has no slots at all.

Measured before the fix (2026-11-15, ``checkout_stack`` under ``db_failover``,
over a real ``/runs`` stream): ``orders-db at 100%`` at t=24, the full outage
from t=30, then ``orders-db back to 0%`` at t=36, in the middle of it. A node
with no live slots and nothing waiting for one reads 0% utilization
(``Component.utilization``): it is down, not recovered.
"""

from __future__ import annotations

from typing import Any

from sim_core.feed import narrate


def _tick(t: float, utilization: float, capacity: int) -> dict[str, Any]:
    return {
        "type": "tick",
        "time": t,
        "failed": 0,
        "per_component": {
            "db": {
                "utilization": utilization,
                "queue_length": 0.0,
                "capacity": capacity,
                "in_use": 0,
                "retries": 0,
            },
        },
    }


def test_a_node_with_no_slots_is_not_recovered() -> None:
    lines = narrate([_tick(1.0, 1.0, 4), _tick(2.0, 0.0, 0), _tick(3.0, 0.0, 0)])
    assert [line["text"] for line in lines] == ["db at 100%"]


def test_it_recovers_once_its_slots_are_back() -> None:
    lines = narrate([_tick(1.0, 1.0, 4), _tick(2.0, 0.0, 0), _tick(3.0, 0.3, 4)])
    assert [(line["t"], line["text"]) for line in lines] == [
        (1.0, "db at 100%"),
        (3.0, "db back to 30%"),
    ]


def test_a_tick_without_capacity_reads_as_before() -> None:
    """Ticks from before 3.1 carry no ``capacity``: utilization alone decides."""
    old = [
        {"type": "tick", "time": 1.0, "per_component": {"db": {"utilization": 0.95}}},
        {"type": "tick", "time": 2.0, "per_component": {"db": {"utilization": 0.5}}},
    ]
    assert [line["kind"] for line in narrate(old)] == ["saturated", "recovered"]
