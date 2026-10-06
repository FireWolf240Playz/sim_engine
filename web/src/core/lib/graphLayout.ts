import type { TopologyEdge, TopologyNode } from "../types";

/**
 * Layered ("Sugiyama-style") layout for the request-path graph, split
 * out of the diagram component so it can be reasoned about and tested
 * as what it is: a pure function from nodes+edges to geometry.
 */

export const NODE_W = 160;
export const NODE_H = 64;
export const GAP_X = 56;
export const GAP_Y = 36;
export const PAD = 12;

export interface Positioned {
  node: TopologyNode;
  x: number;
  y: number;
  col: number;
}

export interface EdgeGeom {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** Canonical key for an edge; also the React key the diagram renders with. */
export function edgeKey(edge: Pick<TopologyEdge, "source" | "target">): string {
  return `${edge.source}|${edge.target}`;
}

/** Edges that actually connect two distinct, existing nodes. */
function validEdges(nodes: TopologyNode[], edges: TopologyEdge[]): TopologyEdge[] {
  const names = new Set(nodes.map((n) => n.name));
  return edges.filter(
    (e) => names.has(e.source) && names.has(e.target) && e.source !== e.target,
  );
}

interface Adjacency {
  /** target -> its sources */
  sources: Map<string, string[]>;
  /** source -> its targets */
  outgoing: Map<string, string[]>;
}

function adjacency(edges: TopologyEdge[]): Adjacency {
  const sources = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const e of edges) {
    sources.set(e.target, [...(sources.get(e.target) ?? []), e.source]);
    outgoing.set(e.source, [...(outgoing.get(e.source) ?? []), e.target]);
  }
  return { sources, outgoing };
}

/**
 * Column assignment, longest-path style: a node sits one column past its
 * DEEPEST source, so (almost) every edge spans exactly one column. A BFS
 * "first discovery" depth would let a node land left of one of its
 * sources, and those long multi-column diagonals were the main source of
 * choppy, crossing connections. Kahn's topological order keeps it
 * deterministic; nodes stuck in a cycle fall back to their original
 * order so a malformed graph still lays out instead of throwing.
 */
export function assignColumns(
  nodes: TopologyNode[],
  edges: TopologyEdge[],
): Map<string, number> {
  const valid = validEdges(nodes, edges);
  const { sources, outgoing } = adjacency(valid);

  const indegree = new Map<string, number>();
  nodes.forEach((n) => indegree.set(n.name, 0));
  for (const e of valid) {
    indegree.set(e.target, (indegree.get(e.target) ?? 0) + 1);
  }

  const topo: string[] = [];
  const placed = new Set<string>();
  const queue = nodes.map((n) => n.name).filter((n) => (indegree.get(n) ?? 0) === 0);
  while (queue.length) {
    const name = queue.shift()!;
    topo.push(name);
    placed.add(name);
    for (const t of outgoing.get(name) ?? []) {
      const rest = (indegree.get(t) ?? 1) - 1;
      indegree.set(t, rest);
      if (rest === 0) queue.push(t);
    }
  }
  for (const n of nodes) if (!placed.has(n.name)) topo.push(n.name);

  const col = new Map<string, number>();
  for (const name of topo) {
    const srcs = sources.get(name) ?? [];
    col.set(name, srcs.length ? Math.max(...srcs.map((s) => col.get(s) ?? 0)) + 1 : 0);
  }
  return col;
}

/**
 * Barycenter crossing reduction: every column is re-sorted by the mean
 * position of its already-placed neighbors, sweeping left→right then
 * right→left over a few passes. This is what straightens fan-outs into
 * parallel, uncrossed curves — ties keep the original order, so the
 * result is deterministic for the same input.
 */
function orderColumns(
  columns: Map<number, TopologyNode[]>,
  sources: Map<string, string[]>,
  outgoing: Map<string, string[]>,
): void {
  const sortedCols = [...columns.keys()].sort((a, b) => a - b);
  const pos = new Map<string, number>();
  for (const c of sortedCols) columns.get(c)!.forEach((n, i) => pos.set(n.name, i));

  const meanPos = (name: string, useSources: boolean): number => {
    const neighbors = (useSources ? sources : outgoing).get(name) ?? [];
    const idxs: number[] = [];
    for (const n of neighbors) {
      const p = pos.get(n);
      if (p !== undefined) idxs.push(p);
    }
    return idxs.length ? idxs.reduce((a, b) => a + b, 0) / idxs.length : Number.POSITIVE_INFINITY;
  };

  for (let sweep = 0; sweep < 3; sweep += 1) {
    for (const leftToRight of [true, false]) {
      const cols = leftToRight ? sortedCols : [...sortedCols].reverse();
      for (const c of cols) {
        const colNodes = columns.get(c)!;
        colNodes.sort((a, b) => meanPos(a.name, leftToRight) - meanPos(b.name, leftToRight));
        colNodes.forEach((n, i) => pos.set(n.name, i));
      }
    }
  }
}

