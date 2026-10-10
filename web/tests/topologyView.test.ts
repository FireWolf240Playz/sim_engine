/**
 * Contract for 1.3e/1.3f (`.agents/tasks/1.3f-simplify-render.md`): the
 * topology re-renders only the node cards whose picture changed, using
 * plain `memo` and no hand-written comparators.
 *
 * Vitest runs in node with no DOM, so render counts cannot be observed
 * directly. What is pinned instead is everything the skip decision rests
 * on: each card's props are flat primitives (so React's default shallow
 * compare decides correctly), only the touched node's props change on a
 * downsize, and the components are `memo` with the default compare.
 * Make these pass — do not edit them.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";
import { EdgeView, NodeBody, TopologyDiagram } from "@/core/components/TopologyDiagram";
import { DEMO_CONFIG, playbookTargets } from "@/core/lib/demo";
import { applySuggestions } from "@/core/lib/suggestions";
import * as topologyView from "@/core/lib/topologyView";
import { nodeViews, type NodeViewModel, type TopologyDiagramProps } from "@/core/lib/topologyView";
import type { ComponentSizing, Suggestion, TopologyNode } from "@/core/types";

const NODES = DEMO_CONFIG.topology.nodes;
const EDGES = DEMO_CONFIG.topology.edges ?? [];

function sizingFor(
  status: ComponentSizing["status"],
  mean: number,
  recommended = 4,
): ComponentSizing {
  return {
    status,
    mean_utilization: mean,
    p95_utilization: Math.min(1, mean + 0.2),
    p95_queue: 0,
    recommended_capacity: recommended,
  };
}

/** A plausible `component_sizing` for the demo, as one response would carry it. */
const SIZING: Record<string, ComponentSizing> = {
  lb: sizingFor("oversized", 0.12, 2),
  worker: sizingFor("oversized", 0.32, 4),
  cache: sizingFor("right_sized", 0.55, 8),
  db: sizingFor("right_sized", 0.71, 3),
  pricing_api: sizingFor("right_sized", 0.6, 4),
};

const DOWNSIZE_WORKER: Suggestion = {
  node: "worker",
  param: "max_capacity",
  current: 6,
  proposed: 4,
  reason: "mean 32% at ×6",
  est_monthly_delta: -72,
};

function views(
  nodes: TopologyNode[] = NODES,
  sizing: Record<string, ComponentSizing> = SIZING,
  playbook: string | null = "db_failover",
): NodeViewModel[] {
  const { names, wholePath } = playbookTargets(playbook, nodes);
  return nodeViews(nodes, sizing, names, wholePath);
}

/**
 * What React's default `memo` compare does with flat props: a card
 * re-renders when any of its props differs by `Object.is`. Local to the
 * test on purpose; production code must not need a comparator.
 */
function rerendered(prev: NodeViewModel[], next: NodeViewModel[]): string[] {
  const before = new Map(prev.map((v) => [v.name, v]));
  return next
    .filter((v) => {
      const old = before.get(v.name);
      if (!old) return true;
      const keys = new Set([...Object.keys(old), ...Object.keys(v)]) as Set<keyof NodeViewModel>;
      return [...keys].some((k) => !Object.is(old[k], v[k]));
    })
    .map((v) => v.name);
}

function props(overrides: Partial<TopologyDiagramProps> = {}): TopologyDiagramProps {
  const { names, wholePath } = playbookTargets("db_failover", NODES);
  return { nodes: NODES, edges: EDGES, sizing: SIZING, targetNames: names, wholePath, ...overrides };
}

const REACT_MEMO = Symbol.for("react.memo");
function memoInfo(component: unknown): { $$typeof: symbol; compare: unknown } {
  return component as { $$typeof: symbol; compare: unknown };
}

