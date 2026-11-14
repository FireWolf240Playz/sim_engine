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
                                                           ├─> suggestions.py (right-sizing "fix it", needs node configs)
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
| `config.py` | 22 | **All Pydantic models.** `SimulationConfig`, `GraphTopologyConfig`, `Edge`, `TrafficPattern`, `ChaosEvent`, `ComponentRole`. Open for: adding a config field, a validation rule. Grep the class name; never read whole. Note `TopologyConfig` is the legacy 4-node shape kept for compatibility — `.to_graph()` converts it to `GraphTopologyConfig`, which is what the engine actually runs. Every model is strict (`extra="forbid"`): an unknown field is an error, and `hit_rate` is only valid on a cache. `Edge.group` marks alternative edges (one is taken per request). `TrafficPattern.expected_requests()` / `peak_rps()` back the run-size limit `MAX_EXPECTED_REQUESTS`. |
| `topology.py` | 11 | **Live SimPy wrappers.** `Component` (owns the `simpy.Resource`, `apply_capacity_drop`, `apply_latency_spike`, `apply_hit_rate_drop`, `draw_service_time`) and `Topology` (the graph: `component()`, `outgoing_edges()`, `cache()`). `set_capacity` hands freed slots to the waiting queue itself: SimPy only re-checks a queue on an arrival or a release. A capacity drop rounds the *removed* slots half up (half of 3 leaves 1). `Topology.choose()` picks among grouped edges. Open for: anything about live capacity or service time. |
| `engine.py` | 11 | **`CloudSimulator`** — owns `simpy.Environment`, `serve_request()`, `_walk()` (the DAG traversal, cache hit/miss, fan-out probabilities), `_use_component()` (acquire/timeout/retry/release). `run(suggest=True)` attaches `summary["suggestions"]` and `summary["fix_outcome"]` from `rightsize.plan_fix` after `summary()` (it has the node configs; `MetricsCollector.summary()` alone carries neither key). Verification re-runs use `suggest=False`. `CloudSimulator.seed` is always an int (drawn when the config has none), and the plan verifies on it. Open for: how a request moves through the graph. |
| `traffic.py` | 4 | Poisson arrivals following the configured profile, exact across rate changes (a gap never crosses a spike edge; exponential growth is inverted, not stepped). A constant rate draws what it always drew. Small, read it whole. |
| `chaos.py` | 4 | The three injectors: `component_failure`, `network_latency`, `cache_outage` (hits the cache its `target` names, else the first). Small, read it whole. |
| `metrics.py` | 16 | `RequestRecord`, `UtilizationSample`, `MetricsCollector`. **`summary()` and `timeseries()` are the contract above.** Every arrival is accounted for: `record_arrival` + `settle_unfinished` record a request still in the system at the horizon and already past the SLA budget as failed (`unfinished_requests`, `overdue_requests`), and a failed request never counts as SLA-met. The provisioned cost base bills the *configured* capacity (an outage is not a discount). `component_sizing` also carries the exact `waited_requests` and `peak_in_use`. Open for: adding a metric. |
| `score.py` | 18 | Pure functions on `summary()`: `resilience_score`, `cost_grade`, `cost_extrapolation`, `cost_per_completed_request`, `score_headline`, `score_color`, `score_band`, `capped_band` (a crit finding caps the band at "At risk", a warn at "Solid"), `score_explanation` (carries `band_capped_by`). The formula lives once, in `_score_parts`; `cost_grade` grades each node on `verified_status` when present, so `run()` takes the grade again after attaching the labels. |
| `findings.py` | 21 | Pure functions on `summary()`: `build_findings` → the plain-English "why this score" verdict, `verdict_headline`. Each `Finding` has `id`, `severity`, `text`, `node?` plus optional `title`, `why`, `impact`, `evidence` (`[{label, value}]`), `recommendation`. Each rule is its own `_`-prefixed function with a threshold constant at module top. A node past 90% is one finding (`util_weak_link`), not that plus `undersized`. **Add a finding = add one function + register it.** |
| `suggestions.py` | 7 | Pure flagging formula: `build_suggestions(nodes, summary)` → `Suggestion` list (`node`, `param`, `current`, `proposed`, `reason`, `est_monthly_delta`). Says *which* nodes look off by mean utilization; it no longer sets the sizes the summary carries. |
| `rightsize.py` | 14 | **Verified "Fix it".** `plan_fix(config, summary)` → `{suggestions, outcome}`: repair (raise whichever node removes the most crit/warn findings), clear undersized flags, then trim (smallest size that adds no finding, ≤ 0.5 score points below the settled score, repeated until a re-run of the result would offer nothing), all proven by re-simulating on the pinned seed, one run per distinct sizing. The run being explained is the plan's "before", and sizes that would replay the same events (nobody waited for the node) are not simulated. Cost: ~0.1 s on the demo, 5–13 s on the 19-node examples. `FixOutcome` = score/band before → after, `resolved`, `unfixed`. Contract: `tests/test_suggestions.py` (locked). |
| `profile.py` | 7 | Multi-seed confidence: `resilience_profile` (worst/typical/best), `typical_index`, `timeseries_band` (per-tick P95 min/max across seeds). |
| `playbooks.py` | 7 | The four named incidents: `db_failover`, `cross_region_latency_spike`, `cache_eviction_storm`, `dependency_timeout_cascade`. Each is a function returning `ChaosEvent`s, targeted **by role**. Open for: adding an incident. |
| `presets.py` | 12 | AWS/Azure/GCP small-tier component presets. Flat list of factory functions. `cost_per_hour` is per slot: each preset spreads its instance's list price over its slots. |
| `compare.py` | 12 | One core, three faces: `run_many`, `diff_runs`, `set_path` (dotted-path patching), `apply_provider`, `sweep`, `knee_point`. Backs `eleven compare multi-cloud|what-if|sweep`. |
| `importers.py` | 26 | Terraform/YAML/JSON → `SimulationConfig`. `import_architecture()` is the entry; everything else is private estimation tables. An imported load balancer picks one worker per request (edge group `workers`) and reaches the cache only through it. **Open only for import bugs.** |
| `viz.py` | 8 | Matplotlib report PNG. `render_report`, `render_comparison`. |
| `exports.py` | 16 | **Export center (2.1).** Pure, no `sim_core` import: `to_csv(request_log)` (one row per request, `CSV_COLUMNS`), `to_json` (summary + config + seed, replayable), `to_xlsx` (`XLSX_SHEETS`: Summary, Per-node, Findings; `openpyxl` imported inside). `EXPORT_FORMATS` is the registry the API and the CLI both read (`extension`, `media_type`, `tier`, `uses_summary`); `render_export(name, ...)` → bytes is the only way a file is made. `spreadsheet_safe` stops a cell being read as a formula. The rows come from `MetricsCollector.request_log()`. |
| `cli/sim.py` | 26 | `run` / `demo` / `playbooks` / `compare` subcommands + all terminal formatting. `--export FORMAT ...` / `--export-base PATH` on `run` and `demo` write the run that was just printed (`write_exports`, bytes, never a second simulation); with `--seeds N` it is the typical run. |
| `cli/pricing.py` | 14 | `prices` / `aws-prices` / `gcp-prices` catalog subcommands. |
| `price_source/` | 60 | Live cloud price catalogs (AWS/Azure/GCP + `base.py` HTTP/cache plumbing). **Self-contained. Ignore unless the task is pricing.** Needs `.env` credentials for GCP. |

