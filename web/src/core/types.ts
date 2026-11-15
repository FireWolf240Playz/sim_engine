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

/** 3.5: how the base rate bends over the traffic window (`TrafficPattern.shape`). */
export type TrafficShape = "flat" | "ramp" | "diurnal" | "black_friday";

export interface TrafficConfig {
  base_rps: number;
  duration: number;
  /** When traffic begins, in seconds (engine default 0). */
  start_time?: number;
  spike_rps?: number | null;
  spike_start?: number | null;
  spike_duration?: number | null;
  /** 3.5: absent means flat. `base_rps` is always the quiet rate. */
  shape?: TrafficShape;
  /** 3.5: how many times busier the peak is; absent = the shape's default.
   * The engine refuses it on a flat pattern. */
  shape_peak?: number | null;
}

/** Directed, probabilistic request-path edge (see `sim_core.config.Edge`). */
export interface TopologyEdge {
  source: string;
  target: string;
  probability?: number;
  /**
   * Edges out of one node with the same group are alternatives: a request
   * takes at most one of them (a load balancer picking a backend). Without
   * a group the edge is followed independently.
   */
  group?: string | null;
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
  /** Id of the finding that capped `band` below the score's own band (crit caps
   * at "At risk", warn at "Solid"), or null when the score set it. */
  band_capped_by?: string | null;
  terms: ScoreTerm[];
}

export type SizingStatus = "right_sized" | "oversized" | "undersized";

export interface ComponentSizing {
  status: SizingStatus;
  mean_utilization: number;
  p95_utilization: number;
  p95_queue: number;
  recommended_capacity: number;
  /** Counted, not sampled: slot acquisitions that had to queue, and the most
   * slots held after a grant that did not. Optional: older payloads lack them. */
  waited_requests?: number;
  peak_in_use?: number;
  /** Verified answer (1.3h): what "Fix it" will actually do to this node.
   * Absent on `suggest=False` runs. */
  verified_status?: SizingStatus;
  verified_capacity?: number;
}

export interface CostExtrapolation {
  hour: number;
  month: number;
  year: number;
}

/**
 * One right-sizing proposal (roadmap 1.3) — mirrors
 * `sim_core/suggestions.py::Suggestion`. Deterministic: same input,
 * same list, always.
 */
export interface Suggestion {
  node: string;
  /** The config field to patch (v1: always `"max_capacity"`). */
  param: string;
  current: number;
  proposed: number;
  reason: string;
  /**
   * Estimated monthly $ for the provisioned slots only
   * (`cost_per_hour * (proposed - current) * 720`); `null` when the node
   * carries no rate (nothing to project).
   */
  est_monthly_delta: number | null;
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
  /**
   * Requests still in the system when the run stopped, and the part of them
   * already past the SLA budget. The overdue ones are in `requests` as
   * failures, so arrivals = `requests + unfinished_requests -
   * overdue_requests`. Optional because older payloads predate the keys.
   */
  unfinished_requests?: number;
  overdue_requests?: number;
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
  /**
   * Deterministic "fix it" list (roadmap 1.3): the capacity changes that
   * would right-size this run. Attached in `CloudSimulator.run()`, so it
   * rides along on every single- and multi-seed summary. Optional because
   * pre-1.3 payloads (e.g. stored JSON) predate the key.
   */
  suggestions?: Suggestion[];
  /** What applying every suggestion does — the verification run itself
   * (`sim_core/rightsize.py::FixOutcome`). Null when nothing to change. */
  fix_outcome?: FixOutcome | null;
}

/** A finding reduced to what a before → after comparison needs. */
export interface FindingRef {
  id: string;
  node: string | null;
  severity: string;
  title: string;
}

/** Mirrors `sim_core/rightsize.py::FixOutcome`. */
export interface FixOutcome {
  seed: number;
  /** 1.3i: every seed the plan was verified on (one, or the multi-seed run's). */
  seeds?: number[];
  score_before: number | null;
  score_after: number | null;
  band_before: string | null;
  band_after: string | null;
  /** crit/warn findings the suggestions clear. */
  resolved: FindingRef[];
  /** crit/warn findings no capacity change clears — the cause is elsewhere. */
  unfixed: FindingRef[];
  monthly_delta: number | null;
}

/** One node inside a tick. The last three are sent by every engine since 3.1. */
export interface TickNode {
  utilization: number;
  queue_length: number;
  /** Live slots right now: lower than the configured size during an outage. */
  capacity?: number;
  in_use?: number;
  /** Timeout retries on this node in the tick's window. */
  retries?: number;
}