describe("nodeViews", () => {
  it("returns one view per node, in node order", () => {
    expect(views().map((v) => v.name)).toEqual(NODES.map((n) => n.name));
  });

  // Flat primitives are what lets the default shallow compare decide
  // correctly. An object or array field would compare by identity.
  it("holds primitives only", () => {
    for (const view of views()) {
      for (const value of Object.values(view)) {
        expect(
          value === null || ["string", "number", "boolean"].includes(typeof value),
        ).toBe(true);
      }
    }
  });

  it("carries what the card draws", () => {
    const worker = views().find((v) => v.name === "worker")!;
    expect(worker).toMatchObject({
      name: "worker",
      role: "worker",
      capacity: 6,
      status: "oversized",
      meanUtil: 0.32,
      recommended: 4,
      isTarget: false,
      inPath: false,
    });
    expect(views().find((v) => v.name === "db")!.isTarget).toBe(true);
  });

  it("marks a node with no sizing yet as unmeasured, not as zero", () => {
    const view = nodeViews(NODES, {}, [], false).find((v) => v.name === "db")!;
    expect(view.status).toBeNull();
    expect(view.meanUtil).toBeNull();
  });

  // Hover is per-pointer. As a card prop, pointing at one node would
  // change every other card's props and re-render them all.
  it("leaves hover and focus state out of the view", () => {
    for (const view of views()) {
      expect(Object.keys(view)).not.toContain("dimmed");
      expect(Object.keys(view)).not.toContain("isActive");
    }
  });
});

describe("which cards a change re-renders (default memo semantics)", () => {
  it("none when a re-run returns the same numbers in fresh objects", () => {
    expect(rerendered(views(), views(NODES, structuredClone(SIZING)))).toEqual([]);
  });

  // The bug: downsizing one node redrew the whole diagram.
  it("only the node a downsize touched", () => {
    const next = applySuggestions(DEMO_CONFIG, [DOWNSIZE_WORKER]).topology.nodes;
    expect(rerendered(views(), views(next))).toEqual(["worker"]);
  });

  it("only the nodes whose measured sizing changed", () => {
    const sizing = { ...structuredClone(SIZING), db: sizingFor("undersized", 0.93, 6) };
    expect(rerendered(views(), views(NODES, sizing))).toEqual(["db"]);
  });

  it("the old and new targets when the incident changes", () => {
    expect(rerendered(views(), views(NODES, SIZING, "cache_eviction_storm")).sort()).toEqual([
      "cache",
      "db",
    ]);
  });
});

describe("TopologyDiagram wiring", () => {
  // No hand-written comparators: a comparator that forgets a field ships
  // a card that silently never updates.
  it("wraps the diagram and each node body in plain memo", () => {
    for (const component of [TopologyDiagram, NodeBody]) {
      expect(memoInfo(component).$$typeof).toBe(REACT_MEMO);
      expect(memoInfo(component).compare).toBeNull();
    }
  });

  // memo only where a measurement justifies it: an edge is one <path>,
  // cheaper to redraw than to compare.
  it("leaves EdgeView a plain function component", () => {
    expect(typeof EdgeView).toBe("function");
  });

  it("keeps topologyView down to the view model", () => {
    const runtimeExports = Object.entries(topologyView)
      .filter(([, value]) => typeof value === "function")
      .map(([name]) => name);
    expect(runtimeExports).toEqual(["nodeViews"]);
  });
});

// Regression guard: the refactor must draw the same diagram.
describe("TopologyDiagram markup", () => {
  let html = "";
  beforeEach(() => {
    html = renderToStaticMarkup(createElement(TopologyDiagram, props()));
  });

  it("draws one focusable card per node", () => {
    expect(html.match(/role="group"/g) ?? []).toHaveLength(NODES.length);
  });

  it("draws one flow line per edge", () => {
    expect(html.match(/eleven-edge-flow/g) ?? []).toHaveLength(EDGES.length);
  });

  it("puts the target ring on the incident's target only", () => {
    expect(html.match(/eleven-target-ring/g) ?? []).toHaveLength(1);
  });

  it("still prints each node's sizing word, utilization and capacity", () => {
    expect(html).toContain("OVERSIZED");
    expect(html).toContain("RIGHT-SIZED");
    expect(html).toContain("32%");
    expect(html).toContain("×6 capacity");
  });

  // Positions are cached across a resize; the node data must not be.
  it("shows the new capacity after a downsize", () => {
    const next = applySuggestions(DEMO_CONFIG, [DOWNSIZE_WORKER]).topology.nodes;
    const resized = renderToStaticMarkup(createElement(TopologyDiagram, props({ nodes: next })));
    expect(resized).toContain("worker — worker, ×4 capacity");
  });
});
