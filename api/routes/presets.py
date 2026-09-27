"""GET /presets — provider-calibrated component presets as plain JSON.

Each preset factory is invoked with no name (the default name is used)
and the resulting config model dumped to JSON, so the frontend can show
or seed component fields with realistic starting points per provider.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import PresetListResponse
from sim_core.presets import PRESETS

router = APIRouter(tags=["presets"])


@router.get("/presets", response_model=PresetListResponse)
def presets() -> dict[str, Any]:
    """All provider presets, keyed by preset name, dumped to JSON."""
    items = {name: factory().model_dump(mode="json") for name, factory in PRESETS.items()}
    return {"presets": items, "count": len(items)}
