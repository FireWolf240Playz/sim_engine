/**
 * Contract for 1.3g (`.agents/tasks/1.3g-rerun-feedback.md`): while an
 * "Apply & re-run" is in flight, only the nodes that changed say so, and
 * the Fix-it panel tells the truth about what it is waiting for.
 * Make these pass — do not edit them.
 */
import { describe, expect, it } from "vitest";
import { DEMO_CONFIG } from "@/core/lib/demo";
import { fixPanelView, staleNodes } from "@/core/lib/rerun";
import { applySuggestions } from "@/core/lib/suggestions";
import { nodeViews } from "@/core/lib/topologyView";
import type { Suggestion } from "@/core/types";

const NODES = DEMO_CONFIG.topology.nodes;

function resize(node: string, current: number, proposed: number): Suggestion {
  return {
    node,
    param: "max_capacity",
    current,
    proposed,
    reason: "test",
    est_monthly_delta: null,
  };
}

const AFTER_APPLY = applySuggestions(DEMO_CONFIG, [
  resize("worker", 6, 4),
  resize("db", 3, 6),
]).topology.nodes;

describe("staleNodes", () => {
  it("is empty before any run has been measured", () => {
    expect(staleNodes(null, NODES)).toEqual([]);
  });

  it("is empty when the config is the one that was measured", () => {
    expect(staleNodes(NODES, NODES)).toEqual([]);
    expect(staleNodes(NODES, structuredClone(NODES))).toEqual([]);
  });

  it("names exactly the nodes whose capacity differs from the measured run", () => {
    expect(staleNodes(NODES, AFTER_APPLY)).toEqual(["worker", "db"]);
  });

  it("names a node the measured run never saw", () => {
    const added = [...NODES, { name: "queue", role: "generic" as const, max_capacity: 2, service_time: 1 }];
    expect(staleNodes(NODES, added)).toEqual(["queue"]);
  });
});

describe("nodeViews re-measure state", () => {
  it("defaults to no re-measure state, so existing callers are unchanged", () => {
    for (const view of nodeViews(NODES, {}, [], false)) expect(view.remeasure).toBeNull();
  });

  it("marks only the stale nodes as running while the re-run is in flight", () => {
    const views = nodeViews(AFTER_APPLY, {}, [], false, {
      names: ["worker", "db"],
      running: true,
    });
    const byName = Object.fromEntries(views.map((v) => [v.name, v.remeasure]));
    expect(byName).toEqual({
      lb: null,
      worker: "running",
      cache: null,
      db: "running",
      pricing_api: null,
    });
  });

  // A failed re-run leaves the config patched but unmeasured: the old
  // utilization belongs to the old size and must not pass as current.
  it("marks stale nodes as unmeasured once nothing is running", () => {
    const views = nodeViews(AFTER_APPLY, {}, [], false, { names: ["db"], running: false });
    expect(views.find((v) => v.name === "db")!.remeasure).toBe("unmeasured");
    expect(views.find((v) => v.name === "worker")!.remeasure).toBeNull();
  });
});

describe("fixPanelView", () => {
  it("shows the diff and the verified outcome while suggestions are pending", () => {
    expect(
      fixPanelView({ pending: 2, suggestions: 2, hasOutcome: true, isPending: false }),
    ).toEqual({ diff: true, outcome: true, note: null });
  });

  // The bug: right after Apply this read "Nothing to change: every node is
  // the right size" while the re-run that decides that was still running.
  it("says the re-run is in flight once every suggestion is applied", () => {
    expect(
      fixPanelView({ pending: 0, suggestions: 2, hasOutcome: true, isPending: true }),
    ).toEqual({ diff: false, outcome: false, note: "rerunning" });
  });

  it("says applied-but-unmeasured when the re-run is not running (it failed)", () => {
    expect(
      fixPanelView({ pending: 0, suggestions: 2, hasOutcome: true, isPending: false }),
    ).toEqual({ diff: false, outcome: false, note: "applied" });
  });

  it("says nothing to change only when the run proposed nothing", () => {
    expect(
      fixPanelView({ pending: 0, suggestions: 0, hasOutcome: false, isPending: false }),
    ).toEqual({ diff: false, outcome: false, note: "nothing" });
  });

  // e.g. dependency_timeout_cascade: no size fixes it, the outcome says why.
  it("shows the outcome alone when nothing is proposed but findings remain", () => {
    expect(
      fixPanelView({ pending: 0, suggestions: 0, hasOutcome: true, isPending: false }),
    ).toEqual({ diff: false, outcome: true, note: null });
  });
});
