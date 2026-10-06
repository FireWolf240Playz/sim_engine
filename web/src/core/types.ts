/**
 * TypeScript mirrors of the sim_core / API contract.
 *
 * The FastAPI endpoints (see `api/schemas.py`) are the source of truth;
 * these exist so the UI is fully typed without importing Python.
 */

export type ComponentRole =
  | "load_balancer"
  | "worker"
  | "cache"
  | "database"
  | "external_api"
  | "generic";

export interface TopologyNode {
  name: string;
  role: ComponentRole;
  max_capacity: number;
  service_time: number;
  hit_rate?: number | null;
  cost_per_hour?: number | null;
}

export interface TrafficConfig {
  base_rps: number;
  duration: number;
  spike_rps?: number | null;
  spike_start?: number | null;
  spike_duration?: number | null;
}

/** Directed, probabilistic request-path edge (see `sim_core.config.Edge`). */
export interface TopologyEdge {
  source: string;
  target: string;
  probability?: number;
}

export type ChaosEventType =
  | "component_failure"
  | "network_latency"
  | "cache_outage";

export interface ChaosEventCfg {
  event_type: ChaosEventType;
  intensity: number;
  start_time: number;
  interval: number;
  duration?: number | null;
  target?: string | null;
}

export interface SimulationConfig {
  seed: number;
  duration: number;
  metrics_interval?: number;
  sla_target?: number | null;
  topology: { nodes: TopologyNode[]; edges?: TopologyEdge[] };
  traffic: TrafficConfig;
  chaos?: ChaosEventCfg[];
}

/** A concrete [start, end] disruption window expanded from a chaos event. */
export interface ChaosWindow {
  start: number;
  end: number;
  event_type: ChaosEventType;
  intensity: number;
}

/** Finding severity tokens — the BE strings from `sim_core/findings.py`. */
export type FindingSeverity = "info" | "warn" | "crit";

/** One evidence chip: a label + the run's actual number behind a finding. */
export interface EvidencePair {
  label: string;
  value: string;
}

/**
 * One plain-English verdict line (roadmap 1.2, `sim_core/findings.py`).
 * `id` is a stable slug; `node` names the component for per-node findings
 * (rendered as a link to the node inspector once step 1.4 lands).
 *
 * The rich verdict pass adds optional fields (pre-rework summaries omit
 * them, so they stay optional): `title` is the bold card heading, `why`
 * the mechanism, `impact` the quantified consequence, `evidence` the
 * numbers, `recommendation` the concrete fix.
 */
export interface Finding {
  id: string;
  severity: FindingSeverity;
  text: string;
  node?: string | null;
  title?: string | null;
  why?: string | null;
  impact?: string | null;
  evidence?: EvidencePair[] | null;
  recommendation?: string | null;
}

/** One term of the score decomposition (`sim_core/score.py`). */
export interface ScoreTerm {
  label: string;
  /** Signed contribution in points: positive = credit, negative = penalty. */
  points: number;
  detail: string;
}

/** "How the score is built" — the point-by-point score decomposition. */
export interface ScoreExplanation {
  /** Must equal `Summary.resilience_score` for the same run. */
  score: number | null;
  /** True when the raw blend hit the 0..100 rail before rounding. */
  clamped: boolean;
  /** Plain-English band word: Resilient / Solid / At risk / Fragile. */
  band: string;
  terms: ScoreTerm[];
}

export type SizingStatus = "right_sized" | "oversized" | "undersized";

export interface ComponentSizing {
  status: SizingStatus;
  mean_utilization: number;
  p95_utilization: number;
  p95_queue: number;
  recommended_capacity: number;
}

export interface CostExtrapolation {
  hour: number;
  month: number;
  year: number;
}

/** Shape of `POST /simulate` → `summary` (see `sim_core/metrics.py::summary`). */
export interface Summary {
  requests: number;
  completion_rate: number | null;
  failed_requests: number;
  sla_compliance: number | null;
  avg_latency: number | null;
  p50_latency: number | null;
  p95_latency: number | null;
  p99_latency: number | null;
  cache_hit_rate: number | null;
  total_retries: number;
  total_cost: number;
  cost_breakdown_by_component: Record<string, number>;
  component_sizing: Record<string, ComponentSizing>;
  sla_target: number | null;
  cost_per_completed_request: number | null;
  resilience_score: number | null;
  cost_grade: string | null;
  /**
   * Deterministic "why this score" verdict (roadmap 1.2) — 3–5
   * severity-ordered lines, or a single `info` line when all is healthy.
   */
  findings: Finding[];
  /** "How the score is built" — mirrors `resilience_score` exactly. */
  score_explanation: ScoreExplanation | null;
  /** One-sentence overall verdict (breaks / bends / holds), deterministic. */
  verdict_headline: string | null;
  cost_base: number;
  cost_metered: number;
  cost_extrapolation: CostExtrapolation | null;
  steady_state_cost_extrapolation: CostExtrapolation | null;
}

