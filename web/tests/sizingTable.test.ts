/**
 * 1.3g follow-up: while a resized node is re-measured (or its re-run
 * failed), the Sizing table shows none of its old-size numbers as current,
 * the same as its topology card. Found by the 1.3g by-eye check: the table
 * printed the new ×6 next to the old run's "right-sized, rec ×13".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SizingTable } from "@/core/components/SizingTable";
import { DEMO_CONFIG } from "@/core/lib/demo";
import type { ComponentSizing } from "@/core/types";

const NODES = DEMO_CONFIG.topology.nodes;

const SIZING: Record<string, ComponentSizing> = {
  db: {
    status: "right_sized",
    mean_utilization: 0.683,
    p95_utilization: 1,
    p95_queue: 9,
    recommended_capacity: 13,
  },
  worker: {
    status: "oversized",
    mean_utilization: 0.289,
    p95_utilization: 0.59,
    p95_queue: 0,
    recommended_capacity: 4,
  },
};

function row(stale?: { names: string[]; running: boolean }, name = "db"): string {
  const html = renderToStaticMarkup(createElement(SizingTable, { nodes: NODES, sizing: SIZING, stale }));
  const start = html.indexOf(`>${name}</td>`);
  return html.slice(start, html.indexOf("</tr>", start));
}

describe("SizingTable while a resize is re-measured", () => {
  it("hides the old size's numbers and says it is measuring", () => {
    const db = row({ names: ["db"], running: true });
    expect(db).toContain("measuring…");
    expect(db).not.toContain("68.3%");
    expect(db).not.toContain("×13");
    expect(db).not.toContain("right-sized");
  });

  it("says not measured when the re-run failed", () => {
    expect(row({ names: ["db"], running: false })).toContain("not measured");
  });

  it("still shows the node's current size", () => {
    expect(row({ names: ["db"], running: true })).toContain(`×${NODES.find((n) => n.name === "db")?.max_capacity}`);
  });

  it("leaves unchanged nodes alone", () => {
    const worker = row({ names: ["db"], running: true }, "worker");
    expect(worker).toContain("28.9%");
    expect(worker).toContain("oversized");
  });

  it("is unchanged with no stale nodes", () => {
    expect(row()).toContain("68.3%");
    expect(row()).toContain("right-sized");
  });
});
