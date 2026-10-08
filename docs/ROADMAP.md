# Eleven — Feature Roadmap (playbook)

**What this document is.** The single roadmap for the next stretch of work.
Every feature below was discussed and approved with the founder; the order
is the order we execute. Written so that *any* agent (or human) can pick
the next wave and implement it without re-deriving context.

For what already shipped, read [PLAN.md](PLAN.md). For the plain-language
product/company picture, read [FOUNDER_GUIDE.md](FOUNDER_GUIDE.md).

---

> **Where we are (2026-10-09):** Wave 1. 1.1 ✅ · 1.2 ✅ · 1.3 ✅ (verified
> repair-and-trim "Fix it", before → after preview, change log in the page
> and report) · 1.4 not started · Waves 2–6 not started.
> Next up: **1.3e verified sizing labels** (the over/under/right-sized label
> still comes from mean utilization and can disagree with "Fix it"; see
> §1.3), then **1.4 node inspector**.

## 0. Current state (verified, as of 2026-10-04)

| Piece | State |
|---|---|
| Engine `sim_core/` | SimPy discrete-event engine; chaos mutates **live** state (reference-counted apply/release); 4 playbooks (`sim_core/playbooks.py`); pure-math `score.py` (resilience 0–100, cost grade A–F, cost extrapolation); `compare.py` (multi-cloud / what-if / sweep + knee) |
| API `api/` | FastAPI, stateless: `POST /simulate`, `POST /compare`, `GET /playbooks`, `GET /presets`, `GET /health` |
| Web `web/` | Next.js 15 + React 19 + TS + Tailwind. Pages: Simulator (`/`), Incidents, Import (YAML/JSON/Terraform), Compare, Presets. Sidebar + light/dark themes. Topology SVG diagram with hover-focus, long-edge de-emphasis, edge tooltips |
| Tests | ~106 green (engine + API via TestClient) |
| Not done | `docs/BACKLOG.md` (Phase 3 of PLAN.md was never written — the Tier 2 items live in §5 below), DB, auth, exports, anything in this roadmap |

**Ground rules (non-negotiable, from PLAN.md):**
Python 3.12+ full type hints · Pydantic = config only, never live SimPy
state · chaos mutates live state only · no `pass`/TODO stubs · every wave
ends with tests green + a real CLI/API/web run before "done" ·
verdicts (score/grade/findings) must be **pure deterministic functions**
of recorded metrics — no randomness, no AI.

**Business context (drives priorities):** CLI stays free; the web UI with
"all the cool stuff" is the paid tier; **data export is a paid feature**
("the data is the gold"); founder runs GTM once the product feels live.

---

## Wave 1 · Trust (engine-first, no new deps) — DO THIS FIRST

Goal: turn "a number" into "a verdict I can defend to a CTO".
Everything here is pure functions over data `summary()` already carries.

### 1.1 Multi-seed confidence run (LOCKED) — ✅ done (2026-10-04, `profile.py`, `n_seeds` on `/simulate`, `run --seeds`, `ConfidencePanel`)
- **Why:** one seed can be lucky/unlucky; an architecture is only as good
  as its worst day. Founder explicitly wants worst/typical/best.
- **BE shape:** `sim_core/profile.py` →
  `resilience_profile(summaries: list[dict]) -> dict` with
  `worst / typical / best` (min/median/max) over `resilience_score`,
  `p95`, `sla_compliance`, `cost_per_completed_request`; plus
  `per_run: [{seed, score, p95, sla, completion}]` and
  `score_spread = best − worst`.
  `api/routes/simulate.py`: `POST /simulate` accepts optional
  `n_seeds: int = 1` (or explicit `seeds: list[int]`). `n_seeds > 1` runs
  the engine once per seed (seed = `base_seed + i`, base from config or 42)
  and returns `{ "runs": [...], "profile": {...} }` (single-run response
  shape unchanged when `n_seeds == 1` → backward compatible).
  CLI: `eleven run <config> --seeds 5` prints the profile block.
- **FE:** Simulator page toggle "single run / 5-seed confidence"; headline
  shows typical with worst–best range bar; per-run table expandable.
