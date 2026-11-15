"""POST /runs, GET /runs/{id}, GET /runs/{id}/events — a run you can watch.

``/simulate`` answers once everything is done: the simulation (a fraction of
a second) and the Fix-it plan (up to half a minute on a 20-node topology).
A run started here answers at once with its id, and its event stream sends
what the engine does as it does it:

=========  ===============================================================
started    ``{run_id, seed, seeds, duration, metrics_interval, chaos}``
seed       multi-seed only: ``{seed, index, of, resilience_score}`` per seed
tick       one engine tick (see ``MetricsCollector.timeseries``)
chaos      a disruption opening or closing
feed       a line of the event feed, right after the event that caused it
measured   ``{summary, typical_seed}``: the run without the Fix-it plan
result     exactly what ``/simulate`` returns, ``include_timeseries`` on
error      ``{detail}``, instead of ``result``
ping       ``{}``, no id: nothing else was sent for a while
=========  ===============================================================

The request is checked before anything starts, by the same rules and with
the same words as ``/simulate``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.limits import check_budget
from api.routes.simulate import (
    _resolve_seed_list,
    effective_config,
    multi_seed_body,
    seed_config,
    single_run_body,
)
from api.runs import Run, RunStore, sse
from api.schemas import RunRequest, RunStartedResponse, RunStatusResponse, SimulateResponse
from sim_core import CloudSimulator, SimulationConfig, typical_index
from sim_core.feed import Narrator

router = APIRouter(tags=["runs"])


def _response_json(body: dict[str, Any]) -> dict[str, Any]:
    """``body`` as ``/simulate`` serialises it (its response model, unset
    keys left out), so the stream's ``result`` equals that response."""
    return SimulateResponse(**body).model_dump(mode="json", exclude_unset=True)


def _publish_engine_event(run: Run, narrator: Narrator, event: dict[str, Any]) -> None:
    """One engine event, then the feed lines it caused."""
    run.publish(str(event["type"]), {key: value for key, value in event.items() if key != "type"})
    for line in narrator.read(event):
        run.publish("feed", line)


def _publish_started(
    run: Run, config: SimulationConfig, seed: int, seeds: list[int] | None
) -> None:
    run.publish(
        "started",
        {
            "run_id": run.run_id,
            "seed": seed,
            "seeds": seeds,
            "duration": config.duration,
            "metrics_interval": config.metrics_interval,
            # The effective schedule: the config's events and the playbook's.
            "chaos": [event.model_dump(mode="json") for event in config.chaos],
        },
    )


def _single_run(run: Run, config: SimulationConfig) -> None:
    narrator = Narrator()
    simulator = CloudSimulator(
        config, event_sink=lambda event: _publish_engine_event(run, narrator, event)
    )
    _publish_started(run, config, simulator.seed, None)
    summary = simulator.run(suggest=False)
    run.publish("measured", {"summary": summary, "typical_seed": None})
    simulator.attach_fix_plan(summary)
    body = single_run_body(config, summary, timeseries=simulator.collector.timeseries())
    run.finish(_response_json(body))


def _multi_seed_run(run: Run, config: SimulationConfig, seed_list: list[int]) -> None:
    """Every seed first (each takes a fraction of a second), then the typical
    run's events, the run the headline and the timeline come from."""
    _publish_started(run, config, seed_list[0], seed_list)
    simulators: list[CloudSimulator] = []
    recorded: list[list[dict[str, Any]]] = []
    summaries: list[dict[str, Any]] = []
    for index, seed in enumerate(seed_list, start=1):
        events: list[dict[str, Any]] = []
        simulator = CloudSimulator(seed_config(config, seed), event_sink=events.append)
        summary = simulator.run(suggest=False)
        simulators.append(simulator)
        recorded.append(events)
        summaries.append(summary)
        run.publish(
            "seed",
            {
                "seed": seed,
                "index": index,
                "of": len(seed_list),
                "resilience_score": summary["resilience_score"],
            },
        )

    typical = typical_index(summaries)
    narrator = Narrator()
    for event in recorded[typical]:
        _publish_engine_event(run, narrator, event)
    run.publish("measured", {"summary": summaries[typical], "typical_seed": seed_list[typical]})

    for simulator, summary in zip(simulators, summaries, strict=True):
        simulator.attach_fix_plan(summary)
    series = [simulator.collector.timeseries() for simulator in simulators]
    run.finish(_response_json(multi_seed_body(config, seed_list, summaries, series)))


def execute_run(run: Run, config: SimulationConfig, seed_list: list[int] | None) -> None:
    """The worker: run it, publish as it goes, and always close the run."""
    try:
        if seed_list is None:
            _single_run(run, config)
        else:
            _multi_seed_run(run, config, seed_list)
    except Exception as exc:  # whatever broke, the reader is told
        run.fail(f"the run failed: {exc}")


@router.post("/runs", status_code=202, response_model=RunStartedResponse)
def start_run(payload: RunRequest, request: Request) -> dict[str, Any]:
    """Start a run and answer at once with its id.

    Refused before anything starts, as ``/simulate`` refuses: an unknown
    playbook is a 404, one that cannot apply a 400, a config or a budget
    too large a 422. No room for another run is a 429.
    """
    config = effective_config(payload.config, payload.playbook, payload.custom_chaos)
    seed_list = _resolve_seed_list(payload, config.seed)
    check_budget(config, runs=len(seed_list) if seed_list is not None else 1)

    store: RunStore = request.app.state.runs
    run = store.create()
    if run is None:
        raise HTTPException(
            status_code=429,
            detail="the engine is busy: every run slot is in use. Try again in a moment.",
        )
    request.app.state.run_pool.submit(execute_run, run, config, seed_list)
    return {"run_id": run.run_id, "events_url": f"/runs/{run.run_id}/events"}


def _run_or_404(request: Request, run_id: str) -> Run:
    store: RunStore = request.app.state.runs
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id!r}")
    return run


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """The run without a stream: its status, and its result once done."""
    run = _run_or_404(request, run_id)
    return {"run_id": run.run_id, "status": run.status, "result": run.result, "error": run.error}


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, request: Request) -> StreamingResponse:
    """The run's events as a server-sent-event stream.

    ``Last-Event-ID`` (sent by a reconnecting ``EventSource``) resumes after
    that event; a value that is not a number is ignored.
    """
    run = _run_or_404(request, run_id)
    try:
        last_seen = int(request.headers.get("last-event-id", "-1"))
    except ValueError:
        last_seen = -1
    return StreamingResponse(
        sse(run, last_seen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
