# Eleven — start here

**Read `docs/AGENTS.md` before anything else.** It is the repo map: ~3k tokens
that replace reading a ~160k-token codebase. Then read `docs/HANDOFF.md` for
the current task.

Do not open other files until those two name them. Grep before reading; read
by line range, never whole files.

Quick orientation:

- `sim_core/` — the SimPy engine. Everything flows through one dict,
  `MetricsCollector.summary()`.
- `api/` — stateless FastAPI over the engine.
- `web/` — Next.js frontend. `web/src/core/types.ts` mirrors that dict by hand.
- `tests/` — pytest for the engine. `web/tests/` — Vitest for the frontend.

Rules that are bugs if broken, in full in `docs/AGENTS.md`: Pydantic holds
configuration and SimPy holds state; chaos must mutate the live simulation, not
a recorded metric; real SimPy 4 API only; full type hints.
