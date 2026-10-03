"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { ComponentRole, ComponentSizing, TopologyEdge, TopologyNode } from "../types";
import {
  SEVERITY_BG,
  SEVERITY_BORDER,
  fmtPct,
  sizingSeverity,
  type Severity,
} from "../lib/format";
import { useTheme, type Theme } from "../state/ThemeContext";

const NODE_W = 160;
const NODE_H = 64;
const GAP_X = 56;
const GAP_Y = 36;
const PAD = 12;

/**
 * SVG presentation attributes can't read CSS variables, so the diagram
 * picks an explicit palette per theme (values mirror the tokens in
 * globals.css) and re-renders when the theme flips.
 */
interface Palette {
  nodeBase: string;
  nodeStroke: string;
  chipBg: string;
  chipStroke: string;
  nameInk: string;
  dimInk: string;
  accent: string;
  edge: string;
  track: { color: string; opacity: number };
  sev: Record<Severity, { color: string; soft: string }>;
}

const PALETTES: Record<Theme, Palette> = {
  light: {
    nodeBase: "#ffffff",
    nodeStroke: "#e4e7ee",
    chipBg: "#ffffff",
    chipStroke: "#e4e7ee",
    nameInk: "#171a21",
    dimInk: "#667085",
    accent: "#6d5df0",
    edge: "#c6cdda",
    track: { color: "#ffffff", opacity: 0.7 },
    sev: {
      ok: { color: "#178a50", soft: "#e7f5ee" },
      warn: { color: "#b26209", soft: "#fdf1de" },
      crit: { color: "#d92d20", soft: "#fdeceb" },
    },
  },
  dark: {
    nodeBase: "#151d2e",
    nodeStroke: "#26334d",
    chipBg: "#1b2436",
    chipStroke: "#26334d",
    nameInk: "#e8edf5",
    dimInk: "#8b96ac",
    accent: "#8b7bff",
    edge: "#33415e",
    track: { color: "#0e1420", opacity: 0.7 },
    sev: {
      ok: { color: "#2fbf71", soft: "#2fbf7126" },
      warn: { color: "#e8a13a", soft: "#e8a13a29" },
      crit: { color: "#e5484d", soft: "#e5484d26" },
    },
  },
};

