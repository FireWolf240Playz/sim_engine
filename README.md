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
├── config.py       # Pydantic v2 models (validated config data only)
├── topology.py     # Component + Topology: live simpy.Resource wrappers
├── traffic.py      # Poisson arrivals, base curve + optional spike window
├── chaos.py        # Real failure injection (capacity / latency / cache)
├── metrics.py      # RequestRecord, utilisation samples, pandas aggregation
├── engine.py       # CloudSimulator: wires everything into a simpy.Environment
├── viz.py          # Matplotlib three-panel report -> PNG
├── __main__.py     # CLI: `python -m sim_core run | demo`
└── py.typed        # PEP 561: this is a fully-typed package

tests/
└── test_engine.py
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

## Reproducibility

All randomness draws from `numpy.random.default_rng(seed)` split into two independent streams: one dedicated to traffic arrivals, one for service times / cache hits / chaos. A fixed seed gives a bit-for-bit reproducible run, and because arrivals live on their own stream, the arrival pattern is identical across scenarios with the same seed - so comparing a healthy run against a chaos run isolates the chaos effect itself rather than a shifted arrival stream.

## Testing

```bash
pytest
```

## Design notes

- `Resource.capacity` is read-only in SimPy, so `Component.set_capacity` updates its backing field to model a live capacity change — the one private API touch in the codebase.
- `env.run(until=duration)` bounds the run; the traffic, chaos, and sampler loops are simply suspended once the clock reaches the horizon.