/** One `timeseries()` tick (see `sim_core/metrics.py::timeseries`). */
export interface TimelineTick {
  time: number;
  p50_latency: number | null;
  p95_latency: number | null;
  sla_met: number | null;
  per_component: Record<
    string,
    { utilization: number; queue_length: number }
  >;
}

export interface SimulateResponse {
  summary: Summary;
  report_png_b64: string | null;
  /** Effective chaos schedule (config + appended playbook events). */
  chaos: ChaosEventCfg[];
  timeseries: TimelineTick[] | null;
  /**
   * Multi-seed confidence mode (roadmap 1.1) — present only when the run
   * used `n_seeds > 1`; single-run responses omit these keys entirely.
   */
  seeds?: number[] | null;
  /** One run per seed, in seed order. */
  runs?: Array<{ seed: number; summary: Summary }> | null;
  /** worst/typical/best across the seeds (see `sim_core/profile.py`). */
  profile?: ResilienceProfile | null;
}

/** One metric's worst/typical/best (keys mirror `sim_core/profile.py`). */
export interface ProfileTri {
  resilience_score: number | null;
  sla_compliance: number | null;
  p95_latency: number | null;
  cost_per_completed_request: number | null;
}

export interface ProfilePerRun {
  seed: number | null;
  score: number | null;
  p95: number | null;
  sla: number | null;
  completion: number | null;
}

export interface ResilienceProfile {
  n_runs: number;
  seeds: (number | null)[] | null;
  worst: ProfileTri;
  typical: ProfileTri;
  best: ProfileTri;
  score_spread: number | null;
  per_run: ProfilePerRun[];
}

export interface PlaybookInfo {
  name: string;
  description: string;
}

export interface PlaybookListResponse {
  playbooks: PlaybookInfo[];
  count: number;
}

export interface HealthResponse {
  status: string;
  version: string;
}

// ---------------------------------------------------------------------------
// Compare-runs (see `api/schemas.py::CompareResponse`, `sim_core.compare`)
// ---------------------------------------------------------------------------

export type CompareMode = "multi-cloud" | "what-if" | "sweep";

/** One labelled run inside `CompareResponse.diff` (baseline-relative). */
export interface CompareRunDiff {
  label: string;
  /** Metric key -> value for this run (null where the run has no value). */
  values: Record<string, number | null>;
  /** Metric key -> value minus the baseline (null where either side is null). */
  deltas: Record<string, number | null>;
  /** Component name -> sizing status (e.g. "right_sized"). */
  sizing: Record<string, string>;
}

export interface CompareResponse {
  mode: CompareMode;
  diff: { baseline: string; runs: CompareRunDiff[] };
  /** Sweep only: the capacity value where improvement flattens (null = none). */
  knee: number | null;
}

export interface CompareRequest {
  config: SimulationConfig;
  mode: CompareMode;
  /** what-if: non-empty list of "path=value" strings. */
  set?: string[];
  /** sweep: dotted path to sweep. */
  param?: string;
  /** sweep: values to try (at least two). */
  values?: number[];
  /** sweep: knee metric (default "p95_latency"). */
  metric?: string;
  /** sweep: knee improvement fraction (default 0.05). */
  threshold?: number;
}

// ---------------------------------------------------------------------------
// Provider presets (see `sim_core/presets.py`)
// ---------------------------------------------------------------------------

export interface Preset {
  name: string;
  role: string;
  max_capacity: number;
  service_time: number;
  hit_rate?: number | null;
  cost_per_hour?: number | null;
}

// ---------------------------------------------------------------------------
// Architecture import (see `api/schemas.py::ImportResponse`,
// `sim_core/importers.py`)
// ---------------------------------------------------------------------------

export interface ImportNodeSummary {
  name: string;
  role: ComponentRole;
  max_capacity: number;
  service_time: number;
}

export interface ImportReport {
  format: "native_yaml" | "native_json" | "terraform_json";
  source: string | null;
  node_count: number;
  edge_count: number;
  nodes: ImportNodeSummary[];
  edges: TopologyEdge[];
  assumptions: string[];
  warnings: string[];
  unmapped_resources: string[];
}

export interface ImportResponse {
  format: string;
  /** A fully valid SimulationConfig — drop it straight into POST /simulate. */
  config: SimulationConfig;
  report: ImportReport;
}

export interface PresetListResponse {
  presets: Record<string, Preset>;
  count: number;
}
