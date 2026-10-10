/**
 * 1.4 — the node inspector's view model, nothing else.
 *
 * One node, one answer: its config, what the last run measured at its
 * CURRENT size (the verified label, 1.3h), its findings, and its pending
 * "Fix it" change. Per-node failures, retries and p95 contribution are not
 * in `summary()` (run-wide totals only), so they are not shown here — an
 * engine follow-up.
 *
 * Pure and dependency-free on purpose: no React, no DOM — Vitest runs it
 * in node.
 */
import { sizingLabel } from "./format";
import { pendingSuggestions } from "./suggestions";
import type {
  ComponentRole,
  Finding,
  SimulateResponse,
  SimulationConfig,
  SizingStatus,
  Suggestion,
} from "../types";

/** Same 24/7 projection "Fix it" uses — `rightsize.py::_HOURS_PER_MONTH`. */
const HOURS_PER_MONTH = 720;

/** What the last run measured at this node's current size. */
export interface NodeMeasurement {
  status: SizingStatus;
  recommended: number;
  meanUtil: number;
  p95Util: number;
  p95Queue: number;
  peakUtil: number | null;
  peakQueue: number | null;
}

/** One node, one answer: config, measurement, findings, pending fix. */
export interface NodeInspection {
  name: string;
  role: ComponentRole;
  capacity: number;
  serviceTime: number;
  costPerHour: number | null;
  monthlyCost: number | null;
  measured: NodeMeasurement | null;
  /** 1.3g: the node's size moved since the numbers were measured. */
  remeasure: "running" | "unmeasured" | null;
  findings: Finding[];
  fix: Suggestion | null;
}

export function inspectNode(
  name: string,
  config: SimulationConfig,
  result: SimulateResponse,
  stale: { names: readonly string[]; running: boolean } = { names: [], running: false },
): NodeInspection | null {
  const node = config.topology.nodes.find((n) => n.name === name);
  if (!node) return null;

  const remeasure = stale.names.includes(name)
    ? (stale.running ? "running" : "unmeasured")
    : null;

  // The price comes from the config, not the run — so it stays valid while
  // the new size is being re-measured, unlike everything measured.
  const costPerHour = node.cost_per_hour ?? null;
  const monthlyCost =
    costPerHour === null ? null : costPerHour * node.max_capacity * HOURS_PER_MONTH;

  let measured: NodeMeasurement | null = null;
  const info = result.summary.component_sizing[name];
  if (info && remeasure === null) {
    const label = sizingLabel(info);
    // Peaks are the run's actual maxima for this node — null when the
    // timeline has no tick for it (a node no request ever touched).
    let peakUtil: number | null = null;
    let peakQueue: number | null = null;
    for (const tick of result.timeseries ?? []) {
      const per = tick.per_component[name];
      if (!per) continue;
      peakUtil = peakUtil === null ? per.utilization : Math.max(peakUtil, per.utilization);
      peakQueue = peakQueue === null ? per.queue_length : Math.max(peakQueue, per.queue_length);
    }
    measured = {
      status: label.status,
      recommended: label.capacity,
      meanUtil: info.mean_utilization,
      p95Util: info.p95_utilization,
      p95Queue: info.p95_queue,
      peakUtil,
      peakQueue,
    };
  }

  const summary = result.summary;
  // While re-measured, the old size's findings would pass as current — none.
  const findings =
    remeasure === null ? summary.findings.filter((f) => f.node === name) : [];
  const fix =
    pendingSuggestions(config, summary.suggestions ?? []).find((s) => s.node === name) ?? null;

  return {
    name: node.name,
    role: node.role,
    capacity: node.max_capacity,
    serviceTime: node.service_time,
    costPerHour,
    monthlyCost,
    measured,
    remeasure,
    findings,
    fix,
  };
}