- **Accept:** same config, `n_seeds=5` → profile values exactly match the
  5 individual run summaries; `n_seeds=1` response byte-identical to today;
  CLI flag works; tests in `tests/test_profile.py`.

### 1.2 Deterministic findings — "why this score" (LOCKED) — ✅ SHIPPED (engine 2026-10-06 · rich verdict card + full-report modal 2026-10-07)
- **Why:** the plain-English verdict a non-technical person screenshots.
  Same input ⇒ same text, always.
- **BE shape:** `sim_core/findings.py` →
  `build_findings(summary: dict) -> list[Finding]` where
  `Finding = {id, severity: "info"|"warn"|"crit", text, node?: str}`.
  Rule examples (each a small function, all testable):
  any node avg utilization > 90% → crit "X is your weakest link (97%
  utilization under spike)"; SLA compliance < 95% → crit with the worst
  path; retries/n > 2% → warn "retry storm amplifying load"; p95 > 1.5×
  SLA → warn with headroom math; a node right_sized=undersized → warn;
  all healthy → info "no structural weaknesses found".
  Sort by severity; cap at 5. Surface in `summary()["findings"]` and the
  API response for free.
- **FE:** "Verdict" block on results: 3–5 lines, severity-colored, node
  names link to the node inspector (Wave 3).
- **Accept:** fixed-dict tests asserting exact finding ids/text;
  deterministic across runs (seed-pinned e2e); appears in CLI report too.
- **Shipped (2026-10-07):** rich `Finding` fields (`title`, `why`, `impact`,
  `evidence[]`, `recommendation`) + `verdict_headline`; `score_explanation`
  point-by-point strip; `VerdictPanel` with animated gauge, band word,
  severity chips and finding cards; `VerdictReportModal` (score-math table,
  run context, Copy verdict, Esc / focus-trap). API contract pinned in
  `tests/test_api.py` — `findings` / `score_explanation` /
  `verdict_headline` must survive the API boundary in single- and multi-seed
  runs.

### 1.3 Right-sizing suggestions — "fix it" (LOCKED) — ✅ shipped (2026-10-09)
- **Why:** one click from "it's broken" to "it's fixed" — the demo moment.
- **BE shape:** `sim_core/suggestions.py` →
  `build_suggestions(nodes, summary) -> list[Suggestion]`; the exact rules
  are under "Built" below and pinned in `tests/test_suggestions.py`.
  (The original sketch, "propose `recommended_capacity`" / "oversized =
  util < 40%", was replaced on 2026-10-08: `recommended_capacity` includes
  queue backlog and reads 138 for a 2-slot node.) `service_time` /
  `hit_rate` suggestions are v2. `est_monthly_delta` ties into 4.4 later.
  FE applies a suggestion = mutate the config in client state → re-run.
- **FE:** on each node card / inspector: "raise capacity 8 → 12" button +
  "apply & re-run".
- **Fix diff (added 2026-10-08):** the applied suggestion set renders as a
  before→after table + total est. monthly delta + a copyable snippet of the
  changed config lines — native YAML primary, plus a clearly-labeled
  illustrative Terraform mapping for common roles. The snippet is the
  handoff artifact to the real environment; Eleven never mutates production
  (a validated config is the product, live patching is out of scope).
- **Accept:** fixed-dict tests (undersized → up, oversized → down,
  right-sized → no suggestion); applying a suggestion in the web UI re-runs
  with the changed config and the flagged node no longer appears in
  findings (verify manually on the 20-node fixture); fix-diff snippet
  round-trips (YAML parses back into a valid `SimulationConfig`).
