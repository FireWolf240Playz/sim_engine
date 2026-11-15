/**
 * 3.2 — what the diagram and the feed show at one moment of the run.
 *
 * `liveFrame` is the diagram's view of the tick under the playback clock:
 * each node's load, its live capacity against its configured size, whether
 * an incident is on it right now, and how much traffic each edge carries.
 * Every number is the engine's (its ticks and chaos events); the colours use
 * the feed's thresholds (`sim_core/feed.py`), so a node turns red on the
 * diagram when the feed says it saturated.
 *
 * Pure and dependency-free: no React, no DOM.
 */
import type { Severity } from "./format";
import { frameIndex } from "./playback";
import type { NodeViewModel } from "./topologyView";
import type { ChaosMark, FeedLine, TimelineTick, TopologyNode } from "../types";

/** `sim_core/feed.py::SATURATED_AT`. */
export const LIVE_CRIT = 0.9;
/** `sim_core/feed.py::RECOVERED_BELOW`. */
export const LIVE_WARN = 0.75;
/** The most pulses one edge carries. */
export const PULSE_LEVELS = 4;

export interface LiveNode {
  name: string;
  utilization: number;
  queue: number;
  /** Live slots at this tick. */
  capacity: number;
  /** The configured size. */
  configured: number;
  inUse: number;
  tone: Severity;
  /** Fewer live slots than configured: an outage is on it. */
  degraded: boolean;
  /** An incident window is open on it at this moment. */
  struck: boolean;
}

export interface LiveFrame {
  /** Index of the tick shown. */
  index: number;
  /** Time of the tick shown. */
  time: number;
  /** An incident that names no node (a latency spike) is open. */
  wholePath: boolean;
  /** Requests per second on `edges[i]` in the tick's window. */
  edgeRates: number[];
  /** In the config's node order. */
  nodes: LiveNode[];
}

/** A node's colour from its load right now. */
export function liveTone(utilization: number): Severity {
  if (utilization >= LIVE_CRIT) return "crit";
  if (utilization >= LIVE_WARN) return "warn";
  return "ok";
}

/**
 * The nodes with an incident window open at `t` (each once, in the order
 * first hit), and whether one that names no node is open. Windows on the
 * same node are counted, so overlapping ones keep it marked until the last
 * closes; a window that never closed stays open to the end.
 */
export function activeIncidents(
  chaos: readonly ChaosMark[],
  t: number,
): { nodes: string[]; wholePath: boolean } {
  const open = new Map<string, number>();
  let whole = 0;
  for (const mark of chaos) {
    if (mark.t > t) continue;
    const step = mark.phase === "start" ? 1 : -1;
    if (mark.node === null) whole += step;
    else open.set(mark.node, (open.get(mark.node) ?? 0) + step);
  }
  return {
    nodes: [...open].filter(([, count]) => count > 0).map(([name]) => name),
    wholePath: whole > 0,
  };
}

/**
 * The diagram at `position`: the newest tick at or before it (null before
 * the first), with incidents read at `position` itself, so a node is marked
 * the moment its incident begins, before the next tick shows the damage.
 */
export function liveFrame(
  nodes: readonly TopologyNode[],
  ticks: readonly TimelineTick[],
  chaos: readonly ChaosMark[],
  position: number,
  interval: number,
): LiveFrame | null {
  const index = frameIndex(ticks, position);
  if (index < 0) return null;
  const tick = ticks[index];
  const incidents = activeIncidents(chaos, position);
  const struck = new Set(incidents.nodes);
  return {
    index,
    time: tick.time,
    wholePath: incidents.wholePath,
    edgeRates: (tick.edge_flow ?? []).map((count) => count / interval),
    nodes: nodes.map((node) => {
      const live = tick.per_component[node.name];
      const capacity = live?.capacity ?? node.max_capacity;
      const utilization = live?.utilization ?? 0;
      return {
        name: node.name,
        utilization,
        queue: live?.queue_length ?? 0,
        capacity,
        configured: node.max_capacity,
        inUse: live?.in_use ?? 0,
        tone: liveTone(utilization),
        degraded: capacity < node.max_capacity,
        struck: incidents.wholePath || struck.has(node.name),
      };
    }),
  };
}

/**
 * The diagram's node views with the moment laid over them, as flat
 * primitives (so a node whose moment did not change keeps equal memo
 * props). Without a frame the views come back as they are; a node the
 * frame does not know keeps its run-level view.
 */
export function withLive(
  views: NodeViewModel[],
  frame: LiveFrame | null | undefined,
): NodeViewModel[] {
  if (!frame) return views;
  const byName = new Map(frame.nodes.map((node) => [node.name, node] as const));
  return views.map((view) => {
    const live = byName.get(view.name);
    if (!live) return view;
    return {
      ...view,
      liveUtil: live.utilization,
      liveTone: live.tone,
      liveQueue: live.queue,
      liveCapacity: live.capacity,
      liveConfigured: live.configured,
      liveStruck: live.struck,
    };
  });
}

/** The busiest edge of the run, in requests per second (the pulse scale). */
export function maxEdgeRate(ticks: readonly TimelineTick[], interval: number): number {
  let max = 0;
  for (const tick of ticks) {
    for (const count of tick.edge_flow ?? []) max = Math.max(max, count / interval);
  }
  return max;
}

/** 0–4 pulses: none on an idle edge, at least one on any edge with traffic. */
export function pulseLevel(rate: number, maxRate: number): number {
  if (!(rate > 0) || !(maxRate > 0)) return 0;
  return Math.min(PULSE_LEVELS, Math.max(1, Math.ceil((PULSE_LEVELS * rate) / maxRate)));
}

/** The feed lines up to `position`; the same list once every line is visible. */
export function visibleLines(lines: readonly FeedLine[], position: number): readonly FeedLine[] {
  let count = 0;
  while (count < lines.length && lines[count].t <= position) count += 1;
  return count === lines.length ? lines : lines.slice(0, count);
}

/** `t=62s`, as the feed prints a time. */
export function feedTime(t: number): string {
  return `t=${Number(t.toFixed(1))}s`;
}
