# Eleven — repo map for agents

Pre-deployment digital-twin simulator: model a cloud architecture, stress it
with traffic and chaos, score it, price it — before any infrastructure exists.

**You are reading this instead of reading the codebase.** The repo is ~160k
tokens. It does not fit in your context and never will. This file is ~3k. Read
it, then open only the files your task names. If you find yourself opening a
fourth file to answer one question, stop and grep instead.

---

## Read protocol (follow this literally)

1. Read this file and `docs/HANDOFF.md`. Nothing else, yet.
2. `grep` for the symbol you need. Do not read a file to find out what is in it.
3. Open files **by line range**, not whole. `config.py` is 22 KB — reading it
   to check one field costs ~6k tokens.
4. Never open: `venv/`, `node_modules/`, `web/.next/`, `__pycache__/`,
   `*.pyc`, `sim_core/price_source/*.py` (unless the task says "pricing"),
   or any root-level `*.json` / `*.png` (generated run output, not source).
5. When done, rewrite `docs/HANDOFF.md`. Format is at the bottom of this file.

---

## The one thing to understand first

Everything flows through **one dict**: `MetricsCollector.summary()`.

```
SimulationConfig (Pydantic, validated)
  └─> Topology          builds LIVE simpy.Resource objects from config numbers
  └─> CloudSimulator    owns simpy.Environment, runs traffic + chaos + requests
        └─> MetricsCollector.summary()  ── THE CONTRACT ──┐
                                                           ├─> score.py      (resilience_score, cost_grade)
                                                           ├─> findings.py   (plain-English verdict)
                                                           ├─> profile.py    (worst/typical/best over seeds)
                                                           ├─> compare.py    (baseline-relative diff)
                                                           ├─> viz.py        (report PNG)
                                                           ├─> cli/sim.py    (terminal output)
                                                           └─> api/routes/   (JSON to the frontend)
```

`score.py`, `findings.py` and `profile.py` are **pure functions over that
dict**. They never touch SimPy, never re-run anything. If you need a new
number on screen, the question is almost always "is it in `summary()` yet?"

The frontend mirrors this dict by hand in `web/src/core/types.ts`. Changing
`summary()`'s shape without changing that file is the most common way to break
this repo.

---

## Hard rules (violating these is a bug, not a style choice)

1. **Pydantic holds configuration. SimPy holds state.** Never call
   `.request()`, `.release()` or `.capacity` on a BaseModel. Live resources are
   created inside `Topology`, initialized *from* config values.
2. **Chaos must change the running simulation** — drop a real
   `resource.capacity`, add a real `env.timeout()`, interrupt a real process.
   Mutating a recorded metric after the fact is not chaos, it is a lie.
   See `topology.py`'s `apply_*` / `release_*` pairs: they are reference
   counted so overlapping windows compose and a restore never cancels a window
   that is still open.
3. **Real SimPy 4 API only.** `env.process()`, `env.timeout()`,
   `resource.request()`, `simpy.Interrupt`. `env.schedule()` does not exist.
4. **Percentiles over many samples**, never from one data point.
5. **Full type hints**, Python 3.14 (`pyproject.toml` targets it), `mypy --strict` clean.
6. **No new dependencies** without asking.

---

## `sim_core/` — the engine (Python)