- **Built, first pass (2026-10-08):** `sim_core/suggestions.py::build_suggestions(
  nodes, summary)` — capacity-only, contract pinned in
  `tests/test_suggestions.py` (27 tests): undersized → `max(current+1,
  ceil(current·mean_util/0.8))` (80% target; the p99 `recommended_capacity`
  is deliberately *not* the proposal — saturation backlog inflates it);
  oversized → one 25% step down `max(1, floor(current·0.75))`, only when
  it is a real cut whose predicted utilization stays ≤ 80%; right-sized →
  none. Raises first, then cuts, name tiebreak. `est_monthly_delta` =
  `cost_per_hour·(proposed−current)·720` (provisioned slots only), key
  always present (`None` when no rate). The list rides on
  `summary()["suggestions"]` (attached in `CloudSimulator.run()`), so the
  CLI "Fix it" block, the API and every multi-seed run get it with no API
  shape change. FE: `FixDiffPanel` (before→after table, total monthly Δ,
  Copy YAML / Copy Terraform (illustrative), Apply & re-run through the
  token-guarded `applyAndRerun`); the patched config becomes active
  ("· right-sized" chip + reset back to the baseline). The YAML snippet
  round-trips into a valid `SimulationConfig` (cross-language snapshot
  `tests/fixtures/suggestions_applied.yaml`, `web/tests/suggestions.test.ts`).
  Repeated apply & re-run converges — nothing ever flips a node to
  undersized.
- **Verified fix (2026-10-09, `sim_core/rightsize.py`):** the formula alone
  made things worse. It cut the healthy demo's worker 6 → 4 and breached the
  SLA, crept in 25% steps, and under every incident offered only cuts while
  the SLA breach stayed. `plan_fix(config, summary)` now proves every size by
  re-simulating on the same pinned seed: (1) **repair**: while crit/warn
  findings remain, raise the node that removes the most failure, trimmed to
  the smallest size that helps as much; (2) clear undersized flags;
  (3) **trim** to the smallest size that adds no finding and costs ≤ 0.5
  score points in total. Passes repeat to a fixed point, so one apply lands
  on the end state. `summary["fix_outcome"]` is the verification run itself
  (score/band before → after, findings resolved, findings capacity cannot
  fix). Demo: db_failover 81.2 At risk → 99.0 Resilient (db 3 → 6);
  dependency_timeout_cascade reports its SLA breach as not fixable by
  capacity. A run is ~5 ms; the search adds well under a second.
- **Verdict band (2026-10-09):** a crit finding caps the band word at
  "At risk", a warn at "Solid" (`score.py::capped_band`,
  `score_explanation.band_capped_by`); the web verdict uses one colour for
  bar, icon, word and gauge.
- **Change log (2026-10-09):** the Fix-it panel previews the verified
  outcome; every Apply & re-run is logged (before, prediction, measured
  re-run, findings cleared or introduced) in a "Changes applied" panel, the
  full report, and "Copy verdict".
- **Follow-up, 1.3e (next): verified sizing labels.** `component_sizing`
  status still comes from mean utilization (≤ 50% "oversized", ≥ 85%
  "undersized"), which ignores latency tails, pool size and incident
  headroom. The demo worker reads "oversized" at 32% though a cut to 4
  breaches the SLA; the db reads "right_sized" while it is the db_failover
  bottleneck. Make the label the verified answer (oversized = a free cut
  exists; undersized = repair or saturation needs a raise), keep utilization
  as evidence, and move the sizing table, topology colours, the
  `undersized` finding ("size-to 138" text) and the cost grade onto it.
- **Known limit:** a fix is verified under the scenario it was found in; a
  cut that is free under db_failover is not yet re-checked under the other
  incidents.

### 1.4 Node inspector (pure FE, no BE)
- **Why:** the diagram should answer "which box is the problem?" with one
  click. Data already in `summary()` per component.
- **FE:** click a node in `TopologyDiagram.tsx` → side panel (or popover):
  role, capacity ×N, avg/max utilization, p95 contribution, failures,
  retries, cost/hour, right-sizing verdict, that node's findings, and the
  fix-it button (1.3).
- **Accept:** works light + dark, keyboard accessible (Enter opens, Esc
  closes), `tsc`/`eslint` green.

**Wave 1 done =** every run answers: *how bad (worst case), why, and what
to change* — in the CLI, the API, and the web, identically.

---

## Wave 2 · Money — exports + paid gating

Goal: "the data is the gold" — export is the paid hook.

