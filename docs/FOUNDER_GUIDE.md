# Eleven — Founder's Guide

**What this document is.** Your private map: how the machine works (in plain
language), what exists today, where the real ceilings are, and the roadmap
for both the product and the company — milestones, money, hiring, GTM.
Read it once top-to-bottom (~30 min), then keep it as a reference.
For delivery-level detail on what shipped, see [PLAN.md](PLAN.md).

---

## 1. The product in one paragraph

Eleven is a **pre-deployment digital twin** for cloud architectures. You
describe (or upload) your system — load balancer, workers, cache, database,
dependencies — and we *simulate* it: real traffic flows through it, realistic
incidents hit it (database failover, cache eviction storm, region latency
spike), and you get back a **resilience score (0–100), a cost grade (A–F),
and a monthly/yearly cost projection** — with a reproducible, provable report.
The value proposition: *find out before you deploy whether your architecture
survives its own worst day — and what it will cost.* No cloud credits burned,
no staging environment, no 4 a.m. incident to learn it.

## 2. How the machine works (plain language)

The engine is a **discrete-event simulation** built on SimPy (a standard,
battle-tested Python simulation library). In one breath:

1. **Requests arrive on a random schedule** (Poisson-like) at your configured
   rate. The *pattern* of randomness is fixed by the **seed** — a number that
   programs the dice.
2. **Each request walks your topology.** At every node it may have to wait in
   a queue if all capacity slots are busy, then it spends a (slightly
   random) service time inside the node. We record the total end-to-end time.
3. **Chaos events physically change the running system** — a node's live
   capacity shrinks, its service time inflates, or its cache hit rate
   collapses, for a defined window, then recovers. This is not "pretend" —
   the queues actually grow and requests actually slow down during the
   window. (This is a hard engineering rule of this codebase.)
4. **After the run, pure arithmetic produces the verdicts.** The resilience
   score, the cost grade, and the cost extrapolation are *pure functions* of
   the recorded metrics — no randomness, no AI. Same numbers in ⇒ same
   verdict out, always.

That's the entire machine. Everything else is interface.

## 3. One request's journey (trace it once, you'll never lose it)

```
traffic generator ──> LOAD BALANCER ──> WORKER ──> CACHE? ──> DATABASE
   (seeded random       wait if full     wait if     hit → done    wait if full
    arrival schedule)   serve 0.5s      full, serve  miss → DB     serve 5s
                        ↓                2.0s         ↓
                     queued? ───────────┴──────────────┴──> latency recorded
```

- Somewhere in the middle, if a chaos window is active on that node, the node
  has **fewer slots** and/or **slower service** — the request feels it
  directly (longer wait).
- If a request exceeds its timeout budget, it is retried (up to the limit) —
  retries and failures are counted and *penalize the score*.
- At the end, every request has a total latency; the percentiles (p50/p95),
  SLA compliance, failures, and retries roll up into the summary → score.

## 4. What is random, what is fixed, what is computed

| Layer | Controlled by | If you change it… |
|---|---|---|
| **Randomness** (arrival times, service-time jitter) | the **seed** | same architecture behaves differently (different dice) |
| **Architecture** (nodes, capacities, service times, traffic) | the **config** (YAML/JSON) | everything shifts — this is the real lever |
| **Verdicts** (score, grade, cost) | **pure math** over the recorded metrics | never changes for the same run |

**The three truths to repeat in any investor or client conversation:**

1. Same config + same seed ⇒ **bit-for-bit identical** run. (Reproducible.)
2. Different seeds ⇒ different *samples* of the same architecture. (That's
   why the confidence-band feature exists — one run is a sample, not the truth.)
3. The score is **arithmetic, not AI** — auditable, testable, and we say so.

## 5. What the headline numbers mean

> `Resilience 98/100 | Cost grade D | $1,517/mo (steady $1,175/mo) | $18.5k/yr`

- **Resilience 0–100** — 60% SLA compliance + 40% completion rate, minus
  penalties for failed requests (heavy), retries (lighter), and p95 running
  past the SLA budget. SLA-dominant: a slow system is bad; a failing one is
  worse. (Exact formula: `sim_core/score.py` docstring.)
- **Cost grade A–F** — 70% "is your sizing sane" (right-sized > oversized >
  *undersized is the worst*, because undersized = reliability risk) + 30%
  "are you paying for capacity you don't use."