/** Role glyph (16×16 stroke icon) — shape, not color, carries the role. */
function RoleGlyph({ role, x, y, color }: { role: ComponentRole; x: number; y: number; color: string }) {
  const stroke = {
    fill: "none",
    stroke: color,
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  const paths: Record<ComponentRole, ReactNode> = {
    load_balancer: <path d="M1 8h6m0 0 8-5M7 8l8 5" {...stroke} />,
    worker: (
      <g {...stroke}>
        <rect x="1" y="1" width="5.5" height="5.5" rx="1" />
        <rect x="9.5" y="1" width="5.5" height="5.5" rx="1" />
        <rect x="1" y="9.5" width="5.5" height="5.5" rx="1" />
        <rect x="9.5" y="9.5" width="5.5" height="5.5" rx="1" />
      </g>
    ),
    cache: <path d="M9.5 1 3 10h4l-1.5 5L12 6H8Z" {...stroke} />,
    database: (
      <g {...stroke}>
        <ellipse cx="8" cy="3.6" rx="6" ry="2.4" />
        <path d="M2 3.6v8.8c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4V3.6" />
        <path d="M2 8c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4" />
      </g>
    ),
    external_api: <path d="M5 12.5h8a2.6 2.6 0 0 0 .4-5.2A4.3 4.3 0 0 0 5.3 5.8 3.3 3.3 0 0 0 5 12.5Z" {...stroke} />,
    generic: <path d="M8 1.2 14.3 4.6v6.8L8 14.8 1.7 11.4V4.6Z" {...stroke} />,
  };
  return <g transform={`translate(${x}, ${y})`}>{paths[role]}</g>;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

interface Positioned {
  node: TopologyNode;
  x: number;
  y: number;
  col: number;
}

/**
 * Column assignment, longest-path style: a node sits one column past its
 * DEEPEST source, so (almost) every edge spans exactly one column. The old
 * BFS "first discovery" depth let a node land left of one of its sources,
 * and those long multi-column diagonals were the main source of the choppy,
 * crossing connections. Kahn's topological order keeps it deterministic;
 * nodes stuck in a cycle fall back to their original order.
 */
function assignColumns(nodes: TopologyNode[], edges: TopologyEdge[]): Map<string, number> {
  const names = new Set(nodes.map((n) => n.name));
  const valid = edges.filter(
    (e) => names.has(e.source) && names.has(e.target) && e.source !== e.target,
  );

  const sources = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  nodes.forEach((n) => indegree.set(n.name, 0));
  for (const e of valid) {
    sources.set(e.target, [...(sources.get(e.target) ?? []), e.source]);
    outgoing.set(e.source, [...(outgoing.get(e.source) ?? []), e.target]);
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
 * Barycenter crossing reduction (Sugiyama-style): every column is re-sorted
 * by the mean position of its already-placed neighbors, sweeping
 * left→right then right→left over a few passes. This is what straightens
 * fan-outs into parallel, uncrossed curves — ties keep the original order,
 * so the result is deterministic for the same input.
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
function layout(nodes: TopologyNode[], edges: TopologyEdge[]): Positioned[] {
  const col = assignColumns(nodes, edges);

  const columns = new Map<number, TopologyNode[]>();
  for (const node of nodes) {
    const c = col.get(node.name) ?? 0;
    columns.set(c, [...(columns.get(c) ?? []), node]);
  }

  // Adjacency for the ordering pass (same filtering rules as assignColumns).
  const names = new Set(nodes.map((n) => n.name));
  const valid = edges.filter(
    (e) => names.has(e.source) && names.has(e.target) && e.source !== e.target,
  );
  const sources = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const e of valid) {
    sources.set(e.target, [...(sources.get(e.target) ?? []), e.source]);
    outgoing.set(e.source, [...(outgoing.get(e.source) ?? []), e.target]);
  }
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

interface EdgeGeom {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/**
 * Where each edge touches its nodes. Outgoing edges spread along the right
 * edge of the source (incoming along the left of the target), each ordered
 * by the OTHER node's vertical position — so a fan-out leaves as a clean
 * fan, one attachment per edge, instead of a bundle at a single point, and
 * the curves that must cross (the graph forces some) meet mid-gap at a
 * clean angle rather than piling up on the node border.
 */
function computeEdges(positioned: Positioned[], edges: TopologyEdge[]): EdgeGeom[] {
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

  const key = (e: TopologyEdge) => `${e.source}|${e.target}`;
  const src = new Map<string, { x: number; y: number }>();
  const dst = new Map<string, { x: number; y: number }>();

  for (const name of new Set(valid.map((e) => e.source))) {
    const p = byName.get(name)!;
    const list = valid
      .filter((e) => e.source === name)
      .sort((a, b) => byName.get(a.target)!.y - byName.get(b.target)!.y);
    list.forEach((e, i) => src.set(key(e), slot(p, "out", i, list.length)));
  }
  for (const name of new Set(valid.map((e) => e.target))) {
    const p = byName.get(name)!;
    const list = valid
      .filter((e) => e.target === name)
      .sort((a, b) => byName.get(a.source)!.y - byName.get(b.source)!.y);
    list.forEach((e, i) => dst.set(key(e), slot(p, "in", i, list.length)));
  }

  const out: EdgeGeom[] = [];
  for (const e of valid) {
    const s = src.get(key(e));
    const d = dst.get(key(e));
    if (s && d) out.push({ key: key(e), x1: s.x, y1: s.y, x2: d.x, y2: d.y });
  }
  return out;
}

interface Props {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  sizing: Record<string, ComponentSizing>;
  /** Node names the selected incident targets (empty = clean run). */
  targetNames: string[];
  /** True for whole-path incidents (latency spike, cascade tail). */
  wholePath: boolean;
}

/**
 * One node card: role glyph in a chip, name, sizing verdict (tint + word +
 * utilization bar) — every signal lives INSIDE the card, nothing floats
 * underneath. Incident targets get an animated dashed accent ring: accent
 * means "the incident will hit this", severity colors mean "health".
 */
function NodeView({
  p,
  info,
  isTarget,
  inPath,
  dimmed,
  isHovered,
  onHover,
  pal,
}: {
  p: Positioned;
  info: ComponentSizing | undefined;
  isTarget: boolean;
  /** Whole-path incident: soft halo on every node in the blast radius. */
  inPath: boolean;
  /** Hover focus active and this node is NOT part of the focused subgraph. */
  dimmed: boolean;
  /** This is the node the pointer is on (accent stroke emphasis). */
  isHovered: boolean;
  onHover: (name: string | null) => void;
  pal: Palette;
}) {
  const { node, x, y } = p;
  const severity = info ? sizingSeverity(info.status) : null;
  const sev = severity ? pal.sev[severity] : null;
  const util = info?.mean_utilization ?? 0;
  const trackW = NODE_W - 18;
  const roleLabel = node.role.replace(/_/g, " ");

  const tooltip = [
    `${node.name} — ${roleLabel}, ×${node.max_capacity} capacity`,
    info
      ? `${info.status.replace("_", "-")}: mean ${fmtPct(info.mean_utilization, 0)} · p95 ${fmtPct(
          info.p95_utilization,
          0,
        )} · p95 queue ${info.p95_queue} · recommended ×${info.recommended_capacity}`
      : null,
    isTarget ? "incident target" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <g
      opacity={dimmed ? 0.22 : 1}
      style={{ transition: "opacity 160ms ease" }}
      onMouseEnter={() => onHover(p.node.name)}
      onMouseLeave={() => onHover(null)}
    >
      <title>{tooltip}</title>
      {isTarget ? (
        <>
          {/* soft halo */}
          <rect
            x={x - 5}
            y={y - 5}
            width={NODE_W + 10}
            height={NODE_H + 10}
            rx={14}
            fill="none"
            stroke={pal.accent}
            strokeOpacity={0.16}
            strokeWidth={4}
          />
          {/* rotating dashed ring: the incident is "locked on" this node */}
          <rect
            x={x - 5}
            y={y - 5}
            width={NODE_W + 10}
            height={NODE_H + 10}
            rx={14}
            fill="none"
            stroke={pal.accent}
            strokeWidth={1.4}
            strokeDasharray="5 7"
            className="eleven-target-ring"
          />
        </>
      ) : inPath ? (
        // blast radius, not the victim: halo only, no ring
        <rect
          x={x - 5}
          y={y - 5}
          width={NODE_W + 10}
          height={NODE_H + 10}
          rx={14}
          fill="none"
          stroke={pal.accent}
          strokeOpacity={0.1}
          strokeWidth={3}
        />
      ) : null}

      <rect
        x={x}
        y={y}
        width={NODE_W}
        height={NODE_H}
        rx={10}
        fill={sev ? sev.soft : pal.nodeBase}
        stroke={isHovered ? pal.accent : sev ? sev.color : pal.nodeStroke}
        strokeOpacity={isHovered ? 1 : sev ? 0.4 : 1}
        strokeWidth={isHovered ? 1.6 : 1}
      />
      <rect
        x={x + 9}
        y={y + 9}
        width={28}
        height={28}
        rx={8}
        fill={pal.chipBg}
        stroke={pal.chipStroke}
        strokeWidth={1}
      />
      <RoleGlyph role={node.role} x={x + 15} y={y + 15} color={sev ? sev.color : pal.dimInk} />

      <text
        x={x + 46}
        y={y + 22}
        fill={pal.nameInk}
        fontSize={12}
        fontFamily="IBM Plex Mono, ui-monospace, monospace"
        fontWeight={600}
        letterSpacing="0.01em"
      >
        {truncate(node.name, 13)}
      </text>

      {sev && info ? (
        <>
          <text
            x={x + 46}
            y={y + 37}
            fill={sev.color}
            fontSize={8}
            fontWeight={600}
            letterSpacing="0.14em"
            fontFamily="Inter, system-ui, sans-serif"
          >
            {info.status.replace("_", "-").toUpperCase()}
          </text>
          <text
            x={x + NODE_W - 9}
            y={y + 37}
            textAnchor="end"
            fill={pal.dimInk}
            fontSize={10}
            fontFamily="IBM Plex Mono, ui-monospace, monospace"
          >
            {fmtPct(info.mean_utilization, 0)}
          </text>
          <rect
            x={x + 9}
            y={y + 45}
            width={trackW}
            height={4}
            rx={2}
            fill={pal.track.color}
            fillOpacity={pal.track.opacity}
          />
          {util > 0 ? (
            <rect
              x={x + 9}
              y={y + 45}
              width={Math.max(4, trackW * Math.min(1, util))}
              height={4}
              rx={2}
              fill={sev.color}
            />
          ) : null}
        </>
      ) : null}
    </g>
  );
}

/**
 * The request path as a live diagram (never a text list): layered by request
 * flow, each node is a compact card whose tint + bar encode mean utilization
 * and whose verdict word carries the sizing call, edges show request flow
 * (animated dashes, restrained lens), and the selected incident's targets
 * wear a dashed accent ring.
 */
export function TopologyDiagram({ nodes, edges, sizing, targetNames, wholePath }: Props) {
  const { theme } = useTheme();
  const [hovered, setHovered] = useState<string | null>(null);
  const pal = PALETTES[theme];
  const positioned = layout(nodes, edges);
  if (positioned.length === 0) return null;

  const colCounts = new Map<number, number>();
  for (const p of positioned) colCounts.set(p.col, (colCounts.get(p.col) ?? 0) + 1);
  const maxCol = Math.max(...colCounts.keys());
  const maxColNodes = Math.max(...colCounts.values());
  const width = PAD * 2 + (maxCol + 1) * NODE_W + maxCol * GAP_X;
  const height = PAD * 2 + maxColNodes * NODE_H + Math.max(0, maxColNodes - 1) * GAP_Y;

  const flagged = new Set(targetNames);
  const edgeGeoms = computeEdges(positioned, edges);

  const edgeKey = (e: TopologyEdge) => `${e.source}|${e.target}`;
  const metaOf = new Map(edges.map((e) => [edgeKey(e), e] as const));
  const colOf = new Map(positioned.map((p) => [p.node.name, p.col] as const));

  // Hover focus: hovering a node keeps it, its direct neighbors, and the
  // edges between them at full strength and fades everything else — the
  // direct answer to "what is connected to what" in a dense graph.
  const focusEdges = new Set<string>();
  const focusNodes = new Set<string>();
  if (hovered) {
    focusNodes.add(hovered);
    for (const e of edges) {
      if (e.source === hovered || e.target === hovered) {
        focusEdges.add(edgeKey(e));
        focusNodes.add(e.source);
        focusNodes.add(e.target);
      }
    }
  }

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Topology: ${nodes.map((n) => n.name).join(", ")}; incident targets: ${
          wholePath ? "whole path" : targetNames.join(", ") || "none"
        }`}
      >
        {/* edges first, so nodes sit on top of the lines: a quiet base line
            plus a slow accent dash drifting source → target (the request
            flow) — motion that means "traffic is moving" */}
        {edgeGeoms.map((g) => {
          const meta = metaOf.get(g.key);
          const long = meta
            ? Math.abs((colOf.get(meta.target) ?? 0) - (colOf.get(meta.source) ?? 0)) > 1
            : false;
          const active = !hovered || focusEdges.has(g.key);
          const emphasized = hovered !== null && focusEdges.has(g.key);
          const dx = Math.max(24, Math.abs(g.x2 - g.x1) * 0.5);
          const d = `M ${g.x1} ${g.y1} C ${g.x1 + dx} ${g.y1}, ${g.x2 - dx} ${g.y2}, ${g.x2} ${g.y2}`;
          return (
            <g
              key={g.key}
              opacity={active ? (long ? 0.55 : 1) : 0.08}
              style={{ transition: "opacity 160ms ease" }}
            >
              <title>
                {meta ? `${meta.source} → ${meta.target} · p=${meta.probability}` : g.key}
              </title>
              <path
                d={d}
                fill="none"
                stroke={emphasized ? pal.accent : pal.edge}
                strokeOpacity={emphasized ? 0.9 : 1}
                strokeWidth={emphasized ? 2 : long ? 1.1 : 1.5}
              />
              <path
                d={d}
                fill="none"
                stroke={pal.accent}
                strokeOpacity={emphasized ? 0.8 : long ? 0.25 : 0.45}
                strokeWidth={1.5}
                strokeLinecap="round"
                strokeDasharray="4 10"
                className="eleven-edge-flow"
              />
            </g>
          );
        })}

        {positioned.map((p) => {
          const name = p.node.name;
          return (
            <NodeView
              key={name}
              p={p}
              info={sizing[name]}
              isTarget={flagged.has(name)}
              inPath={wholePath}
              dimmed={hovered !== null && !focusNodes.has(name)}
              isHovered={hovered === name}
              onHover={setHovered}
              pal={pal}
            />
          );
        })}
      </svg>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-ink-dim">
        {(["right_sized", "oversized", "undersized"] as const).map((status) => {
          const sev = sizingSeverity(status);
          return (
            <span key={status} className="flex items-center gap-1.5">
              <span className={`inline-block h-2.5 w-2.5 rounded-[4px] border ${SEVERITY_BORDER[sev]} ${SEVERITY_BG[sev]}`} />
              {status.replace("_", "-")}
            </span>
          );
        })}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-4 rounded-[4px] border border-dashed border-accent" />
          incident target
        </span>
        <span className="ml-auto hidden font-mono text-[10px] sm:inline">
          bar = mean utilization · hover a node to trace its connections
        </span>
      </div>
    </div>
  );
}