### 2.1 Export center (LOCKED)
- **BE shape:**
  - CSV: per-request log (`t, path, latency_ms, sla_ok, retries, failed`)
    — the raw data power users want.
  - Excel: workbook with Summary / Per-node / Findings sheets.
    **First new engine dep: `openpyxl`** (add to `pyproject.toml`).
  - JSON: the full `summary()` + config (already trivial).
  - New module `sim_core/exports.py` (pure functions:
    `to_csv(summary) -> str`, `to_xlsx(summary) -> bytes`,
    `to_json(summary) -> str`) + `POST /export` (body: summary-or-run-id,
    `format: csv|xlsx|json`) or a `format` param on `/simulate`.
  - FE: download buttons; format availability reflects the user's tier
    (free: CSV + JSON; paid: Excel + full per-request + API access —
    final split to confirm with founder when pricing lands).
- **Accept:** `tests/test_exports.py` (CSV round-trip row count ==
  completed+failed requests; xlsx opens and sheet names correct);
  download works in browser; CLI `eleven run ... --export csv xlsx`.

### 2.2 Tier gating (lands with Wave 4 auth)
- Gating lives in the **BE** (FE can't be trusted): a `tier` claim on the
  JWT; export/compare endpoints check it; 402 with a clean upgrade message.

---

## Wave 3 · Liveness — make it feel alive (THE GTM WAVE)

Goal: "watch the attack happen" instead of form → wait → report.
This wave doubles as the demo; the founder records the pitch video from it.

### 3.1 SSE run streaming (infrastructure — build first)
- **Why:** one 20s blocking request today; liveness needs a pipe.
- **BE shape:** engine hook — `CloudSimulator` gains an optional
  `event_sink: Callable[[dict], None]` called on each metrics tick
  (`{t, per_node_util, queue_depths, completed, failed}`) and on chaos
  window start/end (`{t, kind, node}`). API: `POST /runs` (starts run,
  returns `run_id`) + `GET /runs/{run_id}/events` (SSE: `progress` events,
  then `result`). Keep `POST /simulate` as-is (synchronous, backward
  compat). **No new deps** (Starlette `EventSourceResponse` needs
  `sse-starlette` — allowed, first API-only dep).
  In-memory run store is fine for now (dict + TTL); it becomes the `runs`
  table in Wave 4.
- **Accept:** `curl -N` shows ticks streaming then the final result;
  existing `/simulate` tests untouched and green.

### 3.2 Live run view — mission control (THE headline feature)
- **Why:** the single most "alive" thing we can show a prospect.
- **FE:** while a run executes: request pulses flowing along edges (rate ∝
  traffic), node util bars animating from the tick stream, the incident
  striking at its `start_time` (node flashes + event-feed line), degradation
  visible on the path. Beside the diagram: a scrolling **event feed**
  ("t=62s · order-db at 97%", "t=63s · 3 retries on payment-service",
  "t=85s · incident window ended"). Respect `prefers-reduced-motion`
  (bars update without flow animation).
- **Accept:** full run watched live in browser light + dark; event feed
  matches the engine's emitted events exactly (assert in a component test
  with a mocked SSE stream).

### 3.3 Click-to-fail (cheap add-on to 3.1/3.2)
- **Why:** interactive demo — pick the prospect's weakest box, break it.
- **BE:** `ChaosEvent.target` already exists (Phase 1). Add a UI-chaos
  builder in the API: `POST /simulate` body accepts
  `custom_chaos: [ChaosEvent]` merged with the playbook (validate target
  exists → 422 with clear message).
- **FE:** node inspector gains "inject failure here" → picks
  `component_failure` on that node for a 15 s window → run.
- **Accept:** failing `order-service` on the 20-node fixture drops its
  live capacity in-window (reuse the Phase 1 live-state test pattern);
  unknown node → clean 422.

### 3.4 Timeline replay
- **Why:** "here, at second 63, is exactly where it broke" — point at a
  moment in a finished run.
- **BE:** the ticks are already the 2 s `UtilizationSample`s; expose them
  ordered (they're in `summary()`/collector — surface a compact
  `timeline: [{t, per_node_util, completed, failed}]` array in the
  response; no new simulation).