## `api/` — stateless FastAPI over the engine

No database, no sessions. Each request carries a full config, gets the full
summary back. `api/schemas.py` reuses `sim_core.SimulationConfig` directly, so
the API validates by exactly the same rules as the CLI.

- `main.py` — `create_app()`, `/health`, CORS (a local frontend on any port by
  default; `ELEVEN_CORS_ORIGINS` names other origins).
- `limits.py` — what one call may ask for: `check_budget` (expected requests
  × runs), `MAX_SWEEP_VALUES`. Over the limit is a 422 that states the numbers.
- `routes/simulate.py` — `POST /simulate`. Single run, or multi-seed when
  `n_seeds > 1` (then also returns `seeds` / `runs` / `profile` /
  `typical_seed`, and with `include_timeseries` the typical run's
  `timeseries` plus `timeseries_band`). The single-run key set is pinned.
  `apply_playbook_or_404` is the one playbook lookup every route uses.
- `routes/export.py` — `GET /export/formats`, `POST /export`
  `{config, format, playbook?}` → the file, named in `Content-Disposition`.
  The API keeps no run, so it simulates the config again: a config without a
  seed is a 422. CSV skips the Fix-it plan. `tier` is a label, not a gate.
- `routes/compare.py` — `POST /compare` (multi-cloud / what-if / sweep).
- `routes/imports_api.py` — `POST /imports`.
- `routes/playbooks.py`, `routes/presets.py` — `GET`, trivial.

