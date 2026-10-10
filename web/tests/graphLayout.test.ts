import { beforeEach, describe, expect, it } from "vitest";
import {
  GAP_X,
  NODE_H,
  NODE_W,
  PAD,
  assignColumns,
  canvasSize,
  computeEdges,
  edgeKey,
  edgePath,
  layout,
  layoutKey,
} from "@/core/lib/graphLayout";
import { DEMO_CONFIG } from "@/core/lib/demo";
import type { TopologyEdge, TopologyNode } from "@/core/types";

function node(name: string, role: TopologyNode["role"] = "generic"): TopologyNode {
  return { name, role, max_capacity: 4, service_time: 1 };
}

function edge(source: string, target: string, probability = 1): TopologyEdge {
  return { source, target, probability };
}

const DEMO_NODES = DEMO_CONFIG.topology.nodes;
const DEMO_EDGES = DEMO_CONFIG.topology.edges ?? [];

describe("assignColumns", () => {
  it("lays the demo topology out by request flow", () => {
    const col = assignColumns(DEMO_NODES, DEMO_EDGES);
    expect(col.get("lb")).toBe(0);
    expect(col.get("worker")).toBe(1);
    expect(col.get("cache")).toBe(2);
    expect(col.get("pricing_api")).toBe(2);
    expect(col.get("db")).toBe(3);
  });

  // The reason for longest-path over BFS depth: with BFS, `d` would be
  // discovered from `a` at column 1 and end up LEFT of `c`, drawing a
  // backwards edge across two columns.
  it("places a node past its deepest source, not its first", () => {
    const nodes = [node("a"), node("b"), node("c"), node("d")];
    const edges = [edge("a", "b"), edge("b", "c"), edge("a", "d"), edge("c", "d")];
    const col = assignColumns(nodes, edges);
    expect(col.get("d")).toBe(3);
    expect(col.get("d")!).toBeGreaterThan(col.get("c")!);
  });

  it("never places a node left of any of its sources", () => {
    const col = assignColumns(DEMO_NODES, DEMO_EDGES);
    for (const e of DEMO_EDGES) {
      expect(col.get(e.target)!).toBeGreaterThan(col.get(e.source)!);
    }
  });

  it("ignores self-loops and edges naming unknown nodes", () => {
    const nodes = [node("a"), node("b")];
    const col = assignColumns(nodes, [
      edge("a", "a"),
      edge("a", "ghost"),
      edge("ghost", "b"),
      edge("a", "b"),
    ]);
    expect(col.get("a")).toBe(0);
    expect(col.get("b")).toBe(1);
  });

  it("still places every node when the graph has a cycle", () => {
    // Topology construction rejects cycles, but the layout must not hang
    // or drop nodes if a malformed graph ever reaches it.
    const nodes = [node("a"), node("b"), node("c")];
    const col = assignColumns(nodes, [edge("a", "b"), edge("b", "c"), edge("c", "a")]);
    expect([...col.keys()].sort()).toEqual(["a", "b", "c"]);
    for (const value of col.values()) expect(Number.isFinite(value)).toBe(true);
  });

  it("is deterministic for the same input", () => {
    const a = assignColumns(DEMO_NODES, DEMO_EDGES);
    const b = assignColumns(DEMO_NODES, DEMO_EDGES);
    expect([...a.entries()]).toEqual([...b.entries()]);
  });
});

describe("layout", () => {
  it("positions every node exactly once", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    expect(positioned).toHaveLength(DEMO_NODES.length);
    expect(new Set(positioned.map((p) => p.node.name)).size).toBe(DEMO_NODES.length);
  });

  it("spaces columns by node width plus the gap", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const byName = new Map(positioned.map((p) => [p.node.name, p]));
    expect(byName.get("lb")!.x).toBe(PAD);
    expect(byName.get("worker")!.x).toBe(PAD + (NODE_W + GAP_X));
    expect(byName.get("db")!.x).toBe(PAD + 3 * (NODE_W + GAP_X));
  });

  it("gives nodes sharing a column distinct, non-overlapping rows", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const sameCol = positioned.filter((p) => p.col === 2).sort((a, b) => a.y - b.y);
    expect(sameCol).toHaveLength(2);
    expect(sameCol[1].y - sameCol[0].y).toBeGreaterThanOrEqual(NODE_H);
  });

  it("centres a short column against the tallest one", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const byName = new Map(positioned.map((p) => [p.node.name, p]));
    const fanOut = positioned.filter((p) => p.col === 2);
    const mid = (Math.min(...fanOut.map((p) => p.y)) + Math.max(...fanOut.map((p) => p.y))) / 2;
    expect(byName.get("lb")!.y).toBeCloseTo(mid, 5);
  });

  it("handles a single node with no edges", () => {
    const positioned = layout([node("solo")], []);
    expect(positioned).toEqual([
      { node: node("solo"), x: PAD, y: PAD, col: 0 },
    ]);
  });

  it("handles an empty topology", () => {
    expect(layout([], [])).toEqual([]);
    expect(canvasSize([])).toEqual({ width: 0, height: 0 });
  });
});