/**
 * One `timeseries()` tick (see `sim_core/metrics.py::timeseries`). The same
 * object is a `tick` event of the run stream (3.1): the stream and the
 * finished run cannot disagree. The counts are for the window that ends at
 * `time`; they are optional here only because older fixtures lack them.
 */
export interface TimelineTick {
  time: number;
  p50_latency: number | null;
  p95_latency: number | null;
  sla_met: number | null;
  arrived?: number;
  completed?: number;
  failed?: number;
  per_component: Record<string, TickNode>;
  /** Requests that set off along `config.topology.edges[i]` in the window. */
  edge_flow?: number[];
}

/* ------------------------------------------------------------------ */
/* 3.1 — the run stream (`POST /runs`, `GET /runs/{id}/events`)        */
/* ------------------------------------------------------------------ */

/** Body of `POST /runs`; the same fields `/simulate` takes. */
export interface RunRequest {
  config: SimulationConfig;
  playbook: string | null;
  n_seeds?: number;
  /** 3.3: failures the user injected by clicking nodes. */
  custom_chaos?: ChaosEventCfg[];
}

export interface RunStartedResponse {
  run_id: string;
  events_url: string;
}

/** `started`: what is running. `seeds` is null for a single run. */
export interface RunStarted {
  run_id: string;
  seed: number;
  seeds: number[] | null;
  duration: number;
  metrics_interval: number;
  /** The effective schedule: config + playbook + custom chaos. */
  chaos: ChaosEventCfg[];
}

/** `chaos`: one window opening or closing, as the engine did it. */
export interface ChaosMark {
  t: number;
  phase: "start" | "end";
  event_type: ChaosEventType;
  /** The node hit; null for a latency spike (every node). */
  node: string | null;
  intensity: number;
}

export type FeedKind =
  | "incident_start"
  | "incident_end"
  | "saturated"
  | "recovered"
  | "retries"
  | "failures";

/** `feed`: one line of the event feed. `text` is the engine's, printed as is. */
export interface FeedLine {
  t: number;
  kind: FeedKind;
  severity: FindingSeverity;
  node: string | null;
  text: string;
}

/** `seed`: one seed of a multi-seed run has been measured. */
export interface SeedProgress {
  seed: number;
  index: number;
  of: number;
  resilience_score: number;
}

/** `measured`: the summary before the Fix-it plan is verified. */
export interface RunMeasured {
  summary: Summary;
  typical_seed: number | null;
}

/** `GET /runs/{id}`. */
export interface RunStatusResponse {
  run_id: string;
  status: "running" | "done" | "failed";
  result: SimulateResponse | null;
  error: string | null;
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
  /** Multi-seed only: the seed the headline `summary` and `timeseries` came from. */
  typical_seed?: number | null;
  /** Multi-seed + timeline only: P95 range across every seed, per tick. */
  timeseries_band?: TimelineBandTick[] | null;
}

/** One `timeseries_band()` entry (see `sim_core/profile.py`). */
export interface TimelineBandTick {
  time: number;
  p95_min: number | null;
  p95_max: number | null;
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

/** A downloadable format of one run (`sim_core/exports.py::EXPORT_FORMATS`). */
export type ExportFormatName = "csv" | "json" | "xlsx";

/** Which plan a format belongs to. A label today; the API enforces it with auth (roadmap 2.2). */
export type ExportTier = "free" | "paid";

/** One entry of `GET /export/formats`. */
export interface ExportFormatInfo {
  name: ExportFormatName;
  label: string;
  extension: string;
  media_type: string;
  tier: ExportTier;
}

export interface ExportFormatsResponse {
  formats: ExportFormatInfo[];
  count: number;
}

/**
 * The run a result was measured from. The API keeps no run, so an export
 * names it by what produced it and the server simulates it again; this is
 * the config and playbook *of the result on screen*, not whatever the
 * controls were changed to afterwards.
 */
export interface MeasuredRun {
  config: SimulationConfig;
  playbook: string | null;
  /** 3.3: the injected failures that run carried (absent = none). */
  customChaos?: ChaosEventCfg[];
}

/** Body of `POST /export` (see `api/schemas.py::ExportRequest`). */
export interface ExportRequest {
  config: SimulationConfig;
  playbook: string | null;
  format: ExportFormatName;
  /** 3.3: sent only when the run on screen carried injected failures. */
  custom_chaos?: ChaosEventCfg[];
}