- **FE:** slider + play/pause over the finished run; node states, util
  bars, and a mini latency line-chart follow the scrub position. Reuses
  the 3.2 live-render components (same props, different data source).
- **Accept:** scrub to the incident window → the degraded nodes show
  elevated util; play/pause works; no re-simulation happens (network tab:
  zero POSTs).

### 3.5 Real traffic shapes
- **Why:** "we simulated your Black Friday" — GTM vocabulary.
- **BE:** extend `TrafficPattern` (config.py, Pydantic-validated):
  `shape: "flat"|"diurnal"|"ramp"|"black_friday"` + params
  (business-hours low/high, ramp slope, plateau). Engine's arrival
  generator reads the shape at each tick. Add presets
  (`black_friday`, `eu_business_hours`) in `presets.py`.
- **FE:** traffic-shape picker with a tiny preview of the curve (SVG).
- **Accept:** each shape produces a distinct arrival curve (unit test on
  the rate schedule function); a `black_friday` run on the 20-node
  fixture breaks harder than `flat` (e2e assertion).

**Wave 3 done =** the product's one-line pitch is demonstrable live:
*watch the attack → replay the breaking moment → fail the node you choose →
on traffic shaped like their real day.*

---

## Wave 4 · SaaS — accounts, history, share (service-ification)

Goal: Eleven stops being a tool and starts being a service.
**New deps (expected):** `sqlalchemy`, `aiosqlite` or plain `sqlite3`,
`passlib[bcrypt]` (or `pwdlib`), `python-jose`/`pyjwt` for tokens.

### 4.1 Persistence + auth
- **BE:** SQLite via SQLAlchemy. Tables: `users(id, email, password_hash,
  tier, created_at)`, `runs(id, user_id?, config_json, summary_json,
  seed, playbook, created_at, slug)`. Endpoints: `POST /auth/register`,
  `POST /auth/login` (JWT), `GET /runs` (history, paginated),
  `GET /runs/{id}`, `DELETE /runs/{id}`. Free-tier rate limit on
  `/simulate` (e.g. 20 runs/day) — enforced BE-side.
  The Wave 3 in-memory run store moves into the `runs` table.
- **FE:** login/register screens; "History" page (list runs, reopen any,
  diff any two → feeds 3.x A/B view); guest mode = today's behavior.
- **Accept:** `tests/test_auth.py` + `tests/test_runs.py` (register →
  login → simulate → history contains it; wrong password 401; rate limit
  429 with clean message).

### 4.2 Shareable run link + branded report
- **BE:** `GET /share/{slug}` — public, read-only run (config + summary +
  findings, no user PII). Branded HTML report page (server-rendered is
  fine); the PNG renderer already exists for the CLI.
- **FE:** "Copy share link" button on every run; the share page doubles as
  the marketing artifact (beautiful standalone = free ads).
- **Accept:** shared link opens without auth, renders the full verdict,
  works light/dark, prints cleanly.

### 4.3 A/B diff view (FE, uses 4.1)
- Two runs side-by-side, **only deltas highlighted** (score, p95, SLA,
  cost, per-node util). Built on React Query + the compare page's existing
  chart components.
- **Accept:** diff of "before/after fix-it" shows the fixed node's util
  drop highlighted; negative deltas green.

### 4.4 Cost-of-failure + right-sizing savings (engine)
- **Why:** put dollars on every finding — money language for the pitch.
- **BE:** `sim_core/score.py` (or new `sim_core/money.py`):
  `cost_of_failure(summary, downtime_cost_per_min?) -> dict`
  (est. failed-requests × configurable $/request, or downtime-minutes ×
  rate) and `right_sizing_savings(suggestions, costs) -> $/mo` wired into
  1.3's `est_monthly_delta`. Deterministic, documented formula in
  docstring, fixed-dict tests.
- **Accept:** findings block shows "$" lines; same input ⇒ same $.

---

## Wave 5 · Engine realism & insight (backend depth)

The "more real + sharper insight" feature set. Order within the wave is
the suggested order; 5.1–5.3 are the first three (headline number, most
real incident, money on findings — as agreed).

