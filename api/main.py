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

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import sim_core
from api.routes import compare, imports_api, playbooks, presets, simulate
from api.schemas import HealthResponse

#: Environment variable naming the browser origins allowed to call the API,
#: comma-separated (``https://eleven.example.com,https://staging.example.com``).
CORS_ORIGINS_ENV = "ELEVEN_CORS_ORIGINS"

#: Without that variable: a frontend served from this machine, on any port.
_LOCAL_ORIGIN = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"


def _cors_origins() -> dict[str, Any]:
    """Which origins a browser may call from (read once, at app creation).

    The API holds no credentials, so an open policy leaks nothing; what it
    would allow is any page a user visits spending this machine's CPU on
    simulations. The default therefore admits only a local frontend, and a
    deployment names its own origins in :data:`CORS_ORIGINS_ENV`.
    """
    configured = [o.strip() for o in os.environ.get(CORS_ORIGINS_ENV, "").split(",") if o.strip()]
    if configured:
        return {"allow_origins": configured}
    return {"allow_origin_regex": _LOCAL_ORIGIN}


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

    app.add_middleware(
        CORSMiddleware,
        allow_methods=["*"],
        allow_headers=["*"],
        **_cors_origins(),
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
