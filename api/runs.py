"""Runs kept in memory, and their event streams (roadmap 3.1).

``POST /runs`` creates a :class:`Run` in the app's :class:`RunStore` and runs
it on a worker thread; the worker :meth:`Run.publish`es every event as it
happens. A reader of ``GET /runs/{id}/events`` gets the events through
:func:`sse`: everything so far, then each new one as it is published, until
the run ends.

A run keeps its events. A reader that arrives late, reads twice, or
reconnects with ``Last-Event-ID`` gets what it missed. Events are stored as
JSON text, serialised when they are published: the worker goes on to change
the dicts it published (the Fix-it plan is attached to the summary that
``measured`` carried), and a reader must see them as they were.

This in-memory store is the stand-in for the ``runs`` table of Wave 4.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder

#: Seconds of silence after which the stream sends a ``ping``, so a client
#: (and any proxy between) can tell a slow plan from a dead connection. Read
#: on every wait, so a test can shorten it.
HEARTBEAT_SECONDS: float = 15.0

#: Runs the store holds at once, running or finished.
MAX_RUNS: int = 64

#: How long a finished run stays readable.
RUN_TTL_SECONDS: float = 900.0

RunStatus = Literal["running", "done", "failed"]


class Run:
    """One run's status, result and numbered events. Thread-safe."""

    def __init__(self, run_id: str, clock: Callable[[], float]) -> None:
        self.run_id = run_id
        self.status: RunStatus = "running"
        #: The ``/simulate`` body, once done.
        self.result: dict[str, Any] | None = None
        #: Why it failed, once failed.
        self.error: str | None = None
        #: When it finished, on the store's clock; ``None`` while running.
        self.finished_at: float | None = None
        self._clock = clock
        self._events: list[tuple[str, str]] = []
        self._changed = threading.Condition()

    def publish(self, name: str, data: Any) -> None:
        """Append one event, serialised now."""
        text = json.dumps(jsonable_encoder(data), separators=(",", ":"), allow_nan=False)
        with self._changed:
            self._events.append((name, text))
            self._changed.notify_all()

    def finish(self, result: dict[str, Any]) -> None:
        """Publish ``result`` (the ``/simulate`` body) and close the run."""
        self.result = jsonable_encoder(result)
        self.publish("result", self.result)
        self._close("done")

    def fail(self, detail: str) -> None:
        """Publish ``error`` and close the run."""
        self.error = detail
        self.publish("error", {"detail": detail})
        self._close("failed")

    def _close(self, status: RunStatus) -> None:
        with self._changed:
            self.status = status
            self.finished_at = self._clock()
            self._changed.notify_all()

    def read(self, start: int, timeout: float) -> tuple[list[tuple[int, str, str]], bool]:
        """Events from index ``start`` on, waiting up to ``timeout`` seconds
        for one when there is none yet; and whether the run has ended."""
        with self._changed:
            if len(self._events) <= start and self.status == "running":
                self._changed.wait(timeout)
            batch = [(i, *self._events[i]) for i in range(start, len(self._events))]
            return batch, self.status != "running"


def sse(run: Run, last_seen: int = -1) -> Iterator[str]:
    """The run's events as server-sent events, from ``last_seen + 1`` on.

    Each event carries its index as ``id`` (what a browser sends back as
    ``Last-Event-ID`` when it reconnects). A ``ping`` carries none: it is not
    one of the run's events. Ends once the run's last event has been sent.
    """
    index = max(last_seen + 1, 0)
    while True:
        batch, ended = run.read(index, HEARTBEAT_SECONDS)
        for event_id, name, data in batch:
            yield f"id: {event_id}\nevent: {name}\ndata: {data}\n\n"
        index += len(batch)
        if ended:
            # The final events can land between two reads: drain, then stop.
            if run.read(index, 0.0)[0]:
                continue
            return
        if not batch:
            yield "event: ping\ndata: {}\n\n"


class RunStore:
    """The runs one app holds.

    A finished run expires ``ttl_seconds`` after it finished; a running run
    never expires. When the store is full, :meth:`create` makes room by
    dropping the oldest finished run, and returns ``None`` when every run is
    still running.
    """

    def __init__(
        self,
        max_runs: int = MAX_RUNS,
        ttl_seconds: float = RUN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_runs = max_runs
        self._ttl = ttl_seconds
        self._clock = clock
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _expire(self) -> None:
        now = self._clock()
        for run_id, run in list(self._runs.items()):
            if run.finished_at is not None and now - run.finished_at > self._ttl:
                del self._runs[run_id]

    def create(self) -> Run | None:
        """A new running run with a fresh id, or ``None`` when there is no room."""
        with self._lock:
            self._expire()
            if len(self._runs) >= self._max_runs:
                finished = [run for run in self._runs.values() if run.finished_at is not None]
                if not finished:
                    return None
                oldest = min(finished, key=lambda run: run.finished_at or 0.0)
                del self._runs[oldest.run_id]
            run = Run(secrets.token_urlsafe(16), self._clock)
            self._runs[run.run_id] = run
            return run

    def get(self, run_id: str) -> Run | None:
        """The run, or ``None`` when it never existed or has expired."""
        with self._lock:
            self._expire()
            return self._runs.get(run_id)
