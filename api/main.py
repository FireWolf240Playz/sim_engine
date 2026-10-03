"""FastAPI application factory for the Eleven simulator API (Phase 4).

Stateless by design: no database, no sessions, no server-side state —
each request carries a full ``SimulationConfig`` and gets the full
summary back. The engine runs synchronously inside the request (the
simulations are short, in-memory discrete-event runs; a future revision
can move this to a task queue without changing the contract).

Run from the repo root::

    uvicorn api.main:app --reload
    # or
    python -m api
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import sim_core
from api.routes import compare, imports_api, playbooks, presets, simulate
from api.schemas import HealthResponse


def create_app() -> FastAPI:
    """Build the app. Factored so tests get an isolated instance per run."""
    app = FastAPI(
        title="Eleven API",
        version=sim_core.__version__,
        description=(
            "Pre-deployment cloud-resilience simulation. Stateless: config in, "
            "summary + score + cost out. See /docs for the interactive reference."
        ),
    )

    # Permissive CORS for now — the Next.js frontend (Phase 5) will call
    # this from a browser origin; tighten to the real frontend origin once
    # it has one.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(simulate.router)
    app.include_router(compare.router)
    app.include_router(imports_api.router)
    app.include_router(playbooks.router)
    app.include_router(presets.router)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> dict[str, Any]:
        """Liveness probe: the process is up and the engine importable."""
        return {"status": "ok", "version": sim_core.__version__}

    return app


#: Module-level app for ``uvicorn api.main:app``.
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=8000)
