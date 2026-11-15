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
from collections.abc import Sequence
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from api.limits import check_budget
from api.schemas import RunSpec, SimulateRequest, SimulateResponse
from sim_core import CloudSimulator, SimulationConfig, resilience_profile, typical_index
from sim_core.config import ChaosEvent
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
    config = effective_config(payload.config, payload.playbook, payload.custom_chaos)

    seed_list = _resolve_seed_list(payload, config.seed)
    check_budget(config, runs=len(seed_list) if seed_list is not None else 1)
    if seed_list is None:
        return _single_run_response(config, payload.include_report_png, payload.include_timeseries)

    summaries: list[dict[str, Any]] = []
    series: list[list[dict[str, Any]]] = []
    for seed in seed_list:
        simulator = CloudSimulator(seed_config(config, seed))
        summaries.append(simulator.run())
        if payload.include_timeseries:
            series.append(simulator.collector.timeseries())
    return multi_seed_body(
        config, seed_list, summaries, series if payload.include_timeseries else None
    )


def seed_config(config: SimulationConfig, seed: int) -> SimulationConfig:
    """``config`` on ``seed``. Frozen, so a copy; deep, so no nested config
    state is shared between the runs of one call."""
    return config.model_copy(deep=True, update={"seed": seed})


def single_run_body(
    config: SimulationConfig,
    summary: dict[str, Any],
    *,
    report_png_b64: str | None = None,
    timeseries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The single-run response: exactly the four original keys (unchanged by
    1.1), so with ``response_model_exclude_unset`` the serialized body is
    byte-identical to the pre-1.1 contract. Shared with ``/runs``."""
    return {
        "summary": summary,
        "report_png_b64": report_png_b64,
        "chaos": [event.model_dump(mode="json") for event in config.chaos],
        "timeseries": timeseries,
    }


def multi_seed_body(
    config: SimulationConfig,
    seed_list: list[int],
    summaries: list[dict[str, Any]],
    series: list[list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """The multi-seed response, from one summary (and, when asked for, one
    timeline) per seed, in seed order. Shared with ``/runs``."""
    typical = typical_index(summaries)
    response: dict[str, Any] = {
        "seeds": seed_list,
        "runs": [{"seed": s, "summary": m} for s, m in zip(seed_list, summaries, strict=True)],
        "profile": resilience_profile(summaries, seeds=seed_list),
        # Shared across runs: the effective schedule (config + playbook).
        "chaos": [event.model_dump(mode="json") for event in config.chaos],
        # The typical run (middle by resilience score) is the headline.
        "summary": summaries[typical],
        "typical_seed": seed_list[typical],
    }
    if series is not None:
        # The timeline follows the headline: the typical run's ticks, plus
        # the P95 range across every seed as a confidence band behind it.
        response["timeseries"] = series[typical]
        response["timeseries_band"] = timeseries_band(series)
    return response


def effective_config(
    config: SimulationConfig, playbook: str | None, custom_chaos: Sequence[ChaosEvent]
) -> SimulationConfig:
    """The config a run actually simulates: the architecture, then the
    playbook's events, then the failures injected by hand (3.3), in that order.

    The merged config is validated again, so an injected failure aimed at a
    node the topology does not have is refused (422) with the validator's own
    sentence, e.g. ``chaos event targets unknown component 'nope' (known
    nodes: [...])``. With no injected failure it is exactly
    :func:`apply_playbook_or_404`.
    """
    merged = apply_playbook_or_404(config, playbook)
    if not custom_chaos:
        return merged
    try:
        return SimulationConfig.model_validate(
            {**merged.model_dump(), "chaos": [*merged.chaos, *custom_chaos]}
        )
    except ValidationError as exc:
        # One sentence, not Pydantic's list of error objects.
        detail = "; ".join(
            str(error["msg"]).removeprefix("Value error, ") for error in exc.errors()
        )
        raise HTTPException(status_code=422, detail=detail) from exc


def apply_playbook_or_404(config: SimulationConfig, name: Optional[str]) -> SimulationConfig:
    """``config`` with the named incident's chaos events appended.

    Shared by every route that takes a ``playbook``, so the same name is
    refused with the same status and the same words wherever it is sent:
    an unknown name is a 404, a known incident this topology has no node
    for is a 400. ``None`` means no incident and returns ``config`` as is.
    """
    if name is None:
        return config
    try:
        playbook = get_playbook(name)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown playbook: {name!r}",
        ) from exc
    try:
        return playbook.apply(config)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"playbook {name!r} cannot apply to this topology: {exc}",
        ) from exc


def _resolve_seed_list(payload: RunSpec, config_seed: Optional[int]) -> Optional[List[int]]:
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
    config: SimulationConfig, include_report_png: bool, include_timeseries: bool
) -> dict[str, Any]:
    """One run, the original response shape (unchanged by 1.1)."""
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

    return single_run_body(
        config, summary, report_png_b64=report_png_b64, timeseries=timeseries
    )


def render_report_to_temp(collector: Any, tmp_dir: str) -> Path:
    """Render the report into *tmp_dir* and return the written path.

    Isolated so the endpoint itself stays readable and the temp-file
    lifetime is owned by the caller.
    """
    from sim_core.viz import render_report  # local import keeps matplotlib lazy

    output = Path(tmp_dir) / "eleven_report.png"
    return Path(render_report(collector, output_path=output, show=False))