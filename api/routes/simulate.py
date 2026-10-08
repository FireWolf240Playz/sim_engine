"""POST /simulate — config in, summary + score out.

The single most important endpoint: it runs one full simulation and
returns the complete ``summary()`` dict (resilience score, cost grade,
cost split, extrapolations all included), optionally with the rendered
report PNG as base64. Stateless: the request body is the entire input.

Multi-seed confidence (roadmap 1.1): ``n_seeds > 1`` (or an explicit
``seeds`` list) runs the engine once per seed and adds ``seeds`` /
``runs`` / ``profile`` to the response, with ``summary`` set to the
typical run's (middle by resilience score, see
:func:`sim_core.profile.typical_index`). Single-run responses omit those
keys entirely — the contract is byte-identical to the pre-1.1 shape.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException

from api.schemas import SimulateRequest, SimulateResponse
from sim_core import CloudSimulator, resilience_profile, typical_index
from sim_core.playbooks import get_playbook
from sim_core.profile import timeseries_band

router = APIRouter(tags=["simulate"])


@router.post(
    "/simulate",
    response_model=SimulateResponse,
    response_model_exclude_unset=True,
)
def simulate(payload: SimulateRequest) -> dict[str, Any]:
    """Run the simulation and return its full summary dict.

    - Unknown ``playbook`` → 404.
    - Invalid ``config`` → 422 (FastAPI validates :class:`SimulationConfig`).
    - ``include_report_png`` renders the report to a temp file and returns
      the PNG bytes base64-encoded (the engine's :func:`render_report` API
      is unchanged).
    - ``n_seeds > 1`` (or explicit ``seeds``) → one run per seed plus the
      worst/typical/best profile; ``summary`` is the typical run's.
    """
    config = payload.config
    if payload.playbook is not None:
        try:
            playbook = get_playbook(payload.playbook)
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail=f"unknown playbook: {payload.playbook!r}",
            ) from exc
        try:
            config = playbook.apply(config)
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"playbook {payload.playbook!r} cannot apply to this topology: {exc}",
            ) from exc

    seed_list = _resolve_seed_list(payload, config.seed)
    if seed_list is None:
        return _single_run_response(config, payload.include_report_png, payload.include_timeseries)

    runs: List[dict[str, Any]] = []
    series: List[List[dict[str, Any]]] = []
    for seed in seed_list:
        # SimulationConfig is frozen → copy with the per-run seed. Deep so
        # no nested config state is shared between runs.
        run_config = config.model_copy(deep=True, update={"seed": seed})
        simulator = CloudSimulator(run_config)
        runs.append({"seed": seed, "summary": simulator.run()})
        if payload.include_timeseries:
            series.append(simulator.collector.timeseries())

    summaries = [run["summary"] for run in runs]
    typical = typical_index(summaries)
    response: dict[str, Any] = {
        "seeds": seed_list,
        "runs": runs,
        "profile": resilience_profile(summaries, seeds=seed_list),
        # Shared across runs: the effective schedule (config + playbook).
        "chaos": [event.model_dump(mode="json") for event in config.chaos],
        # The typical run (middle by resilience score) is the headline.
        "summary": summaries[typical],
        "typical_seed": seed_list[typical],
    }
    if payload.include_timeseries:
        # The timeline follows the headline: the typical run's ticks, plus
        # the P95 range across every seed as a confidence band behind it.
        response["timeseries"] = series[typical]
        response["timeseries_band"] = timeseries_band(series)
    return response


def _resolve_seed_list(
    payload: SimulateRequest, config_seed: Optional[int]
) -> Optional[List[int]]:
    """The explicit seed list for a multi-seed request, or ``None`` for a
    single run.

    An explicit ``seeds`` list always wins (documented on the schema).
    Otherwise ``n_seeds > 1`` derives ``base + 0..n-1`` where ``base`` is
    the config's seed, or 42 when the config has none (a ``None`` seed
    would otherwise draw from entropy and break the arithmetic).
    """
    if payload.seeds is not None:
        return list(payload.seeds)
    if payload.n_seeds > 1:
        base = config_seed if config_seed is not None else 42
        return [base + i for i in range(payload.n_seeds)]
    return None


def _single_run_response(
    config: Any, include_report_png: bool, include_timeseries: bool
) -> dict[str, Any]:
    """One run, the original response shape (unchanged by 1.1).

    Returns exactly the four original keys — ``summary``,
    ``report_png_b64``, ``chaos``, ``timeseries`` — so with
    ``response_model_exclude_unset`` the serialized body is byte-identical
    to the pre-1.1 contract.
    """
    simulator = CloudSimulator(config)
    summary = simulator.run()

    report_png_b64: str | None = None
    if include_report_png:
        with tempfile.TemporaryDirectory(prefix="eleven_api_") as tmp:
            out_path = render_report_to_temp(simulator.collector, tmp)
            report_png_b64 = base64.b64encode(Path(out_path).read_bytes()).decode("ascii")

    timeseries: list[dict[str, Any]] | None = None
    if include_timeseries:
        timeseries = simulator.collector.timeseries()

    return {
        "summary": summary,
        "report_png_b64": report_png_b64,
        "chaos": [event.model_dump(mode="json") for event in config.chaos],
        "timeseries": timeseries,
    }


def render_report_to_temp(collector: Any, tmp_dir: str) -> Path:
    """Render the report into *tmp_dir* and return the written path.

    Isolated so the endpoint itself stays readable and the temp-file
    lifetime is owned by the caller.
    """
    from sim_core.viz import render_report  # local import keeps matplotlib lazy

    output = Path(tmp_dir) / "eleven_report.png"
    return Path(render_report(collector, output_path=output, show=False))