- **$X/mo** — what this load costs, **assuming the same load sustained 24/7**
  (a planning figure, never a bill). The **steady** figure strips out
  load/chaos-driven metered usage and shows the provisioned baseline.

## 6. Where the REAL ceilings are (roadmap items, not physics)

The engine is a **general graph simulator** — nodes + edges + typed roles.
The "only 4 boxes" shape you see in the demo is a property of our *demo
config and UI*, **not** of the simulator. The actual ceilings are:

| Ceiling | What it blocks | Roadmap home |
|---|---|---|
| No **queue** node type yet | real failure stories (queue buildup) | Phase A/C |
| No **importers** (YAML/JSON/Terraform) | "your architecture, not our demo" | **Phase A (first slice)** |
| No **calibration** from production stats | "your real numbers" credibility | Phase B+ |
| No **run fingerprint** / N-seed confidence | trust in exported data | Phase A |
| No **accounts / storage / paid export** | the business model | Phase B |
| No **canvas builder** | the "draw it" aha-moment | Phase C |

None of these require re-architecting the engine. That is the single most
important technical fact in this document.

## 7. The three verification commands

When anyone (investor, founding dev, future-you) makes a technical claim,
you can check it:

```powershell
# 1. Reproducibility — run twice, identical headline line
.\venv\Scripts\python.exe -m sim_core run my_topology.yaml --playbook db_failover
.\venv\Scripts\python.exe -m sim_core run my_topology.yaml --playbook db_failover

# 2. Chaos is real — the playbook run must score LOWER than the clean run
.\venv\Scripts\python.exe -m sim_core run my_topology.yaml

# 3. The whole contract — 100+ tests, all green
.\venv\Scripts\python.exe -m pytest -q
```

## 8. What exists TODAY (state of the repo, ground truth)

- **Engine** (`sim_core/`): graph topology, seeded dual RNG streams, chaos
  with reference-counted live-state mutation, retries, timeouts, cost
  accounting (per-hour rates, steady vs metered split).
- **Incident playbooks** (`playbooks.py`): 4 named incidents
  (`db_failover`, `cross_region_latency_spike`, `cache_eviction_storm`,
  `dependency_timeout_cascade`) — CLI + API + web.
- **Score + cost** (`score.py`): resilience 0–100, grade A–F, $/mo·/yr —
  pure functions, unit-tested to the digit.
- **Compare** (`compare.py`): multi-cloud calibration, what-if A/B, capacity
  sweep + knee detection.
- **Presets** (`presets.py`): 12 provider-calibrated starting points
  (AWS/Azure/GCP small tiers).
- **API** (`api/`): FastAPI, stateless — `POST /simulate`, `POST /compare`,
  `GET /playbooks`, `GET /presets`, `GET /health`.
- **Web app** (`web/`): Next.js + React 19 + Tailwind — Simulator, Incidents,
  Compare, Presets; light/dark themes; collapsible icon-rail sidebar.
- **Tests:** 100+ green across engine, score, cost, playbooks, compare, API.
- **Not yet:** upload/importers, run fingerprint, N-seed confidence,
  accounts/DB, paid export, canvas builder, calibration.

## 9. Product roadmap (the build order)

### Phase A — "Upload → proof"  ← FIRST, load-bearing
**Goal:** any real architecture in, a defensible report out.

1. **Canonical architecture document** — one JSON schema: typed nodes + edges
   + per-node params. Everything else (upload, paste, canvas) is just another
   editor for this one format.
2. **YAML/JSON upload** with validation + **preview before running**.
3. **Run fingerprint** — hash(config + seed + engine version) stamped on every
   summary and report.
4. **N-seed confidence band** — the system runs the architecture under N
   seeds; the score shows as a band (e.g. `78 [71–86]`). The user never rolls
   dice; the system samples and proves robustness.
5. **Deterministic summary** — a template-generated narrative (same input ⇒
   byte-identical text): *"Under db_failover, 8.4% of requests exceeded SLA;
   bottleneck shifted to worker queueing…"* **No AI in the verdict path —
   that is a feature, not a limitation.**
6. **Share-report** — linkable HTML report page + the existing PNG.
7. **Queue + multi-pool node types** in the engine.
8. **`terraform show -json` importer (AWS first)** — user runs one local
   command, uploads the JSON, we map resources → nodes
   (`aws_lb→LB, ecs_service/k8s→worker, elasticache→cache, rds→db, sqs→queue`).
   Params estimated from instance type, **labeled "estimated"**.
   (Raw `.tf` HCL parsing: explicitly skipped in v1 — brittle, low value.)

