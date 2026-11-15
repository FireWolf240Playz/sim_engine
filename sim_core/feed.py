"""The event feed: a run's events, told in short plain-English lines.

A :class:`Narrator` reads the engine's events (``tick`` and ``chaos``, see
:class:`sim_core.engine.CloudSimulator`) one at a time and returns the lines
a person reads while the run plays::

    {"t": 62.0, "kind": "saturated", "severity": "warn", "node": "db",
     "text": "db at 97%"}

Like the findings, every word is the engine's: a client prints ``text`` as
it is. Pure: no SimPy, no ``sim_core`` import, and the same events always
give the same lines.

What is said, and when:

- ``incident_start`` (crit) / ``incident_end`` (info): a chaos window opens
  or closes;
- ``saturated`` (warn): a node's utilization reaches :data:`SATURATED_AT`.
  It is said once; the node is flagged until it drops below
  :data:`RECOVERED_BELOW`, which is said as ``recovered`` (info). In between
  nothing is said, so a busy node that wobbles is one line, not ten. A node
  with no live slots is never said to have recovered: it reads 0% because
  nothing can reach it;
- ``retries`` (warn): a node retried in the tick's window;
- ``failures`` (crit): requests failed in the tick's window.

Inside one tick: load lines in the tick's node order, then retries in node
order, then failures, so the cause reads before the effect.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Utilization at which a node is said to be saturated (the findings' crit line).
SATURATED_AT: float = 0.9

#: Utilization a saturated node must drop below to be said to have recovered.
RECOVERED_BELOW: float = 0.75

#: How each chaos event type is named in a line.
_INCIDENT_NAMES: dict[str, str] = {
    "component_failure": "component failure",
    "network_latency": "network latency spike",
    "cache_outage": "cache outage",
}

Line = dict[str, Any]


def _line(t: float, kind: str, severity: str, node: str | None, text: str) -> Line:
    return {"t": t, "kind": kind, "severity": severity, "node": node, "text": text}


def _percent(fraction: float) -> int:
    return round(fraction * 100)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


class Narrator:
    """Turns one run's events into feed lines, one event at a time.

    Holds which nodes are flagged as saturated, so each run needs its own.
    """

    def __init__(self) -> None:
        self._saturated: set[str] = set()

    def read(self, event: Mapping[str, Any]) -> list[Line]:
        """The lines ``event`` adds to the feed (often none)."""
        kind = event.get("type")
        if kind == "tick":
            return self._tick(event)
        if kind == "chaos":
            return [self._chaos(event)]
        return []

    def _chaos(self, event: Mapping[str, Any]) -> Line:
        event_type = str(event["event_type"])
        name = _INCIDENT_NAMES.get(event_type, event_type.replace("_", " "))
        node: str | None = event["node"]
        where = f"{name} on {node}" if node is not None else name
        t = float(event["t"])
        if event["phase"] == "end":
            return _line(t, "incident_end", "info", node, f"{where} ended")
        intensity = float(event["intensity"])
        if event_type == "network_latency":
            detail = f"every node is {1.0 + intensity:g}x slower"
        elif event_type == "cache_outage":
            detail = f"{_percent(intensity)}% of its hit rate is gone"
        else:
            detail = f"{_percent(intensity)}% of its capacity is gone"
        return _line(t, "incident_start", "crit", node, f"{where}: {detail}")

    def _tick(self, tick: Mapping[str, Any]) -> list[Line]:
        t = float(tick["time"])
        nodes: Mapping[str, Mapping[str, Any]] = tick["per_component"]
        lines: list[Line] = []
        for name, node in nodes.items():
            utilization = float(node["utilization"])
            # A node with no live slots and nothing waiting reads 0%: it is
            # down, not recovered, so it stays flagged until its slots return.
            down = node.get("capacity") == 0
            if name not in self._saturated and utilization >= SATURATED_AT:
                self._saturated.add(name)
                text = f"{name} at {_percent(utilization)}%"
                lines.append(_line(t, "saturated", "warn", name, text))
            elif name in self._saturated and utilization < RECOVERED_BELOW and not down:
                self._saturated.discard(name)
                text = f"{name} back to {_percent(utilization)}%"
                lines.append(_line(t, "recovered", "info", name, text))
        for name, node in nodes.items():
            retries = int(node.get("retries", 0))
            if retries > 0:
                text = f"{_plural(retries, 'retry', 'retries')} on {name}"
                lines.append(_line(t, "retries", "warn", name, text))
        failed = int(tick.get("failed", 0))
        if failed > 0:
            text = f"{_plural(failed, 'request', 'requests')} failed"
            lines.append(_line(t, "failures", "crit", None, text))
        return lines


def narrate(events: Iterable[Mapping[str, Any]]) -> list[Line]:
    """Every line a fresh :class:`Narrator` says over ``events``, in order."""
    narrator = Narrator()
    return [line for event in events for line in narrator.read(event)]
