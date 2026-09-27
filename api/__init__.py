"""Eleven API: stateless FastAPI wrapper over the :mod:`sim_core` engine.

Phase 4 of the Eleven plan. The API adds *no* state and *no* database:
every request carries a full :class:`~sim_core.config.SimulationConfig`,
the engine runs to completion, and the response carries the full summary
(score, cost grade, cost split, extrapolation included). Results are
reproducible purely from the request body.

Endpoints:

- ``GET  /health``    — liveness + version.
- ``GET  /playbooks`` — the named incident playbooks (name + description).
- ``GET  /presets``   — provider-calibrated component presets (dumped JSON).
- ``POST /simulate``  — config in → summary + score out (optional PNG).
- ``POST /compare``   — multi-cloud / what-if A/B / capacity sweep.

Run it::

    uvicorn api.main:app --reload          # from the repo root
    # or
    python -m api

Interactive docs: ``http://127.0.0.1:8000/docs``.
"""

from api.main import app, create_app

__all__ = ["app", "create_app"]
