# Eleven

*(repo: sim_engine)*

A **pre-deployment cloud-resilience simulator**.

Eleven models a cloud architecture — load balancer, worker pool, read-through cache, and database — as a digital twin and stress-tests it with traffic spikes and chaos injection, so you can see how it degrades *before* you deploy a single real instance.

Built on [SimPy](https://simpy.readthedocs.io/) (discrete-event simulation), [Pydantic v2](https://docs.pydantic.dev/) (validated configuration **only**), [NumPy](https://numpy.org/), [Pandas](https://pandas.pydata.org/), and [Matplotlib](https://matplotlib.org/).

## Why SLA compliance, not completion rate?

Under chaos, requests almost always *complete* — they just get slow. The meaningful resilience signal is **SLA compliance**: the fraction of requests that finish within your latency budget. Eleven tracks both, and the report highlights the drop in SLA compliance as load and failures kick in.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"  # or: pip install -r requirements.txt
```

## Quick start

```bash
python -m sim_core demo
```

Runs the built-in 130-second scenario (a traffic spike plus a component failure, a cache outage, and network latency), prints the headline metrics, and writes `eleven_report.png` + `summary.json`.

### From your own config file

Describe your proposed architecture in YAML/JSON (components, traffic profile, chaos windows) and run it:

```bash
python -m sim_core run my_topology.yaml --report out.png --json summary.json
```

See `my_topology.yaml` in the repo root for a complete example (a two-worker fan-out topology with a cache and a database).

### As a library

```python
from sim_core import (
    AppWorkerConfig,
    CacheConfig,
    ChaosEvent,
    ChaosEventType,
    CloudSimulator,
    DatabaseConfig,
    LoadBalancerConfig,
    SimulationConfig,
    TopologyConfig,
    TrafficPattern,
    render_report,
)

config = SimulationConfig(
    seed=42,
    duration=120.0,
    sla_target=20.0,
    topology=TopologyConfig(
        load_balancer=LoadBalancerConfig(name="lb", max_capacity=12, service_time=0.5),
        app_worker=AppWorkerConfig(name="worker", max_capacity=6, service_time=2.0),
        cache=CacheConfig(name="redis", max_capacity=8, service_time=0.3, hit_rate=0.85),
        database=DatabaseConfig(name="db", max_capacity=4, service_time=5.0),
    ),
    traffic=TrafficPattern(
        base_rps=1.5,
        duration=120.0,
        spike_rps=6.0,
        spike_start=60.0,
        spike_duration=20.0,
    ),
    chaos=[
        ChaosEvent(
            event_type=ChaosEventType.CACHE_OUTAGE,
            intensity=1.0,
            start_time=50.0,
            interval=45.0,
            duration=10.0,
        ),
    ],
)

sim = CloudSimulator(config)
summary = sim.run()          # headline metrics (dict)
render_report(sim.collector)  # writes eleven_report.png
```

### Provider-calibrated presets

Don't want to invent `max_capacity` / `service_time` / `cost_per_hour` yourself? Start from a named small-tier preset — one per provider × role (AWS, Azure, GCP) — seeded from realistic, publicly published list prices and throughput ballparks:

```python
from sim_core.presets import aws_alb, aws_app_worker, aws_elasticache, aws_rds_small

topology = TopologyConfig(
    load_balancer=aws_alb(),          # ~ALB, $0.05/h class
    app_worker=aws_app_worker(),      # ~EC2 t3-class, $0.05/h class
    cache=aws_elasticache(),          # ~cache.t3.small, $0.05/h class
    database=aws_rds_small(),         # ~db.t3.small, $0.05/h class
)
```

The presets bake in a directional `cost_per_hour`, so cost + right-sizing reporting works out of the box. Tune any of them with `preset.model_copy(update={"max_capacity": 4, ...})`. The numbers are **directional estimates, not guaranteed benchmarks** — for exact live rates use the `aws-prices` / `prices` / `gcp-prices` catalog commands.

## Compare runs

`compare` runs the same topology multiple times under different conditions and diffs the summaries — one engine, three faces:

- **`multi-cloud`** — re-calibrates every component with a provider preset (AWS / Azure / GCP) and compares latency, SLA, retries, and cost side by side;
- **`what-if`** — the baseline vs one or more patched parameters (e.g. shrink the database, heat up the spike);
- **`sweep`** — one parameter over a value range, with a directional right-sizing **knee** (the last point where extra capacity still pays off).

```bash
# AWS vs Azure vs GCP for the same logical architecture
python -m sim_core compare multi-cloud --topology my_topology.yaml \
  --json comparison_mc.json --png comparison_mc.png

# Baseline vs a shrunken database + a hotter spike
python -m sim_core compare what-if --topology my_topology.yaml \
  --set postgres.max_capacity=4 --set traffic.spike_rps=12 \
  --json comparison_wf.json

# Right-sizing sweep: where does extra DB capacity stop paying off?
python -m sim_core compare sweep --topology my_topology.yaml \
  --param postgres.max_capacity --values 2,4,8,16 --metric p95_latency \
  --json comparison_sw.json --png comparison_sw.png
```

`--set PATH=VALUE` accepts top-level fields (`duration`), `traffic.*`, `chaos.<i>.*`, and `<node>.<field>` paths. Every mode prints a per-run metrics table plus the per-component right-sizing verdict, and writes the full diff (values + deltas vs baseline) with `--json` and a comparison chart with `--png`. Set a fixed `seed` in the config for meaningful A/B comparisons. Preset calibrations and the knee heuristic are **directional estimates, not benchmarks**.

The engine is also a plain library — `from sim_core import compare` gives you `run_one`, `run_many`, `diff_runs`, `set_path`, `apply_provider`, `sweep`, and `knee_point` directly.

## Metrics

| Metric | Meaning |
| --- | --- |
| `requests` | Total requests that reached the end of the pipeline. |
| `completion_rate` | Fraction that finished without interruption (stays ~1.0 under load — they slow down rather than fail). |
| `sla_compliance` | **The key resilience signal** — fraction finishing within `sla_target`. |
| `avg/p50/p95/p99_latency` | End-to-end latency distribution (computed over all completed requests). |
| `cache_hit_rate` | Fraction of lookups served from cache (misses fall through to the DB). |
| `total_cost` | Simulated spend over the run (USD). Two-part billing, mirroring real provider pricing: each component accrues `cost_per_hour × (max_capacity + in_use + queue) × dt` per sampling tick. The `max_capacity` term is the **provisioned base** - you pay for the slots you sized whether they are busy or not (oversizing therefore shows up as waste); the `in_use + queue` term meters the active workload, so a chaotic run that saturates and queues costs more than a healthy one. |
| `cost_breakdown_by_component` | That total split per component. |
| `component_sizing` | Per-component right-sizing verdict: `mean_utilization` (the verdict driver — in steady state it equals offered load per slot, the classic capacity-planning number), `p95_utilization` / `p95_queue` (context), `recommended_capacity` (size-to-your-p99 concurrent demand), and `status` — `undersized` (mean utilisation ≥ 85%), `right_sized`, or `oversized` (mean utilisation ≤ 50% — paying for slots that sit idle). Computed on every run, even with costs disabled. |

Cost is opt-in: set `cost_per_hour` on any component in the config (e.g. the provider's hourly rate for that tier - the `azure-prices` / `aws-prices` / `gcp-prices` commands resolve live rates for exactly this). Leave it at the default `0.0` and cost fields report zero. The sizing check is independent of cost and always advises whether a topology is over-, under-, or well-provisioned for its load.

The report also plots per-component queue depth and utilisation over time.

## Architecture

Strict separation of **configuration** (immutable Pydantic data) from **live simulation state** (mutable SimPy resources). Chaos mutates the *live* state — it never rewrites historical metrics after the fact.

```
sim_core/
├── config.py        # Pydantic v2 models (validated config data only)
├── topology.py      # Component + Topology: live simpy.Resource wrappers
├── traffic.py       # Poisson arrivals, base curve + optional spike window
├── chaos.py         # Real failure injection (capacity / latency / cache)
├── playbooks.py     # Named incident playbooks (db failover, latency spike, ...)
├── metrics.py       # RequestRecord, utilisation samples, pandas aggregation
├── score.py         # Deterministic resilience score + cost grade + cost extrapolation
├── engine.py        # CloudSimulator: wires everything into a simpy.Environment
├── compare.py       # Compare-runs engine: multi-cloud / what-if / sweep + knee
├── presets.py       # Provider-calibrated component presets (aws/azure/gcp)
├── viz.py           # Matplotlib reports -> PNG (run + comparison charts)
├── __main__.py      # `python -m sim_core` entry point
├── cli/             # argparse tree split by concern (the "control file" is __init__)
│   ├── sim.py       #    run / demo / compare / playbooks commands
│   └── pricing.py   #    prices / aws-prices / gcp-prices catalog commands
├── price_source/    # Provider price catalogs (azure / aws / gcp + constants)
└── py.typed         # PEP 561: this is a fully-typed package

tests/
└── ...              # one suite per concern (engine, chaos, cost, presets, compare, ...)
```

### How the request path works

`load balancer -> worker -> (cache) -> database`

A cache **hit** serves the response and skips the database; a **miss** falls through to the database. Each hop acquires a slot on a real `simpy.Resource`, services for an exponential-distributed duration, and releases. The `with` block is interrupt-safe, so no slot leaks even if a request is aborted mid-flight.

### Chaos events

| Event | Live effect | `intensity` meaning |
| --- | --- | --- |
| `component_failure` | Reduces a random component's live capacity (can reach 0 = full outage). | Fraction of capacity removed. |
| `network_latency` | Inflates every component's live mean service time. | Multiplier minus one (0.5 ⇒ 1.5x). |
| `cache_outage` | Drives the cache hit-rate down (1.0 ⇒ fully disabled). | Fraction of hit-rate removed. |

Each event holds the disruption for `duration`, restores the original value, and repeats every `interval` seconds.

## Incident playbooks

Instead of hand-crafting chaos windows, apply a named, realistic incident to any run. Each playbook picks its victim by *role* (database, cache, external API) — not node name — so the same incident works on any topology that has a node with that role:

| Playbook | What it does |
| --- | --- |
| `db_failover` | 5 s full outage on the database, then 30 s at half capacity while the replica catches up. |
| `cross_region_latency_spike` | Every hop in the path ~80% slower for 15 s. |
| `cache_eviction_storm` | 90% of the cache hit-rate lost for 20 s; lookups fall through to the DB. |
| `dependency_timeout_cascade` | The external API (or the database, when no external-API node exists) times out for 10 s, then retry traffic slows the whole path for 20 s. |

```bash
# List the built-in incidents
python -m sim_core playbooks list

# Run your topology under a DB failover
python -m sim_core run my_topology.yaml --playbook db_failover

# Add an incident to the built-in demo
python -m sim_core demo --playbook cache_eviction_storm
```

The playbook's chaos events are *appended* to whatever chaos the config already defines (never replaced), and the first injection lands ~25% into the run (floored at 5 s) so the incident always fires inside the horizon. All four incidents mutate **live** SimPy state through the same apply/release machinery as raw chaos events, so overlapping windows on the same node compose instead of clobbering each other.

As a library: `from sim_core import get_playbook` — `config = get_playbook("db_failover").apply(config)`.

## Architecture score

Every run ends with a one-liner a non-technical person reads in two seconds:

```
Resilience 98/100 | Cost grade D | $1,517/mo (steady $1,175/mo) | $18.5k/yr
```

- **Resilience score (0–100)** — deterministic weighted blend of the run's own metrics: SLA compliance (60 pts, dominant) + completion rate (40 pts), minus penalties for hard failures (up to 30), retry pressure (up to 10), and p95 latency running past the SLA target (up to 10). Same summary ⇒ same score, always.
- **Cost grade (A–F)** — 70% right-sizing quality (share of `right_sized` vs `oversized`/`undersized` verdicts — undersized scores worst, it is a reliability risk) + 30% cost efficiency (share of spend sitting on oversized components). Bands: A ≥ 0.90, B ≥ 0.75, C ≥ 0.55, D ≥ 0.35, else F.
- **Cost extrapolation** — linear steady-state projection of the sampled run to `{hour, month, year}`, honestly labeled: *assumes the same load sustained 24/7*. A planning number, not a bill.
- **Cost split (steady vs under-load)** — the bill is split into the *provisioned base* (the slots you sized, billed whether busy or not: `cost_base`) and the *metered* part (billed on active + queued demand, driven by traffic, spikes and chaos: `cost_metered`). The headline shows both: the monthly under-load bill and, in parentheses, the steady base-only bill (`steady_state_cost_extrapolation`) — the answer to "is this expensive because of the chaos test, or because I sized it big?"

All three are computed in `sim_core/score.py` — pure functions over the `summary()` dict, no new simulation and no new dependencies — so they surface automatically in the JSON report and flow into the API (below) and the frontend for free. The PNG report carries a color-coded score badge (green ≥ 75, amber ≥ 50, red below).

As a library: `from sim_core import resilience_score, cost_grade, cost_extrapolation, score_headline`.

## API (FastAPI)

The same engine exposed as a stateless HTTP service — config in, results out. No database, no sessions: every request carries a full `SimulationConfig` and gets the full summary (score, grade, cost split, extrapolations) back, so results are reproducible purely from the request body.

```bash
uvicorn api.main:app --reload   # from the repo root; interactive docs at http://127.0.0.1:8000/docs
```

| Endpoint | What it does |
| --- | --- |
| `POST /simulate` | Full `SimulationConfig` + optional `playbook` + optional `include_report_png` → the complete `summary` dict, plus the rendered report as base64 when asked. Unknown playbook → 404, bad config → 422. |
| `POST /compare` | `multi-cloud` (baseline + AWS/Azure/GCP preset calibration), `what-if` (`set: ["path=value", ...]`), or `sweep` (`param` + `values`, returns the right-sizing `knee`). Returns the baseline-relative diff. |
| `GET /playbooks` | The four named incidents (name + description) — feeds the incident picker. |
| `GET /presets` | The 12 provider × role presets as plain JSON — realistic starting points per component. |
| `GET /health` | Liveness + engine version. |

CORS is currently permissive (`*`) so the Phase 5 Next.js frontend can call it from any local origin; tighten to the real frontend origin once it has one. Tests: `tests/test_api.py` (FastAPI `TestClient`, covers every endpoint, both happy paths and the error mappings).

## Reproducibility

All randomness draws from `numpy.random.default_rng(seed)` split into two independent streams: one dedicated to traffic arrivals, one for service times / cache hits / chaos. A fixed seed gives a bit-for-bit reproducible run, and because arrivals live on their own stream, the arrival pattern is identical across scenarios with the same seed - so comparing a healthy run against a chaos run isolates the chaos effect itself rather than a shifted arrival stream.

## Testing

```bash
pytest
```

## Design notes

- `Resource.capacity` is read-only in SimPy, so `Component.set_capacity` updates its backing field to model a live capacity change — the one private API touch in the codebase.
- `env.run(until=duration)` bounds the run; the traffic, chaos, and sampler loops are simply suspended once the clock reaches the horizon.