## `web/` — Next.js 16 + React 19 + Tailwind v4 frontend

**`web/AGENTS.md` is auto-generated by `next dev` — it is not a repo doc.**

- `src/core/types.ts` — hand-written mirror of the API contract. **Changing
  `summary()` means changing this file.**
- `src/core/api/client.ts` — the only place `fetch` is called. Typed
  `ApiError`, per-call timeouts. `request` reads JSON; `download` reads a
  file as a blob (`api.exportRun`). Both go through `send` and `refusal`,
  so a failure reads the same from either.
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
  (Recharts), `StatCards` (the six-tile strip: resilience, SLA compliance,
  P95, failed requests, cost grade, projected bill; the SLA and P95 tiles
  take their colour from the engine's `sla_breach` / `p95_headroom`
  findings, never from a threshold of their own), `SizingTable`,
  `ConfidencePanel`, `SeverityIcon`,
  `Modal` (generic dialog), `FixDiffPanel` (1.3: before→after table, monthly
  Δ, Copy YAML / Terraform, per-row and all-at-once Apply & re-run), `FixLog`
  (`FixOutcomePreview`: the verified before → after; `FixHistoryList`: every
  apply and its measured re-run). The verdict card is `VerdictPanel` → one
  `FindingCard` per finding (holds the engine `info` → frontend `ok` severity
  map, `TONE`), `ScoreGauge` (animated score ring), `ScoreTerms` (strip +
  table views of `score_explanation`), `VerdictReportModal` (full report +
  plain-text copy, including the change log), `NodeInspector` (1.4: the
  node dialog — its config, the verified measurement at the current size,
  cost, the findings that name it, and its pending Fix-it change; opens
  from a topology node's `onOpen`), `ExportMenu` (2.1c: the Export
  button in the page toolbar; it opens the list of formats and downloads
  the run on screen; a paid format wears a "Pro" badge and is not locked). The verdict's bar, icon, band
  word and gauge share one colour, `format.ts::verdictSeverity` (the worse of
  score and worst finding).