**Done when:** a stranger uploads their Terraform, sees their graph, runs a
playbook, and gets a report with a fingerprint they can re-run.

### Phase B — "The business"
- Accounts (email + password, argon2), sessions; runs saved per account.
- **SQLite first** (zero-ops, portable schema → Postgres later is a swap).
- **Paid export surface:** CSV/XLSX, multi-run exports, programmatic API
  access — this is the gold, and it's paid.
- Gating table goes live (see §10).

### Phase C — "The face"
- **Canvas topology builder** (React Flow, n8n-style): drag nodes, wire
  edges, side-panel params, copy/paste JSON = the architecture document
  itself. The document format from Phase A makes this an *editor*, not an
  invention.
- Production-stats **calibration** (paste real p50/p95/RPS → fitted
  service times) — the "your real workload" headline.
- Optional: "describe your architecture in a sentence → graph" (local LLM,
  runs on our 4090 — AI as the door *in*, never as the author of verdicts).

## 10. Free vs paid (the business model, locked early)

| Capability | Free | Paid |
|---|---|---|
| CLI (all commands) | ✅ unlimited | — |
| Web simulator (demo topology) | ✅ | — |
| Upload your own architecture | limited runs/hr | ✅ |
| Share-report (HTML) | ❌ | ✅ |
| **Exports (CSV/XLSX, multi-run, API)** | single-run CSV | ✅ |
| Run history / accounts | 1 run | ✅ |
| Compare + confidence bands | ❌ | ✅ |

Rationale: the CLI stays **fully free** — it is the marketing, the developer
trust-builder, and the converter. We gate *surfaces and scale*, not
capability. Anonymous web runs are rate-limited (cost + conversion nudge).

## 11. Company milestones

| Milestone | Product state | Business bar | Capital move |
|---|---|---|---|
| **M0 — now** | engine + score + API + web demo, tests green | 0 revenue, solo + day job | bootstrap |
| **M1 — proof** | Phase A done (upload, fingerprint, bands, deterministic report) | 5–10 design partners, first €0–2k MRR | — |
| **M2 — business** | Phase B (accounts, paid export) | **15–30 paying teams, €8–15k MRR, ~20%/mo growth**, named logos | optional survival raise **€250–500k @ €1.5–2.5M post** — or keep bootstrapping |
| **M3 — the raise** | C + calibration, export data assets | **€20k+ MRR, 3 months growth evidence, 200–400 stars / ~100 forks** | **€750k–1.2M @ €3–4M post**; founder quits job in 30 days |
| **M4 — scale** | enterprise needs (SSO, audit logs) | €60–80k MRR | AE#2 + Head of Sales joins here |
| **M5 — 10×** | — | **€200k MRR** | Series A conversation |

**Hiring sequence (never out of order):**
founding dev (M3) → AE #1 (M3, mo. 3–4, clones the documented sales motion)
→ AE #2 (only after AE #1 hits quota 2 quarters) → **Head of Sales only at
€60–80k MRR** — a HoS before there is a team is a €7k/mo strategy meeting.

## 12. The money plan

- **Hosting (beta):** one GCP `e2-small` VM (~$15–20/mo) + domain (~$10/yr)
  + Cloudflare free tier ≈ **$25/mo all-in**. GCP new-account **$300 credit
  ≈ 12 months of hosting free**. Stop/VM-start saves more if needed.
- **Graduation trigger (a number, not a date):** when anonymous runs queue
  each other → API to Cloud Run + DB to Cloud SQL, timed to first revenue.
- **GPU:** the existing **4090 (24GB)** is the R&D machine (local models:
  pair-programming, NL→topology later). A 5090 is a 1.3× bump — dropped from
  the budget; cash stays runway.
