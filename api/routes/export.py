"""POST /export and GET /export/formats: one run, downloaded as a file.

The API keeps no run, so an export cannot point at one. It names the run by
what produced it, the config (with its seed) and the playbook, and the server
simulates it again: the engine is deterministic, so the file describes exactly
the run the caller saw. A run id replaces the re-run when the run store lands
(roadmap 3.1).

Every byte of a file comes from :func:`sim_core.exports.render_export`, the
same call the CLI makes, so a format cannot differ between the two.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response

from api.limits import check_budget
from api.routes.simulate import apply_playbook_or_404
from api.schemas import ExportFormatsResponse, ExportRequest
from sim_core import CloudSimulator
from sim_core.exports import EXPORT_FORMATS, render_export

router = APIRouter(tags=["export"])


@router.get("/export/formats", response_model=ExportFormatsResponse)
def export_formats() -> dict[str, Any]:
    """Every format a run can be exported in, in registry order.

    ``tier`` is a label for the UI to show. Nothing here refuses a format
    over it; that arrives with accounts (roadmap 2.2).
    """
    items = [
        {
            "name": fmt.name,
            "label": fmt.label,
            "extension": fmt.extension,
            "media_type": fmt.media_type,
            "tier": fmt.tier,
        }
        for fmt in EXPORT_FORMATS.values()
    ]
    return {"formats": items, "count": len(items)}


@router.post("/export")
def export(payload: ExportRequest) -> Response:
    """Run the simulation again and return it as a file in the asked format.

    - Unknown ``playbook`` → 404, one that cannot apply → 400, in the words
      ``/simulate`` uses.
    - A config without a ``seed`` → 422: the re-run would draw a fresh seed
      and the file would describe some other run than the one on screen.
    - The run counts against the per-call work budget like any other.
    - Unknown ``format`` → 422 from validation, which lists the valid ones.

    The exported config is the one that ran, playbook events included, so the
    file replays as it is.
    """
    config = apply_playbook_or_404(payload.config, payload.playbook)
    if config.seed is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "an export needs config.seed: the server has to re-run the simulation "
                "to build the file, and without the seed it would export a different "
                "run than the one you saw. Send the seed the run was made with."
            ),
        )
    check_budget(config, runs=1)

    fmt = EXPORT_FORMATS[payload.format]
    simulator = CloudSimulator(config)
    # The Fix-it plan re-simulates candidate fixes; a format built from the
    # request log alone never shows it, so it is not computed for one.
    summary = simulator.run(suggest=fmt.uses_summary)
    content = render_export(
        fmt.name,
        summary=summary,
        config=config.model_dump(mode="json"),
        request_log=simulator.collector.request_log(),
        seed=simulator.seed,
    )
    filename = f"eleven-{payload.playbook or 'run'}-seed{simulator.seed}.{fmt.extension}"
    return Response(
        content=content,
        media_type=fmt.media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