### 5.1 Breaking point — "holds 11.3 rps, breaks at 12"
- **BE:** `sim_core/capacity.py` → `find_breaking_point(config, sla_target,
  lo, hi) -> {holds_rps, breaks_rps, headroom_pct}` via binary search
  over `base_rps` using the existing sweep machinery in `compare.py`
  (≤ 8 runs). Surfaced in `summary()["breaking_point"]` when computed
  (opt-in via API param `estimate_breaking_point: bool = false` — it costs
  extra runs) and CLI `--breaking-point`.
- **FE:** headline chip next to the score: "sustains ~11 rps (2× your
  current 6)".
- **Accept:** on a pinned fixture, `holds < breaks` always; the found
  boundary is stable ±1 run across repeated calls; e2e test.

### 5.2 Chaos library expansion
- **BE:** new handlers in `chaos.py` (all mutate **live** state,
  reference-counted like Phase 1):
  - `bad_deploy` — a node's service_time ×2 for the window (the #1
    real-world incident);
  - `partial_degradation` — only N% of a node's requests slow (per-request
    probability inside the handler);
  - `memory_leak` — service_time drifts linearly across the window
    (recompute per request from elapsed time);
  - `network_partition` — a node becomes unreachable (capacity 0) for one
    side only, where the graph allows it; else document the limitation.
  Register in `playbooks.py` where a named scenario makes sense
  (`bad_deploy` definitely: `Playbook` wrapping the new chaos type).
- **Accept:** each new type has a live-state test (state changes
  in-window, restores after) + a playbook entry + CLI list shows them.

### 5.3 Finite queues + real rejection
- **Why:** today a saturated node queues forever; real infra returns
  503s. This changes spike results *honestly*.
