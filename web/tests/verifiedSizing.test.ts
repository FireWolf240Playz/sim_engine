/**
 * Contract for 1.3h (`.agents/tasks/1.3h-verified-labels.md`): every sizing
 * word and size on screen is the verified answer (`verified_status`,
 * `verified_capacity`), the same thing "Fix it" will do. Utilization stays
 * as the evidence. Falls back to `status` / `recommended_capacity` only when
 * a summary has no verified fields.
 * Make these pass — do not edit them.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SizingTable } from "@/core/components/SizingTable";
import { DEMO_CONFIG } from "@/core/lib/demo";
import { sizingLabel } from "@/core/lib/format";
import { nodeViews } from "@/core/lib/topologyView";
import type { ComponentSizing } from "@/core/types";

const NODES = DEMO_CONFIG.topology.nodes;

/** db under db_failover, as measured: utilization says right-sized, the plan raises it 3 → 6. */
const DB: ComponentSizing = {
  status: "right_sized",
  mean_utilization: 0.68,
  p95_utilization: 1,
  p95_queue: 9,
  recommended_capacity: 3,
  verified_status: "undersized",
  verified_capacity: 6,
};

/** worker under db_failover: 29% mean reads oversized, but no cut is safe. */
const WORKER: ComponentSizing = {
  status: "oversized",
  mean_utilization: 0.29,
  p95_utilization: 0.59,
  p95_queue: 0,
  recommended_capacity: 4,
  verified_status: "right_sized",
  verified_capacity: 6,
};

const UNVERIFIED: ComponentSizing = {
  status: "oversized",
  mean_utilization: 0.07,
  p95_utilization: 0.2,
  p95_queue: 0,
  recommended_capacity: 7,
};

describe("sizingLabel", () => {
  it("is the verified answer when the summary carries one", () => {
    expect(sizingLabel(DB)).toEqual({ status: "undersized", capacity: 6 });
    expect(sizingLabel(WORKER)).toEqual({ status: "right_sized", capacity: 6 });
  });

  it("falls back to the utilization label without verified fields", () => {
    expect(sizingLabel(UNVERIFIED)).toEqual({ status: "oversized", capacity: 7 });
  });
});

describe("the diagram's node views", () => {
  const views = nodeViews(NODES, { db: DB, worker: WORKER, lb: UNVERIFIED }, [], false);
  const byName = new Map(views.map((v) => [v.name, v]));

  it("take status and size from the verified answer", () => {
    expect(byName.get("db")).toMatchObject({ status: "undersized", recommended: 6 });
    expect(byName.get("worker")).toMatchObject({ status: "right_sized", recommended: 6 });
  });

  it("keep utilization as the evidence", () => {
    expect(byName.get("worker")).toMatchObject({ meanUtil: 0.29, p95Util: 0.59 });
  });

  it("fall back per node", () => {
    expect(byName.get("lb")).toMatchObject({ status: "oversized", recommended: 7 });
  });
});

describe("SizingTable", () => {
  const html = renderToStaticMarkup(
    createElement(SizingTable, { nodes: NODES, sizing: { db: DB, worker: WORKER } }),
  );
  const row = (name: string) => {
    const start = html.indexOf(`>${name}</td>`);
    return html.slice(start, html.indexOf("</tr>", start));
  };

  it("prints the verified word and size, not the utilization guess", () => {
    expect(row("db")).toContain("undersized");
    expect(row("db")).toContain("×6");
    expect(row("worker")).toContain("right-sized");
    expect(row("worker")).not.toContain("oversized");
  });

  it("still prints mean utilization", () => {
    expect(row("worker")).toContain("29.0%");
  });
});
