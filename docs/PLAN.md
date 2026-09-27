# Eleven — delivery plan

Five phases, in order. Each phase lands as a small, self-contained,
test-verified increment; nothing in a later phase requires reworking an
earlier one. Tier 2 items (see [BACKLOG.md](BACKLOG.md)) are explicitly
deferred to the founding engineer.

## Phase 1 · Incident playbooks — ✅ DONE

One-click, realistic failure scenarios on top of the existing chaos engine.

- `sim_core/playbooks.py` — `Playbook` dataclass + `PLAYBOOKS` registry
  (`db_failover`, `cross_region_latency_spike`, `cache_eviction_storm`,
  `dependency_timeout_cascade`). `Playbook.apply(config)` appends the
  incident's chaos events to the config (never replaces user chaos); first
  injection at `max(5.0, duration * 0.25)` so it always fires in-horizon.
- Targeting by **role** (`ComponentRole.DATABASE` / `CACHE` /
  `EXTERNAL_API`), not node name — works on any topology that has the role;
  clear `ValueError` when it doesn't. `dependency_timeout_cascade` falls
  back to the database when no external-API node exists.
- `ChaosEvent.target: Optional[str]` (config.py) + a `SimulationConfig`
  model validator that fails fast on unknown target names.
- **Root-cause fix:** chaos handlers used capture-and-restore, so two
  overlapping windows on the same node (e.g. `db_failover`'s outage +
  degraded phases) raced and one restore could cancel an active window.
  Replaced with **reference-counted apply/release bookkeeping** on
  `Component` (topology.py) — the live value is always recomputed from the
  configured base + the worst active degradation. Applied to capacity,
  service time, and hit-rate.
- CLI: `eleven playbooks list`, `eleven run <config> --playbook NAME`,
  `eleven demo --playbook NAME` (unknown name / missing role → exit 2 +
  clear stderr).
- Tests: `tests/test_playbooks.py` (13 tests, incl. a live-state test that
  checks the outage → degraded → restored capacity timeline). Suite: 70/70.

## Phase 2 · Architecture score + cost extrapolation — NEXT

A number and a grade that non-technical stakeholders read in one second.

- New `sim_core/score.py`:
  - `resilience_score(summary) -> int` — 0..100, SLA-dominant weighted
    blend of existing summary fields (SLA compliance, completion rate,
    p95-vs-SLA headroom, retry pressure, sizing status).
  - `cost_grade(summary) -> "A".."F"` — from `component_sizing`
    (oversized/undersized share) plus cost-per-request.
  - `cost_extrapolation(total_cost, duration) -> dict` —
    `{hour, month, year}`, explicitly labeled **steady-state, 24/7
    assumption** (linear projection of the sampled run).
- Surface in `summary()` (`resilience_score`, `cost_grade`,
  `cost_extrapolation` keys) + a CLI headline line, e.g.
  `Resilience 78/100 · Cost grade B · $1,240/mo · $14.9k/yr`.
- Tests: fixed-dict determinism, cost math, sizing → grade mapping.
- **No new dependencies.** Complexity: low.

## Phase 3 · Backlog file for the founding engineer

- `docs/BACKLOG.md` — the four Tier 2 items, each with *what / why /
  expected shape / dependencies / suggested order*:
  1. Third-party dependency modeling (Stripe/Auth0-style external APIs
     with their own latency, cost, failure injection).
  2. Calibration from production stats (user pastes real p50/p95 + RPS →
     tool fits service times).
  3. Autoscaling policy simulation (scale-out rules, fixed-vs-elastic
     cost delta).
  4. Shareable branded report (one-link HTML/PDF).
- Note that `ChaosEvent.target` (Phase 1) and `score.py` (Phase 2) are the
  groundwork these items build on. Trivial effort.

## Phase 4 · FastAPI (stateless, no DB)

- New `api/` package:
  - `POST /simulate` — config in (validated Pydantic), summary + PNG out.
  - `POST /compare` — reuses `sim_core.compare` (multi-cloud / what-if /
    sweep).
  - `GET /playbooks` — registry list (Phase 1).
  - `GET /presets` — the 12 provider × role presets.
  - `GET /health`.
- First new dependencies: `fastapi` + `uvicorn`.
- Tests via `fastapi.testclient.TestClient` (config → 200 → summary
  shape; playbook 404; compare modes).

## Phase 5 · Next.js frontend (the real one)

- `web/` — Next.js 15 (App Router) + React 19 + TypeScript + Tailwind.
  No vanilla JS.
- Views:
  - Topology builder (form) or YAML upload → `POST /simulate`.
  - Playbook picker (from `GET /playbooks`).
  - Results view: score badge + cost grade, monthly/yearly cost, latency
    percentiles, PNG report, sizing table.
  - Compare view: multi-cloud / what-if / sweep from `POST /compare`.
- Data fetching: React Query against the Phase 4 API.

## Ground rules (all phases)

- Python 3.12+, full type hints; Pydantic = config only, never live SimPy
  state; chaos mutates live state only.
- Small files, one concern per module, different names for files in
  different folders.
- Every phase: tests green + a real CLI/API run before "done".
- Tier 2 stays out of scope until the founding engineer lands.