- **BE:** config `queue_limit: Optional[int]` per node (Pydantic, default
  `None` = today's unbounded behavior → backward compatible). Engine:
  when queue depth ≥ limit, the request is **rejected** (counted as
  `rejected`, not completed, penalizes SLA/completion). Metrics gain
  `rejected` + per-node max queue depth.
- **Accept:** with a tiny `queue_limit` on the 20-node fixture's spike,
  `rejected > 0` and completion drops vs. unbounded; default config runs
  are byte-identical to today's results (regression test).

### 5.4 Latency budget / path attribution
- **BE:** `sim_core/latency.py` → `path_budgets(summary) -> list`
  "8 s SLA: payment-service eats 4.1 s (51%)" — from per-node service-time
  samples already recorded. Pure function, fixed-dict tests.
- **FE:** inspector shows "share of SLA budget" per node.

### 5.5 Sensitivity analysis
- **BE:** `sim_core/sensitivity.py` → for each node, perturb the top
  param (capacity ±25%) in a cheap re-run, report % score delta:
  "p95 is 40% sensitive to order-db capacity". Opt-in API param
  (`sensitivity: bool`) — it costs N extra runs; document the cost.
- **Accept:** ranking is deterministic; the top-ranked node matches the
  #1 finding on the pinned fixture (e2e assertion).

### 5.6 Autoscaling policy simulation (was Tier 2)
- **BE:** config `autoscale?: {min_replicas, max_replicas, scale_up_after_s,
  cooldown_s, cold_start_s}`. Engine: a watcher process scales the
  node's *live capacity* with cold-start delay — so "your autoscaler
  reacts in 90 s; this incident lasts 45 s" is a testable, simulable
  sentence. Fixed-vs-elastic cost delta in the cost block.
- **Accept:** with a 45 s incident and 90 s cold start, scaling does NOT
  rescue the run (assert); with a 5 min incident it does (assert).

### 5.7 SLO / error-budget engine
- **BE:** config `slo?: {availability, p95_max_s}`. `score.py`-level pure
  fn: `error_budget_burn(summary, slo) -> {budget, remaining, days_left}`.
  "At this failure rate you burn your monthly error budget in 4 days."
- **Accept:** fixed-dict tests; appears in findings (1.2) when < 7 days.

### 5.8 Calibration from production stats (was Tier 2)
- **BE:** input `{observed_p50_ms, observed_p95_ms, rps}` per node → fit
  `service_time` (and optionally a two-point latency distribution) so the
  simulation reproduces their real numbers. `sim_core/calibrate.py` +
  `POST /calibrate` (config in → calibrated config out) + FE "paste your
  APM numbers" panel on Import.
- **Accept:** a calibrated 5-node config reproduces the input p50/p95
  within ±10% over a seed-5 profile (e2e).

### 5.9 Scenario objects (structural)
- **BE:** `Scenario = {name, traffic, chaos, duration, sla_target}` as a
  first-class Pydantic model; `/simulate` accepts `scenario` or the
  current flat shape (compat). Stored on `runs` (Wave 4) → enables
  "re-run this exact scenario", compare-suite, and the future canvas
  runner.
- **Accept:** scenario and flat request produce identical results
  (e2e); scenarios round-trip through the runs table.

### 5.10 Third-party dependency modeling (was Tier 2)
- **BE:** `external_api` role already exists — deepen: per-dependency
  rate limits (429 when exceeded), their own availability SLA, and chaos
  targeting them by name. The `external_api` presets (Stripe/Auth0-style)
  land here.
- **Accept:** a rate-limited payment-gateway produces 429-counted
  rejections under spike; playbook can target it by name.

---

## Wave 6 · Product face (FE polish, interleaved as time allows)

- **Cmd+K command palette** — run, switch incident, export, theme, open
  history. Pure FE.
- **Onboarding** — first-run walkthrough (3 steps: import/pick → run →
  read the verdict); better empty states; guest→account nudge at the
  export moment (the money wave's conversion point).
- **Topology builder v2 / canvas (n8n-style drag-drop + JSON paste)** —
  the big one; **after** Waves 1–4 because the inspector (1.4),
  click-to-fail (3.3), and scenario objects (5.9) are all inputs to it.
  Graph topology models already support arbitrary nodes/edges.

---

## GTM handoff (founder starts here)

The product is "live enough" for GTM when:
1. Wave 1 done (verdict you can defend) ✅ gate
2. Wave 3.2 done (live view) ✅ gate — **record the 90-second demo video
   from it: import → click-to-fail → watch it break → replay second 63 →
   fix-it → green**
3. `black_friday` preset working (3.5) — the "your Black Friday" line
4. Deploy: API + web on GCP (single VM or Cloud Run; SQLite → Postgres
   only if multi-instance), custom domain, HTTPS
5. Landing page (can reuse the share page, 4.2, as the design donor) +
   waitlist form (one table, one endpoint)
6. Pricing page: free CLI / free tier (CSV+JSON, 20 runs/day) / paid
   (Excel, API, history, share links) — exact split confirmed by founder
7. 5–10 beta invites (accounts from 4.1) → collect "what would make you
   pay" answers → those answers order Wave 5

Founder's GTM duties (outside this repo): positioning copy, outreach,
beta recruiting, pricing decision. Everything above that is code lives in
this roadmap.

---

## Explicitly deferred (do not start without founder sign-off)

- Canvas topology builder (Wave 6 — gated on 1.4 / 3.3 / 5.9)
- Multi-cloud billing integration (we estimate; we don't bill)
- Real cloud provisioning / Terraform apply (we simulate; we don't deploy)
- AI-generated narrative text (verdicts stay deterministic by rule)
- Multi-tenant isolation beyond tier flags (single-region, small team)

---

## How to use this file (for the next agent)

1. Work top-to-bottom. A wave is "done" only when its Accept criteria pass
   AND a real end-to-end run (CLI + API + web) was demonstrated.
2. When you finish a feature, add a one-line "✅ done (date, notes)" next
   to its heading — keep this file the single source of truth.
3. If a design detail here conflicts with the code, the code wins; fix
   this file in the same commit.
4. New dependencies: state them in the wave's "New deps" line before
   installing. Current dep additions so far: fastapi, uvicorn, httpx(dev),
   planned: openpyxl (W2), sse-starlette (W3), sqlalchemy + auth stack (W4).
5. Never break: backward-compatible API shapes, `n_seeds=1` default,
   `queue_limit=None` default, flat `/simulate` body alongside `scenario`.
