"""POST /imports — upload an architecture description, get a runnable config.

Accepts Eleven native YAML/JSON or ``terraform show -json`` output (auto-
detected), and returns a fully validated :class:`sim_core.SimulationConfig`
plus an import report (node/edge summary, estimate assumptions, warnings,
unmapped resources). The returned ``config`` can be dropped straight into
``POST /simulate`` — that is the whole point of the endpoint.

Stateless like the rest of the API: the text is the entire input; nothing
is stored server-side.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api.schemas import ImportRequest, ImportResponse
from sim_core.importers import ArchitectureImportError, import_architecture

router = APIRouter(tags=["imports"])


@router.post("/imports", response_model=ImportResponse)
def imports(payload: ImportRequest) -> dict[str, Any]:
    """Convert an uploaded architecture document into a simulation config.

    - Parse failure / nothing mappable → 400 with an actionable detail.
    - Empty content → 422 (FastAPI ``min_length`` on the field).
    - Success → ``{format, config, report}``; ``config`` is a valid
      ``SimulationConfig`` (validated by the exact same rules as a
      hand-written YAML file).
    """
    try:
        result = import_architecture(payload.content, payload.filename)
    except ArchitectureImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "format": result.report.format,
        "config": result.config,
        "report": result.report.model_dump(mode="json"),
    }