| File | KB | What it is / open it when |
|---|---|---|
| `config.py` | 22 | **All Pydantic models.** `SimulationConfig`, `GraphTopologyConfig`, `Edge`, `TrafficPattern`, `ChaosEvent`, `ComponentRole`. Open for: adding a config field, a validation rule. Grep the class name; never read whole. Note `TopologyConfig` is the legacy 4-node shape kept for compatibility — `.to_graph()` converts it to `GraphTopologyConfig`, which is what the engine actually runs. |
| `topology.py` | 11 | **Live SimPy wrappers.** `Component` (owns the `simpy.Resource`, `apply_capacity_drop`, `apply_latency_spike`, `apply_hit_rate_drop`, `draw_service_time`) and `Topology` (the graph: `component()`, `outgoing_edges()`, `cache()`). Open for: anything about live capacity or service time. |
| `engine.py` | 11 | **`CloudSimulator`** — owns `simpy.Environment`, `serve_request()`, `_walk()` (the DAG traversal, cache hit/miss, fan-out probabilities), `_use_component()` (acquire/timeout/retry/release). Open for: how a request moves through the graph. |
| `traffic.py` | 3 | Poisson arrivals following the configured profile. Small, read it whole. |
| `chaos.py` | 4 | The three injectors: `component_failure`, `network_latency`, `cache_outage`. Small, read it whole. |
| `metrics.py` | 16 | `RequestRecord`, `UtilizationSample`, `MetricsCollector`. **`summary()` and `timeseries()` are the contract above.** Open for: adding a metric. |
| `score.py` | 17 | Pure functions on `summary()`: `resilience_score`, `cost_grade`, `cost_extrapolation`, `cost_per_completed_request`, `score_headline`, `score_color`, `score_band`, `score_explanation`. |
| `findings.py` | 21 | Pure functions on `summary()`: `build_findings` → the plain-English "why this score" verdict, `verdict_headline`. Each `Finding` has `id`, `severity`, `text`, `node?` plus optional `title`, `why`, `impact`, `evidence` (`[{label, value}]`), `recommendation`. Each rule is its own `_`-prefixed function with a threshold constant at module top. **Add a finding = add one function + register it.** |
| `profile.py` | 6 | Multi-seed confidence: `resilience_profile` (worst/typical/best), `typical_index`. |
| `playbooks.py` | 7 | The four named incidents: `db_failover`, `cross_region_latency_spike`, `cache_eviction_storm`, `dependency_timeout_cascade`. Each is a function returning `ChaosEvent`s, targeted **by role**. Open for: adding an incident. |
| `presets.py` | 11 | AWS/Azure/GCP small-tier component presets. Flat list of factory functions. |
| `compare.py` | 12 | One core, three faces: `run_many`, `diff_runs`, `set_path` (dotted-path patching), `apply_provider`, `sweep`, `knee_point`. Backs `eleven compare multi-cloud|what-if|sweep`. |
| `importers.py` | 26 | Terraform/YAML/JSON → `SimulationConfig`. `import_architecture()` is the entry; everything else is private estimation tables. **Open only for import bugs.** |
| `viz.py` | 8 | Matplotlib report PNG. `render_report`, `render_comparison`. |
| `cli/sim.py` | 22 | `run` / `demo` / `playbooks` / `compare` subcommands + all terminal formatting. |
| `cli/pricing.py` | 14 | `prices` / `aws-prices` / `gcp-prices` catalog subcommands. |
| `price_source/` | 60 | Live cloud price catalogs (AWS/Azure/GCP + `base.py` HTTP/cache plumbing). **Self-contained. Ignore unless the task is pricing.** Needs `.env` credentials for GCP. |

## `api/` — stateless FastAPI over the engine

No database, no sessions. Each request carries a full config, gets the full
summary back. `api/schemas.py` reuses `sim_core.SimulationConfig` directly, so
the API validates by exactly the same rules as the CLI.

- `main.py` — `create_app()`, CORS (currently `*`), `/health`.
- `routes/simulate.py` — `POST /simulate`. Single run, or multi-seed when
  `n_seeds > 1` (then also returns `seeds` / `runs` / `profile`).
- `routes/compare.py` — `POST /compare` (multi-cloud / what-if / sweep).
- `routes/imports_api.py` — `POST /imports`.
- `routes/playbooks.py`, `routes/presets.py` — `GET`, trivial.

## `web/` — Next.js 16 + React 19 + Tailwind v4 frontend

**`web/AGENTS.md` is auto-generated by `next dev` — it is not a repo doc.**

- `src/core/types.ts` — hand-written mirror of the API contract. **Changing
  `summary()` means changing this file.**
- `src/core/api/client.ts` — the only place `fetch` is called. Typed
  `ApiError`, per-call timeouts.
- `src/core/state/RunStateContext.tsx` — the shared run: active config,
  selected playbook, result. **The engine call lives here exactly once;** no
  view owns a fetch. Runs carry a token so a stale response cannot overwrite a
  newer one.
- `src/core/state/ThemeContext.tsx` — light/dark via `data-theme` on `<html>`,
  `useSyncExternalStore`. Pre-paint script lives in `src/core/lib/themeStorage.ts`.
- `src/app/globals.css` — **the only place a colour is written down.** Tokens
  under `@theme static`, dark overrides under `html[data-theme="dark"]`.
  Components use `bg-surface-1`, `fill-sev-crit`, `stroke-edge`. Never hardcode
  a hex in a component. SVG uses the `fill-*`/`stroke-*` utilities; Recharts
  (which needs real strings) reads the same vars via `core/lib/useThemeTokens`.