- **M3 raise split (~€1M / 18 mo):** ~40% R&D (founding dev, canvas,
  calibration) · ~30% GTM (AE #1, marketplaces, design partners) ·
  ~20% marketing (SEO, PLG polish, the "State of Cloud Resilience"
  benchmark report) · ~10% infra + buffer.
- **One-sentence pitch:** *"The money buys 18 months of compounding: R&D
  lifts the price ceiling, marketing fills the funnel for free, one AE
  closes what I can't reach from a day job."*

## 13. GTM one-pager

- **Hook (the whole pitch):** *"We simulated your architecture. It survives
  a database failover 8 out of 10 times — and costs $1,240/mo to run.
  Here's the proof, reproducible in one command."*
- **ICP:** platform/SRE engineers at 10–200-person companies **choosing
  cloud or capacity right now** (simulator value is pre-invoice, not post).
- **Channels:** GitHub + strong README (the free CLI *is* the landing page);
  one "Show HN" at M1; r/devops + infra Discords (participate, don't post);
  5 warm intros > 500 cold emails; AWS/Azure/GCP marketplaces at M4.
- **Beta mechanics:** 15–25 design partners after A+B; one feedback channel;
  no public floodgates until paid export exists.
- **Killer content asset:** public *"State of Cloud Resilience"* benchmark
  (anonymized runs: "we simulated 50 common architectures, 41% fail a basic
  DB failover") — built on the data-gold insight, costs a sprint, compounds
  forever.
- **North-star belief (from the founders we admire):** *the data is the
  gold, the UI is the pitch.* Exports are paid; every design decision
  (deterministic summary, fingerprints, confidence bands) exists to make the
  exported numbers **defensible**.

## 14. Risks & watch-items

| Risk | Watch-item / mitigation |
|---|---|
| **Name collision** with ElevenLabs (voice AI) — SEO confusion, trademark scope in classes 9/42 | cheap EU/US trademark-scope check **before** the name is on invoices |
| Free-tier compute abuse | anonymous rate limits from day one (§10) |
| Single-founder bus factor | FOUNDER_GUIDE + PLAN.md + tests = the transfer kit for the founding dev |
| Selling a "guess" as a "measurement" | every imported/estimated param is **labeled**; calibration is the fix |
| Raising too early/too big | M2 bar first; round sized to the €20k-MRR plan, not the €8k hope |

## 15. Glossary (the scary words, one line each)

- **Discrete-event simulation** — time jumps from event to event (arrival,
  service-done) instead of ticking every millisecond.
- **SimPy** — the Python library that schedules those events.
- **Seed / RNG** — the number that programs all randomness; same seed = same
  dice. Two independent streams: arrivals vs service times.
- **Capacity** — how many requests a node serves *simultaneously*.
- **Service time** — how long a request spends inside a node (mean + jitter).
- **Hit rate** — fraction of cache requests answered without hitting the DB.
- **RPS** — requests per second (the load you simulate).
- **p50 / p95** — the latency at the 50th/95th percentile; p95 = "the bad day
  most users don't get."
- **SLA target** — your latency budget; compliance = share of requests under it.
- **Chaos event / playbook** — a scheduled, *live-state* degradation
  (capacity down, service slow, cache dead) / a named bundle of them.
- **Right-sizing** — per-node verdict: right / oversized (wasted $) /
  undersized (reliability risk).
- **Steady vs metered cost** — provisioned baseline vs load-driven usage on top.
- **Knee (sweep)** — the capacity point past which extra capacity buys
  almost no SLA improvement.
- **Fingerprint** — hash(config + seed + engine version): the reproducibility
  receipt on every result.
- **Confidence band** — N seeded runs ⇒ min/median/max; "sampled, not rolled."
- **Calibration** — fitting service times to *your* production stats.

## 16. Where to look (file → what it is)

| File | One line |
|---|---|
| `sim_core/config.py` | all validated configuration (Pydantic, read-only) |
| `sim_core/engine.py` | the CloudSimulator — runs the SimPy world |
| `sim_core/topology.py` | live nodes (real SimPy resources, capacity you can shrink) |
| `sim_core/metrics.py` | records everything; `summary()` is the source of all verdicts |
| `sim_core/score.py` | score/grade/cost math — **pure, no engine imports** |
| `sim_core/playbooks.py` | the 4 named incidents |
| `sim_core/compare.py` | multi-cloud / what-if / sweep + knee |
| `sim_core/presets.py` | 12 provider-calibrated starting points |
| `api/` | FastAPI (stateless) — the contract the web app talks to |
| `web/` | Next.js app — Simulator / Incidents / Compare / Presets |
| `tests/` | the 100+ contracts that keep all of the above honest |

---

*Keep this file current: when a milestone is reached or a decision changes,
edit the table, don't add a new doc. One map, many readers — including the
founding dev on day one.*
