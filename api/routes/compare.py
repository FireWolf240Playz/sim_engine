"""POST /compare — multi-cloud, what-if A/B, and capacity sweep.

Reuses :mod:`sim_core.compare` verbatim; the route only parses the
mode-specific request fields into (label, config) pairs, runs them, and
returns the baseline-relative diff (plus the sweep knee). Stateless and
synchronous — a compare request runs its simulations to completion before
responding (each run is short by design; the frontend should size runs
accordingly).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from api.schemas import CompareRequest, CompareResponse
from sim_core.compare import (
    PROVIDERS,
    apply_provider,
    coerce_value,
    diff_runs,
    knee_point,
    run_many,
    run_one,
    set_path,
    sweep,
)

router = APIRouter(tags=["compare"])


def _what_if_variants(config: Any, entries: list[str]) -> list[tuple[str, Any]]:
    """Patch the baseline per ``path=value`` entry; clear errors per entry."""
    variants: list[tuple[str, Any]] = []
    for entry in entries:
        path, separator, raw = entry.partition("=")
        if not separator or not path.strip():
            raise HTTPException(
                status_code=400,
                detail=f"invalid 'set' entry {entry!r}; expected 'path=value'",
            )
        label = path.strip() + "=" + raw
        try:
            patched = set_path(config, path.strip(), coerce_value(raw))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"what-if {label!r}: {exc}") from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=f"what-if {label!r}: {exc}") from exc
        variants.append((label, patched))
    return variants


@router.post("/compare", response_model=CompareResponse)
def compare_runs(payload: CompareRequest) -> dict[str, Any]:
    """Run the requested comparison and return the baseline-relative diff.

    Mode-specific validation:

    - ``what-if`` without a non-empty ``set`` → 400.
    - ``sweep`` without ``param`` and ``values`` → 400.
    - A bad path/value → 400 (path) or 422 (rejected value).
    """
    config = payload.config

    if payload.mode == "multi-cloud":
        pairs: list[tuple[str, Any]] = [("baseline", config)]
        for provider in PROVIDERS:
            try:
                pairs.append((provider, apply_provider(config, provider)))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        diff = diff_runs(run_many(pairs))
        knee: float | None = None

    elif payload.mode == "what-if":
        if not payload.set:
            raise HTTPException(
                status_code=400,
                detail="what-if mode requires a non-empty 'set' list of 'path=value' strings",
            )
        variants = _what_if_variants(config, payload.set)
        diff = diff_runs(run_many([("baseline", config), *variants]))
        knee = None

    else:  # sweep
        if not payload.param:
            raise HTTPException(status_code=400, detail="sweep mode requires 'param'")
        if not payload.values:
            raise HTTPException(status_code=400, detail="sweep mode requires 'values'")
        try:
            results = sweep(config, payload.param, payload.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"sweep: {exc}") from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=f"sweep: invalid value: {exc}") from exc
        knee = knee_point(results, metric=payload.metric, threshold=payload.threshold)
        diff = diff_runs([run_one("baseline", config), *results])

    return {"mode": payload.mode, "diff": diff, "knee": knee}