- `src/core/components/` — `TopologyDiagram` (SVG graph), `TimelineChart`
  (Recharts), `StatCards`, `SizingTable`, `ConfidencePanel`, `SeverityIcon`,
  `Modal` (generic dialog). The verdict card is `VerdictPanel` → one
  `FindingCard` per finding (holds the engine `info` → frontend `ok` severity
  map, `TONE`), `ScoreGauge` (animated score ring), `ScoreTerms` (strip +
  table views of `score_explanation`), `VerdictReportModal` (full report +
  plain-text copy).
- `src/core/lib/` — **helpers only, no tests here.** `format.ts` (severity maps
  + number formatting, mirrors `score.py` thresholds), `verdict.ts`
  (`buildVerdictText` for the copy button, score-point formatting),
  `graphLayout.ts` (pure layered-graph layout), `chaos.ts`,
  `useThemeTokens.ts`, `useReducedMotion.ts`, `themeStorage.ts`, `demo.ts`
  (the calibrated demo topology — changing its numbers invalidates the
  documented scores).
- `tests/` — Vitest unit tests (5 files, 62 tests), one file per module,
  `npm run test:unit`.
- Routes: `/` simulator, `/incidents`, `/import`, `/compare`, `/presets`.

## `tests/` — pytest, 13 files, ~180 tests

Mirrors `sim_core/`: `test_engine.py`, `test_score.py`, `test_findings.py`, …
`test_api.py` covers the FastAPI layer with `TestClient`. `test_cli.py` pins
CLI user-error paths (exit codes + stderr), which no simulation exercises.

## `docs/` — product, not code

`ROADMAP.md` (waves 1-6, the build order), `PLAN.md` (delivery phases),
`FOUNDER_GUIDE.md` (plain-language explanation of the machine).
**Read a single section, never the whole file.** `ROADMAP.md` is 23 KB.

**Where we are:** Wave 1. 1.1 (multi-seed confidence) shipped; 1.2 (findings)
shipped on the engine, rich verdict card in flight; 1.3 (right-sizing
suggestions) and 1.4 (node inspector) not started. Waves 2–6 untouched.

## `.agents/` — how the agents coordinate

`WORKFLOW.md` (Claude decides and reviews, Qwen executes), `LOCKS.md` (who owns
which path right now — read before editing), `tasks/` (task cards, from
`_TEMPLATE.md`), `skills/eleven-frontend-design/` (frontend design rules).

---

## Commands

```bash
pytest                                   # engine, 100+ tests
python -m sim_core demo                  # built-in demo run
python -m sim_core run my_topology.yaml --playbook db_failover
python -m sim_core compare multi-cloud --topology my_topology.yaml
uvicorn api.main:app --port 8000         # the API the frontend needs
ruff check . && mypy sim_core            # lint + strict types (see Known drift)

cd web && npm run dev                    # frontend on :3000
npm run test:unit && npm run typecheck && npm run lint
```

---

## Known drift — check before you trust something

- **`ruff check .` is not clean:** ~525 pre-existing findings, ~80% auto-fixable
  annotation upgrades (`Dict` → `dict`, `Optional[X]` → `X | None`). Judge a
  diff by whether it *adds* findings (`ruff check <changed files>`), not by
  the repo total, until a dedicated `ruff --fix` pass lands.
- **mypy is declared in `[dev]` extras but not installed in `venv/`,** so the
  `--strict` rule is currently unverified. `pip install -e .[dev]` installs it.
- `.env` holds GCP pricing credentials. Never read it, never echo it, never
  commit it.

---

## Handoff format

When your context runs out, overwrite `docs/HANDOFF.md` with this and nothing more.
It is a **work order, not a diary** — the next agent needs what to do next, not
what you learned. Target: under 400 words. Do not list files you merely read.

```markdown
# Handoff — <date> <short task name>

## Task
<One sentence. The goal, not the history.>

## Open these files, in this order
- path/to/file.py — <why>
- path/to/other.tsx — <why>

## Done
- <shipped thing> (<file>)

## Next
1. <the very next concrete action>
2. <the one after>

## Do not touch
- <paths another agent owns right now>

## Constraints discovered
- <non-obvious thing that cost you time — a contract, a gotcha, a threshold>

## Verify with
<the exact command that proves the work is correct>
```