- `src/core/lib/` — **helpers only, no tests here.** `format.ts` (severity maps
  + number formatting, mirrors `score.py` thresholds), `verdict.ts`
  (`buildVerdictText` for the copy button, score-point formatting),
  `suggestions.ts` (1.3: `applySuggestions`, `pendingSuggestions`,
  `buildFixDiff`, snippets, dependency-free `configToYaml` whose output
  pytest loads through Pydantic), `fixHistory.ts` (the apply → re-run log:
  `startRecord`, `completeRecord`, `describeRecord`, `historyText`),
  `timeline.ts` (`withBand`: attaches the multi-seed P95 band to ticks),
  `graphLayout.ts` (pure layered-graph layout), `topologyView.ts`
  (`nodeViews`: the diagram's per-node primitive view model),
  `runResult.ts` (`shareResult`: a new result reuses unchanged parts of the
  old one, so plain `memo` skips), `nodeInspector.ts` (`inspectNode`: one
  node's inspection — config, verified label + peaks, monthly cost, findings,
  pending fix; pure, no React), `verdictView.ts` (`findingKeys`: stable
  id+node keys for the finding cards), `exports.ts` (2.1c: `exportRequest`
  names the measured run and, in 5-seed mode, its typical seed;
  `exportFilename`, `filenameFromDisposition`, `exportChoices`), `chaos.ts`,
  `useThemeTokens.ts`, `useReducedMotion.ts`, `themeStorage.ts`, `demo.ts`
  (the calibrated demo topology — changing its numbers invalidates the
  documented scores).
- `src/core/state/RunStateContext.tsx` exposes `measuredRun` (the config and
  incident the result on screen was simulated with; an export sends this,
  never the controls). It also owns `applyAndRerun(suggestions)`:
  patches the active config and runs it through the same token-guarded path,
  and `fixHistory` (only an apply's own re-run completes its record; a new or
  reset architecture starts a new log).
- Re-runs patch in place: results stay mounted and dim while a run is in
  flight (`.eleven-results[aria-busy]`), the gauge glides old → new, and the
  timeline draws in only for its first data.
- `tests/` — Vitest unit tests (17 files, 179 tests), one file per module,
  `npm run test:unit`.
- Routes: `/` simulator, `/incidents`, `/import`, `/compare`, `/presets`.
- The simulator page (`src/app/page.tsx`) is a dashboard: a sticky toolbar
  (run mode, Export, Run), the incident bar, the tile strip, the topology at
  full width, then two columns that flow independently (left: verdict,
  confidence; right: Fix it, changes applied, timeline, sizing). Below `xl`
  the columns stack. `Shell` keeps the sidebar in view and caps the canvas
  at 1600px. The topology never shrinks below its natural size and grows to
  at most 1.3× of it.

## `tests/` — pytest, 29 files, ~425 tests

Mirrors `sim_core/`: `test_engine.py`, `test_score.py`, `test_findings.py`, …
The engine-review fixes are pinned one file per rule: `test_accounting.py`
(every arrival counted), `test_live_state.py` (capacity restore, per-cache
decisions), `test_pricing_errors.py`, `test_consistency.py` (grade = label,
explanation = score), `test_cost_model.py`, `test_chaos_semantics.py`,
`test_fix_plan.py` (plan cost and seed), `test_traffic.py`,
`test_config_strict.py`, `test_edge_groups.py`, `test_api_limits.py`. Each
opens with what was measured before the fix.
The export center is three files: `test_exports.py` (the formats),
`test_api_export.py` (the route), `test_export_cli.py` (`--export`).
`test_api.py` covers the FastAPI layer with `TestClient`. `test_cli.py` pins
CLI user-error paths (exit codes + stderr), which no simulation exercises.
`tests/fixtures/suggestions_applied.yaml` is written by the Vitest suite and
read by `test_suggestions.py`: run the web tests first or that test skips.

## `docs/` — product, not code

`ROADMAP.md` (waves 1-6, the build order), `PLAN.md` (delivery phases),
`FOUNDER_GUIDE.md` (plain-language explanation of the machine).
**Read a single section, never the whole file.** `ROADMAP.md` is 23 KB.

**Where we are:** Wave 1. 1.1 (multi-seed confidence), 1.2 (findings +
rich verdict card), 1.3 (verified "Fix it" + change log), 1.3e–1.3g
(render streamlining), 1.3h (verified sizing labels) and 1.4 (node
inspector) shipped: Wave 1 is done. Wave 2: 2.1 export center shipped
(engine 2.1, API + CLI 2.1b, web menu 2.1c). 2.2 paid gating lands with
Wave 4's auth, so Wave 3 (liveness) is next. Waves 3–6 untouched.

## `.agents/` — how the agents coordinate

`WORKFLOW.md` (Claude decides and reviews, Qwen executes), `LOCKS.md` (who owns
which path right now — read before editing), `tasks/` (task cards, from
`_TEMPLATE.md`), `skills/eleven-frontend-design/` (frontend design rules).

The loop per feature: Claude writes a card + failing tests (Claude-locked) →
Qwen makes them pass and appends `## Result` → Claude snapshots the attempt to
a `qwen/<item>-attempt-N` branch, reviews the diff, and either writes a
follow-up card with new failing tests or approves → Alexander commits →
Claude marks the roadmap item ✅. Qwen never commits and never marks ✅.

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
