"""POST /simulate — config in, summary + score out.

The single most important endpoint: it runs one full simulation and
returns the complete ``summary()`` dict (resilience score, cost grade,
cost split, extrapolations all included), optionally with the rendered
report PNG as base64. Stateless: the request body is the entire input.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.schemas import SimulateRequest, SimulateResponse
from sim_core import CloudSimulator
from sim_core.playbooks import get_playbook

router = APIRouter(tags=["simulate"])


@router.post("/simulate", response_model=SimulateResponse)
def simulate(payload: SimulateRequest) -> dict[str, Any]:
    """Run one simulation and return its full summary dict.

    - Unknown ``playbook`` → 404.
    - Invalid ``config`` → 422 (FastAPI validates :class:`SimulationConfig`).
    - ``include_report_png`` renders the report to a temp file and returns
      the PNG bytes base64-encoded (the engine's :func:`render_report` API
      is unchanged).
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

    simulator = CloudSimulator(config)
    summary = simulator.run()

    report_png_b64: str | None = None
    if payload.include_report_png:
        with tempfile.TemporaryDirectory(prefix="eleven_api_") as tmp:
            out_path = render_report_to_temp(simulator.collector, tmp)
            report_png_b64 = base64.b64encode(Path(out_path).read_bytes()).decode("ascii")

    return {"summary": summary, "report_png_b64": report_png_b64}


def render_report_to_temp(collector: Any, tmp_dir: str) -> Path:
    """Render the report into *tmp_dir* and return the written path.

    Isolated so the endpoint itself stays readable and the temp-file
    lifetime is owned by the caller.
    """
    from sim_core.viz import render_report  # local import keeps matplotlib lazy

    output = Path(tmp_dir) / "eleven_report.png"
    return Path(render_report(collector, output_path=output, show=False))
