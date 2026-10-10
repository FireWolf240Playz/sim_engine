/**
 * Contract for 1.4 (`.agents/tasks/1.4-node-inspector.md`): click, Enter or
 * Space on a topology node opens an inspector with that node's config, what
 * the last run measured (the verified label, 1.3h), its findings and its
 * "Fix it" change. Per-node failures, retries and p95 contribution are not
 * in `summary()`, so they are not shown.
 * Make these pass — do not edit them.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TopologyDiagram } from "@/core/components/TopologyDiagram";
import { DEMO_CONFIG } from "@/core/lib/demo";
import { inspectNode } from "@/core/lib/nodeInspector";
import type { SimulateResponse, Summary } from "@/core/types";

const CONFIG = DEMO_CONFIG;
const NODES = CONFIG.topology.nodes;
const DB = NODES.find((n) => n.name === "db");
if (!DB) throw new Error("demo has no db");

/** db under db_failover, as measured: utilization says right-sized, the plan raises it. */
const SUMMARY = {
  component_sizing: {
    db: {
      status: "right_sized",
      mean_utilization: 0.683,
      p95_utilization: 1,
      p95_queue: 9,
      recommended_capacity: 13,
      verified_status: "undersized",
      verified_capacity: DB.max_capacity * 2,
    },
    worker: {
      status: "oversized",
      mean_utilization: 0.289,
      p95_utilization: 0.59,
      p95_queue: 0,
      recommended_capacity: 4,
      verified_status: "right_sized",
      verified_capacity: 6,
    },
  },
  cost_breakdown_by_component: { db: 0.18, worker: 0.3 },
  findings: [
    { id: "sla_breach", severity: "crit", text: "SLA breached", node: null },
    { id: "undersized", severity: "warn", text: "db is undersized", node: "db" },
  ],
  suggestions: [
    {
      node: "db",
      param: "max_capacity",
      current: DB.max_capacity,
      proposed: DB.max_capacity * 2,
      reason: "clears sla_breach",
      est_monthly_delta: 130,
    },
  ],
} as unknown as Summary;

const RESULT = {
  summary: SUMMARY,
  report_png_b64: null,
  chaos: [],
  timeseries: [
    { time: 2, p50_latency: 1, p95_latency: 2, sla_met: 1, per_component: { db: { utilization: 0.4, queue_length: 1 } } },
    { time: 4, p50_latency: 1, p95_latency: 9, sla_met: 0, per_component: { db: { utilization: 1, queue_length: 22 } } },
  ],
} as SimulateResponse;

describe("inspectNode", () => {
  const db = inspectNode("db", CONFIG, RESULT);

  it("is null for a node that is not in the config", () => {
    expect(inspectNode("nope", CONFIG, RESULT)).toBeNull();
  });

  it("carries the node's config", () => {
    expect(db).toMatchObject({
      name: "db",
      role: DB.role,
      capacity: DB.max_capacity,
      serviceTime: DB.service_time,
      costPerHour: DB.cost_per_hour ?? null,
    });
  });

  it("prices the node per month the way Fix it does (rate × capacity × 720)", () => {
    const rate = DB.cost_per_hour ?? 0;
    expect(db?.monthlyCost).toBeCloseTo(rate * DB.max_capacity * 720);
    const unpriced = { ...CONFIG, topology: { ...CONFIG.topology, nodes: NODES.map((n) => ({ ...n, cost_per_hour: null })) } };
    expect(inspectNode("db", unpriced, RESULT)?.monthlyCost).toBeNull();
  });

  it("keeps the monthly price while re-measured: it comes from the config", () => {
    expect(inspectNode("db", CONFIG, RESULT, { names: ["db"], running: true })?.monthlyCost).toBe(db?.monthlyCost);
  });

  it("uses the verified label and size, with utilization as evidence", () => {
    expect(db?.measured).toMatchObject({
      status: "undersized",
      recommended: DB.max_capacity * 2,
      meanUtil: 0.683,
      p95Util: 1,
      p95Queue: 9,
    });
  });

  it("takes the peaks from the timeline, null without one", () => {
    expect(db?.measured).toMatchObject({ peakUtil: 1, peakQueue: 22 });
    const noTimeline = inspectNode("db", CONFIG, { ...RESULT, timeseries: null });
    expect(noTimeline?.measured).toMatchObject({ peakUtil: null, peakQueue: null });
  });

  it("keeps only this node's findings", () => {
    expect(db?.findings.map((f) => f.id)).toEqual(["undersized"]);
  });

  it("offers this node's pending fix, and none once applied", () => {
    expect(db?.fix?.proposed).toBe(DB.max_capacity * 2);
    expect(inspectNode("worker", CONFIG, RESULT)?.fix).toBeNull();
    const applied = {
      ...CONFIG,
      topology: {
        ...CONFIG.topology,
        nodes: NODES.map((n) => (n.name === "db" ? { ...n, max_capacity: n.max_capacity * 2 } : n)),
      },
    };
    expect(inspectNode("db", applied, RESULT)?.fix).toBeNull();
  });

  it("shows no old-size numbers or findings while re-measured (1.3g)", () => {
    const running = inspectNode("db", CONFIG, RESULT, { names: ["db"], running: true });
    expect(running).toMatchObject({ measured: null, findings: [], remeasure: "running" });
    const failed = inspectNode("db", CONFIG, RESULT, { names: ["db"], running: false });
    expect(failed?.remeasure).toBe("unmeasured");
    expect(inspectNode("worker", CONFIG, RESULT, { names: ["db"], running: true })?.remeasure).toBeNull();
  });

  it("has no measurement for a node without sizing", () => {
    expect(inspectNode("lb", CONFIG, RESULT)?.measured).toBeNull();
  });
});

describe("TopologyDiagram with an inspector", () => {
  const base = {
    nodes: NODES,
    edges: CONFIG.topology.edges ?? [],
    sizing: SUMMARY.component_sizing,
    targetNames: ["db"],
    wholePath: false,
  };

  it("makes every node a button that opens a dialog", () => {
    const html = renderToStaticMarkup(createElement(TopologyDiagram, { ...base, onOpen: () => {} }));
    expect(html.match(/role="button"/g) ?? []).toHaveLength(NODES.length);
    expect(html.match(/aria-haspopup="dialog"/g) ?? []).toHaveLength(NODES.length);
  });

  it("stays a plain group without one", () => {
    const html = renderToStaticMarkup(createElement(TopologyDiagram, base));
    expect(html).not.toContain('role="button"');
    expect(html).not.toContain("aria-haspopup");
  });
});