describe("canvasSize", () => {
  it("covers every positioned node plus the padding", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const { width, height } = canvasSize(positioned);
    for (const p of positioned) {
      expect(p.x + NODE_W + PAD).toBeLessThanOrEqual(width);
      expect(p.y + NODE_H + PAD).toBeLessThanOrEqual(height);
    }
  });
});

describe("computeEdges", () => {
  it("produces one geometry per valid edge", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const geoms = computeEdges(positioned, DEMO_EDGES);
    expect(geoms.map((g) => g.key).sort()).toEqual(DEMO_EDGES.map(edgeKey).sort());
  });

  it("leaves the source's right edge and arrives at the target's left", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const byName = new Map(positioned.map((p) => [p.node.name, p]));
    for (const g of computeEdges(positioned, DEMO_EDGES)) {
      const [source, target] = g.key.split("|");
      expect(g.x1).toBe(byName.get(source)!.x + NODE_W);
      expect(g.x2).toBe(byName.get(target)!.x);
    }
  });

  // A fan-out must leave as a fan: one attachment slot per edge, not a
  // bundle at a single point on the node border.
  it("spreads a fan-out across distinct attachment slots", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const fromWorker = computeEdges(positioned, DEMO_EDGES).filter((g) =>
      g.key.startsWith("worker|"),
    );
    expect(fromWorker).toHaveLength(2);
    expect(fromWorker[0].y1).not.toBe(fromWorker[1].y1);
  });

  it("keeps every attachment inside the node card", () => {
    const positioned = layout(DEMO_NODES, DEMO_EDGES);
    const byName = new Map(positioned.map((p) => [p.node.name, p]));
    for (const g of computeEdges(positioned, DEMO_EDGES)) {
      const [source, target] = g.key.split("|");
      expect(g.y1).toBeGreaterThan(byName.get(source)!.y);
      expect(g.y1).toBeLessThan(byName.get(source)!.y + NODE_H);
      expect(g.y2).toBeGreaterThan(byName.get(target)!.y);
      expect(g.y2).toBeLessThan(byName.get(target)!.y + NODE_H);
    }
  });

  it("skips edges whose endpoints are not on the canvas", () => {
    const positioned = layout([node("a"), node("b")], [edge("a", "b")]);
    expect(computeEdges(positioned, [edge("a", "ghost")])).toEqual([]);
  });
});

describe("edgePath", () => {
  it("emits a cubic curve between the two attachment points", () => {
    const d = edgePath({ key: "a|b", x1: 10, y1: 20, x2: 110, y2: 80 });
    expect(d.startsWith("M 10 20 C ")).toBe(true);
    expect(d.endsWith("110 80")).toBe(true);
  });

  it("keeps a minimum handle length so a short hop still curves", () => {
    const d = edgePath({ key: "a|b", x1: 0, y1: 0, x2: 2, y2: 40 });
    expect(d).toContain("C 24 0");
  });
});

// 1.3e: the diagram caches its geometry under this key, so it must change
// exactly when `layout` / `computeEdges` could return something different.
describe("layoutKey", () => {
  let key = "";
  beforeEach(() => {
    key = layoutKey(DEMO_NODES, DEMO_EDGES);
  });

  it("ignores everything the layout does not read", () => {
    const resized = DEMO_NODES.map((n) => ({
      ...n,
      max_capacity: n.max_capacity + 3,
      service_time: n.service_time * 2,
      role: "generic" as const,
    }));
    const reweighted = DEMO_EDGES.map((e) => ({ ...e, probability: 0.25 }));
    expect(layoutKey(resized, reweighted)).toBe(key);
  });

  it("ignores edges the layout drops", () => {
    expect(layoutKey(DEMO_NODES, [...DEMO_EDGES, edge("db", "db"), edge("db", "ghost")])).toBe(
      key,
    );
  });

  it("changes when a node is added, removed or renamed", () => {
    expect(layoutKey([...DEMO_NODES, node("queue")], DEMO_EDGES)).not.toBe(key);
    expect(layoutKey(DEMO_NODES.slice(1), DEMO_EDGES)).not.toBe(key);
    const renamed = DEMO_NODES.map((n) => (n.name === "db" ? { ...n, name: "db2" } : n));
    expect(layoutKey(renamed, DEMO_EDGES)).not.toBe(key);
  });

  it("changes when an edge is added or removed", () => {
    expect(layoutKey(DEMO_NODES, DEMO_EDGES.slice(1))).not.toBe(key);
    expect(layoutKey(DEMO_NODES, [...DEMO_EDGES, edge("lb", "db")])).not.toBe(key);
  });

  // Node order seeds the barycenter sweep and the cycle fallback.
  it("changes when the node order changes", () => {
    expect(layoutKey([...DEMO_NODES].reverse(), DEMO_EDGES)).not.toBe(key);
  });

  it("does not collide on names that contain the separator", () => {
    const a = layoutKey([node("a|b"), node("c")], []);
    const b = layoutKey([node("a"), node("b|c")], []);
    expect(a).not.toBe(b);
  });
});
