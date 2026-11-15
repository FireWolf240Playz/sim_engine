/**
 * One small run, as `GET /runs/{id}/events` sends it (roadmap 3.1): lb ->
 * worker -> db, 12 s, a tick every 2 s, and half the db's slots gone from
 * t=3 to t=7. Written by hand to the engine's contract
 * (`tests/test_event_sink.py`, `tests/test_feed.py`, `tests/test_api_runs.py`):
 * the shapes are the engine's, the numbers are chosen to be easy to read.
 *
 * Shared by the Wave 3 web contracts. Do not edit.
 */
import type {
  ChaosEventCfg,
  ChaosMark,
  FeedLine,
  RunMeasured,
  RunStarted,
  SimulateResponse,
  Summary,
  TimelineTick,
  TopologyEdge,
  TopologyNode,
} from "@/core/types";

export const NODES: TopologyNode[] = [
  { name: "lb", role: "load_balancer", max_capacity: 10, service_time: 0.1 },
  { name: "worker", role: "worker", max_capacity: 6, service_time: 0.5 },
  { name: "db", role: "database", max_capacity: 4, service_time: 1.0 },
];

export const EDGES: TopologyEdge[] = [
  { source: "lb", target: "worker", probability: 1.0 },
  { source: "worker", target: "db", probability: 1.0 },
];

export const INTERVAL = 2;
export const DURATION = 12;

function tick(
  time: number,
  util: [number, number, number],
  counts: { arrived: number; completed: number; failed?: number },
  flow: [number, number],
  db: { capacity?: number; queue?: number; retries?: number } = {},
): TimelineTick {
  return {
    time,
    p50_latency: counts.completed ? 1.2 : null,
    p95_latency: counts.completed ? 2.4 : null,
    sla_met: counts.completed ? 1 : null,
    arrived: counts.arrived,
    completed: counts.completed,
    failed: counts.failed ?? 0,
    per_component: {
      lb: { utilization: util[0], queue_length: 0, capacity: 10, in_use: Math.round(util[0] * 10), retries: 0 },
      worker: { utilization: util[1], queue_length: 0, capacity: 6, in_use: Math.round(util[1] * 6), retries: 0 },
      db: {
        utilization: util[2],
        queue_length: db.queue ?? 0,
        capacity: db.capacity ?? 4,
        in_use: Math.round(util[2] * (db.capacity ?? 4)),
        retries: db.retries ?? 0,
      },
    },
    edge_flow: flow,
  };
}

export const TICKS: TimelineTick[] = [
  tick(0, [0, 0, 0], { arrived: 0, completed: 0 }, [0, 0]),
  tick(2, [0.1, 0.3, 0.5], { arrived: 4, completed: 2 }, [4, 3]),
  tick(4, [0.2, 0.5, 0.95], { arrived: 5, completed: 3 }, [5, 4], { capacity: 2, queue: 2 }),
  tick(6, [0.1, 0.8, 1.0], { arrived: 4, completed: 1, failed: 2 }, [4, 4], { capacity: 2, queue: 6, retries: 3 }),
  tick(8, [0.3, 0.6, 0.8], { arrived: 6, completed: 5 }, [6, 5], { queue: 1 }),
  tick(10, [0.1, 0.2, 0.4], { arrived: 2, completed: 4 }, [2, 2]),
];

export const CHAOS: ChaosMark[] = [
  { t: 3, phase: "start", event_type: "component_failure", node: "db", intensity: 0.5 },
  { t: 7, phase: "end", event_type: "component_failure", node: "db", intensity: 0.5 },
];

export const LINES: FeedLine[] = [
  { t: 3, kind: "incident_start", severity: "crit", node: "db", text: "component failure on db: 50% of its capacity is gone" },
  { t: 4, kind: "saturated", severity: "warn", node: "db", text: "db at 95%" },
  { t: 6, kind: "retries", severity: "warn", node: "db", text: "3 retries on db" },
  { t: 6, kind: "failures", severity: "crit", node: null, text: "2 requests failed" },
  { t: 7, kind: "incident_end", severity: "info", node: "db", text: "component failure on db ended" },
  { t: 10, kind: "recovered", severity: "info", node: "db", text: "db back to 40%" },
];

export const SCHEDULE: ChaosEventCfg[] = [
  { event_type: "component_failure", intensity: 0.5, start_time: 3, interval: 100, duration: 4, target: "db" },
];

export const STARTED: RunStarted = {
  run_id: "run-1",
  seed: 42,
  seeds: null,
  duration: DURATION,
  metrics_interval: INTERVAL,
  chaos: SCHEDULE,
};

/** The summary before the plan: no `suggestions`, no `fix_outcome`. */
const MEASURED_SUMMARY = {
  resilience_score: 71,
  requests: 21,
  failed_requests: 2,
  p95_latency: 2.4,
  cost_grade: "B",
  findings: [],
} as unknown as Summary;

export const MEASURED: RunMeasured = { summary: MEASURED_SUMMARY, typical_seed: null };

/** The same run with the plan attached: what `/simulate` would return. */
export const RESULT: SimulateResponse = {
  summary: { ...MEASURED_SUMMARY, cost_grade: "C", suggestions: [], fix_outcome: null } as unknown as Summary,
  report_png_b64: null,
  chaos: SCHEDULE,
  timeseries: TICKS,
};

type Wire = Array<[name: string, data: string]>;
const on = (name: string, data: unknown): [string, string] => [name, JSON.stringify(data)];

/** The stream in the order the engine sends it: a feed line right after its cause. */
export const WIRE: Wire = [
  on("started", STARTED),
  on("tick", TICKS[0]),
  on("tick", TICKS[1]),
  on("chaos", CHAOS[0]),
  on("feed", LINES[0]),
  on("tick", TICKS[2]),
  on("feed", LINES[1]),
  on("tick", TICKS[3]),
  on("feed", LINES[2]),
  on("feed", LINES[3]),
  on("chaos", CHAOS[1]),
  on("feed", LINES[4]),
  on("tick", TICKS[4]),
  on("tick", TICKS[5]),
  on("feed", LINES[5]),
  on("measured", MEASURED),
  on("result", RESULT),
];
