/**
 * 1.3f — the diagram's view model, nothing else.
 *
 * Every node card is drawn from a small view model of primitives only.
 * Unchanged data keeps its identity across re-runs (`runResult.ts`), so
 * the plain `memo` in `TopologyDiagram.tsx` skips by default — no
 * hand-written comparators.
 *
 * Pure and dependency-free on purpose: no React, no DOM — Vitest runs it
 * in node.
 */
import type {
  ComponentRole,
  ComponentSizing,
  SizingStatus,
  TopologyEdge,
  TopologyNode,
} from "../types";

/** The diagram's props (lives here so the view model can name the type). */
export interface TopologyDiagramProps {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  sizing: Record<string, ComponentSizing>;
  /** Node names the selected incident targets (empty = clean run). */
  targetNames: string[];
  /** True for whole-path incidents (latency spike, cascade tail). */
  wholePath: boolean;
  /** 1.3g: nodes whose size moved since the last measured run. */
  stale?: { names: readonly string[]; running: boolean };
}

/**
 * Everything one node card draws, as primitives only. Hover/focus state
 * deliberately does NOT live here: it is per-pointer, and if it were a
 * field, pointing at one node would "change" every other node's view.
 */
export interface NodeViewModel {
  name: string;
  role: ComponentRole;
  capacity: number;
  /** Null when the node has no sizing yet — unmeasured, not zero. */
  status: SizingStatus | null;
  meanUtil: number | null;
  p95Util: number | null;
  p95Queue: number | null;
  /** Null when there is no sizing yet. */
  recommended: number | null;
  isTarget: boolean;
  inPath: boolean;
  /**
   * 1.3g: this node's size moved since the numbers on screen were
   * measured — "running" while the re-run is in flight, "unmeasured"
   * when it failed. The old size's utilization must not pass as current.
   */
  remeasure: "running" | "unmeasured" | null;
}

/** One view per node, in node order. */
export function nodeViews(
  nodes: TopologyNode[],
  sizing: Record<string, ComponentSizing>,
  targetNames: string[],
  wholePath: boolean,
  stale: { names: readonly string[]; running: boolean } = { names: [], running: false },
): NodeViewModel[] {
  const targets = new Set(targetNames);
  const staleNames = new Set(stale.names);
  return nodes.map((n) => {
    const info = sizing[n.name];
    return {
      name: n.name,
      role: n.role,
      capacity: n.max_capacity,
      status: info ? info.status : null,
      meanUtil: info ? info.mean_utilization : null,
      p95Util: info ? info.p95_utilization : null,
      p95Queue: info ? info.p95_queue : null,
      recommended: info ? info.recommended_capacity : null,
      isTarget: targets.has(n.name),
      inPath: wholePath,
      remeasure: staleNames.has(n.name) ? (stale.running ? "running" : "unmeasured") : null,
    };
  });
}
