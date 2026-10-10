/**
 * Contract for 1.3f (`.agents/tasks/1.3f-simplify-render.md`): a new run
 * result reuses every part of the previous one that did not change, so
 * plain `memo` skips the panels and cards fed from those parts. This
 * replaces hand-written by-value comparators. Make these pass — do not
 * edit them.
 */
import { describe, expect, it } from "vitest";
import { shareResult } from "@/core/lib/runResult";
import type { SimulateResponse } from "@/core/types";

function response(): SimulateResponse {
  return {
    summary: {
      resilience_score: 81.2,
      component_sizing: {
        worker: { status: "oversized", mean_utilization: 0.32, p95_utilization: 0.5, p95_queue: 0, recommended_capacity: 4 },
        db: { status: "right_sized", mean_utilization: 0.71, p95_utilization: 0.9, p95_queue: 2, recommended_capacity: 3 },
      },
      score_explanation: {
        score: 81.2,
        clamped: false,
        band: "At risk",
        band_capped_by: "sla_breach",
        terms: [{ label: "SLA", points: 40.5, detail: "p95 under target 81% of the run" }],
      },
      findings: [
        { id: "sla_breach", severity: "crit", text: "SLA breached" },
        { id: "undersized", severity: "warn", text: "db is undersized", node: "db" },
      ],
    },
    timeseries: [{ time: 0, p50_latency: 0.1, p95_latency: 0.3 }],
  } as unknown as SimulateResponse;
}

describe("shareResult", () => {
  it("takes the first result as it is", () => {
    const first = response();
    expect(shareResult(null, first)).toBe(first);
  });

  // isPending flips and same-number re-runs must not invalidate anything.
  it("returns the previous result when the new one is equal", () => {
    const prev = response();
    expect(shareResult(prev, response())).toBe(prev);
  });

  it("keeps every unchanged part and replaces only what changed", () => {
    const prev = response();
    const next = response();
    next.summary.component_sizing.worker.mean_utilization = 0.48;
    const shared = shareResult(prev, next);

    expect(shared).not.toBe(prev);
    expect(shared.summary.component_sizing.worker).not.toBe(prev.summary.component_sizing.worker);
    expect(shared.summary.component_sizing.worker.mean_utilization).toBe(0.48);
    expect(shared.summary.component_sizing.db).toBe(prev.summary.component_sizing.db);
    expect(shared.summary.score_explanation).toBe(prev.summary.score_explanation);
    expect(shared.summary.findings).toBe(prev.summary.findings);
    expect(shared.timeseries).toBe(prev.timeseries);
  });

  it("never mutates the previous result", () => {
    const prev = response();
    const snapshot = structuredClone(prev);
    const next = response();
    next.summary.component_sizing.db.status = "undersized";
    shareResult(prev, next);
    expect(prev).toEqual(snapshot);
  });
});
