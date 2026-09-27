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

## Phase 2 · Architecture score + cost extrapolation — ✅ DONE

A number and a grade that non-technical stakeholders read in one second.

- `sim_core/score.py` — **pure functions over a summary dict** (imports
  nothing from `sim_core`, so it is trivially unit-testable and the future
  API/frontend can reuse it verbatim):
  - `resilience_score(summary) -> 0..100` —
    `100 * (0.6·sla + 0.4·completion) − 30·(failed/n) − 10·min(1, retries/n)
    − 10·min(1, (p95 − sla_target)/sla_target)`, clamped to [0, 100],
    rounded to 0.1. SLA-dominant; failures hurt more than slowness; p95
    past the SLA budget costs up to 10 more.
  - `cost_grade(summary) -> "A".."F"` —
    `0.7·sizing_quality + 0.3·cost_efficiency`; sizing credit
    right 1.0 / over 0.5 / under 0.0 (undersized = reliability risk, worst
    credit); cost efficiency = 1 − share of spend on oversized components
    (falls back to capacity share when no rates configured). Bands:
    A ≥ 0.90, B ≥ 0.75, C ≥ 0.55, D ≥ 0.35, else F.
  - `cost_extrapolation(total_cost, duration) -> {hour, month, year}` —
    linear steady-state projection, **labeled "same load sustained 24/7"**,
    unrounded so `hour·24·30 == month` holds exactly.
  - `cost_per_completed_request`, `score_headline` (the CLI one-liner),
    `score_color` (badge color).
- `MetricsCollector` now takes `sla_target` (passed by the engine) and
  captures the run horizon in `settle_cost`; `summary()` surfaces the whole
  block: `sla_target`, `cost_per_completed_request`, `resilience_score`,
  `cost_grade`, `cost_extrapolation`, plus the cost split `cost_base`,
  `cost_metered`, `steady_state_cost_extrapolation` (provisioned base vs
  load/chaos-driven metered part) — so the JSON report and the future API
  carry it for free.
- CLI headline line under the report header, e.g.
  `Resilience 98/100 | Cost grade D | $1,517/mo (steady $1,175/mo) | $18.5k/yr`
  (the steady figure is the provisioned base with none of the load/chaos
  metering on top; degrades to `n/a` pieces when a run has no requests).
- **Root-cause fix found while verifying:** `_accumulate_cost` multiplied
  `cost_per_hour` (a per-*hour* rate) by `dt` in *seconds* without the
  `/3600` — every run was billed 3600× (a 5-node small-tier topology
  "cost" $5.4M/mo instead of ~$1.5k/mo). Fixed in `metrics.py`, with a
  regression test in `tests/test_cost.py` that re-derives the expected
  total from the unit contract (`test_cost_units_are_per_hour`) and an
  exact-base assertion on `test_idle_capacity_is_still_billed` (which had
  been pinning the buggy $7500 figure).
- Report PNG: color-coded score badge (green ≥ 75 / amber ≥ 50 / red below)
  in the top-right corner.
- Tests: `tests/test_score.py` (exact-value fixed-dict assertions for
  score/grade, cost-math identities, undersized→F vs oversized→D
  discrimination, no-requests edges, headline cost-split rendering,
  end-to-end summary block + seed determinism) + the cost-units regression
  above. Suite: 92/92.
- **No new dependencies.** Complexity: low.

## Phase 3 · Backlog file for the founding engineer — NEXT

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

## Phase 4 · FastAPI (stateless, no DB) — ✅ DONE

**What landed:**
- `api/` package: `main.py` (`create_app()` factory + `app` for
  `uvicorn api.main:app` / `python -m api`), `schemas.py` (Pydantic
  request/response models; `SimulationConfig` reused verbatim so API
  validation == CLI validation), `routes/` (simulate, compare, playbooks,
  presets) + `GET /health` in `main.py`.
- `POST /simulate` — config in → full `summary` (score/grade/cost-split
  all included) + optional base64 report PNG; unknown playbook → 404,
  bad config → 422.
- `POST /compare` — reuses `sim_core.compare`; `what-if` needs `set`,
  `sweep` needs `param`+`values` (400 when missing), invalid path → 400,
  rejected value → 422; sweep returns the `knee`.
- `GET /playbooks` (4), `GET /presets` (12), `GET /health`.
- `compare.py`: `_coerce` promoted to public `coerce_value` (private alias
  kept) so the API reuses the exact coercion rules.
- New dependencies: `fastapi` + `uvicorn[standard]` (in `pyproject.toml`),
  `httpx` in the dev extra for `TestClient`. Permissive CORS for now
  (Phase 5 frontend origin unknown yet).
- Tests: `tests/test_api.py` (14 tests via `fastapi.testclient.TestClient`:
  health, playbooks, presets, simulate happy path + 422 + playbook + 404
  + PNG magic bytes, compare all three modes + the 400 mappings). Verified
  against a live `uvicorn` boot (health/playbooks/simulate/playbook/what-if
  all 200 over real HTTP). Suite: 106/106.

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