/**
 * Layered graph layout: longest-path columns (so edges span one column),
 * barycenter ordering inside each column (so edges barely cross), and
 * columns centering vertically on a shared centerline.
 */
export function layout(nodes: TopologyNode[], edges: TopologyEdge[]): Positioned[] {
  const col = assignColumns(nodes, edges);

  const columns = new Map<number, TopologyNode[]>();
  for (const node of nodes) {
    const c = col.get(node.name) ?? 0;
    columns.set(c, [...(columns.get(c) ?? []), node]);
  }

  const valid = validEdges(nodes, edges);
  const { sources, outgoing } = adjacency(valid);
  orderColumns(columns, sources, outgoing);

  const sortedCols = [...columns.keys()].sort((a, b) => a - b);
  const colHeights = sortedCols.map((c) => {
    const n = columns.get(c)!.length;
    return n * NODE_H + (n - 1) * GAP_Y;
  });
  const maxColH = colHeights.length ? Math.max(...colHeights) : 0;
  const result: Positioned[] = [];
  for (const c of sortedCols) {
    const colNodes = columns.get(c)!;
    const colH = colNodes.length * NODE_H + (colNodes.length - 1) * GAP_Y;
    const top = PAD + (maxColH - colH) / 2;
    colNodes.forEach((node, j) => {
      result.push({ node, x: PAD + c * (NODE_W + GAP_X), y: top + j * (NODE_H + GAP_Y), col: c });
    });
  }
  return result;
}

/** Overall SVG canvas the positioned nodes need. */
export function canvasSize(positioned: Positioned[]): { width: number; height: number } {
  if (positioned.length === 0) return { width: 0, height: 0 };
  const colCounts = new Map<number, number>();
  for (const p of positioned) colCounts.set(p.col, (colCounts.get(p.col) ?? 0) + 1);
  const maxCol = Math.max(...colCounts.keys());
  const maxColNodes = Math.max(...colCounts.values());
  return {
    width: PAD * 2 + (maxCol + 1) * NODE_W + maxCol * GAP_X,
    height: PAD * 2 + maxColNodes * NODE_H + Math.max(0, maxColNodes - 1) * GAP_Y,
  };
}

/**
 * Where each edge touches its nodes. Outgoing edges spread along the
 * right edge of the source (incoming along the left of the target), each
 * ordered by the OTHER node's vertical position — so a fan-out leaves as
 * a clean fan, one attachment per edge, instead of a bundle at a single
 * point, and the curves that must cross (the graph forces some) meet
 * mid-gap at a clean angle rather than piling up on the node border.
 */
export function computeEdges(
  positioned: Positioned[],
  edges: TopologyEdge[],
): EdgeGeom[] {
  const byName = new Map(positioned.map((p) => [p.node.name, p] as const));
  const valid = edges.filter((e) => byName.has(e.source) && byName.has(e.target));

  // attachment point: spread slots across the middle 64% of the card height
  const slot = (p: Positioned, side: "out" | "in", i: number, n: number) => {
    const t = (i + 1) / (n + 1);
    return {
      x: side === "out" ? p.x + NODE_W : p.x,
      y: p.y + NODE_H * (0.18 + 0.64 * t),
    };
  };

  const src = new Map<string, { x: number; y: number }>();
  const dst = new Map<string, { x: number; y: number }>();

  for (const name of new Set(valid.map((e) => e.source))) {
    const p = byName.get(name)!;
    const list = valid
      .filter((e) => e.source === name)
      .sort((a, b) => byName.get(a.target)!.y - byName.get(b.target)!.y);
    list.forEach((e, i) => src.set(edgeKey(e), slot(p, "out", i, list.length)));
  }
  for (const name of new Set(valid.map((e) => e.target))) {
    const p = byName.get(name)!;
    const list = valid
      .filter((e) => e.target === name)
      .sort((a, b) => byName.get(a.source)!.y - byName.get(b.source)!.y);
    list.forEach((e, i) => dst.set(edgeKey(e), slot(p, "in", i, list.length)));
  }

  const out: EdgeGeom[] = [];
  for (const e of valid) {
    const s = src.get(edgeKey(e));
    const d = dst.get(edgeKey(e));
    if (s && d) out.push({ key: edgeKey(e), x1: s.x, y1: s.y, x2: d.x, y2: d.y });
  }
  return out;
}

/** The cubic path the diagram draws for one edge. */
export function edgePath(g: EdgeGeom): string {
  const dx = Math.max(24, Math.abs(g.x2 - g.x1) * 0.5);
  return `M ${g.x1} ${g.y1} C ${g.x1 + dx} ${g.y1}, ${g.x2 - dx} ${g.y2}, ${g.x2} ${g.y2}`;
}
