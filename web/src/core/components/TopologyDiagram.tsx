"use client";

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
 * Layered graph layout: entries (nodes nothing points at) sit in column 0,
 * BFS depth gives the rest their column, and columns center vertically on a
 * shared centerline (compact nodes keep multi-node columns tidy).
 */
function layout(nodes: TopologyNode[], edges: TopologyEdge[]): Positioned[] {
  const targets = new Set(edges.map((e) => e.target));
  const entries = nodes.filter((n) => !targets.has(n.name));
  const starts = (entries.length ? entries : nodes).map((n) => n.name);

  const depth = new Map<string, number>();
  let frontier = [...starts];
  starts.forEach((name) => depth.set(name, 0));
  let d = 1;
  while (frontier.length) {
    const next: string[] = [];
    for (const name of frontier) {
      for (const edge of edges) {
        if (edge.source !== name) continue;
        if (!depth.has(edge.target)) {
          depth.set(edge.target, d);
          next.push(edge.target);
        }
      }
    }
    frontier = next;
    d += 1;
  }
  for (const node of nodes) {
    if (!depth.has(node.name)) depth.set(node.name, 0);
  }

  const columns = new Map<number, TopologyNode[]>();
  for (const node of nodes) {
    const c = depth.get(node.name) ?? 0;
    columns.set(c, [...(columns.get(c) ?? []), node]);
  }

  const colHeights = [...columns.values()].map((col) => col.length * NODE_H + (col.length - 1) * GAP_Y);
  const maxColH = colHeights.length ? Math.max(...colHeights) : 0;
  const result: Positioned[] = [];
  for (const [col, colNodes] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    const colH = colNodes.length * NODE_H + (colNodes.length - 1) * GAP_Y;
    const top = PAD + (maxColH - colH) / 2;
    colNodes.forEach((node, j) => {
      result.push({ node, x: PAD + col * (NODE_W + GAP_X), y: top + j * (NODE_H + GAP_Y), col });
    });
  }
  return result;
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
  pal,
}: {
  p: Positioned;
  info: ComponentSizing | undefined;
  isTarget: boolean;
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
    <g>
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
      ) : null}

      <rect
        x={x}
        y={y}
        width={NODE_W}
        height={NODE_H}
        rx={10}
        fill={sev ? sev.soft : pal.nodeBase}
        stroke={sev ? sev.color : pal.nodeStroke}
        strokeOpacity={sev ? 0.4 : 1}
        strokeWidth={1}
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
  const pal = PALETTES[theme];
  const positioned = layout(nodes, edges);
  if (positioned.length === 0) return null;

  const byName = new Map(positioned.map((p) => [p.node.name, p] as const));
  const colCounts = new Map<number, number>();
  for (const p of positioned) colCounts.set(p.col, (colCounts.get(p.col) ?? 0) + 1);
  const maxCol = Math.max(...colCounts.keys());
  const maxColNodes = Math.max(...colCounts.values());
  const width = PAD * 2 + (maxCol + 1) * NODE_W + maxCol * GAP_X;
  const height = PAD * 2 + maxColNodes * NODE_H + Math.max(0, maxColNodes - 1) * GAP_Y;

  const flagged = new Set(targetNames);

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
        <defs>
          <marker
            id="eleven-arrow"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0 0 8 4 0 8Z" fill={pal.edge} />
          </marker>
        </defs>

        {/* edges first, so nodes sit on top of the lines: a quiet base line
            plus a slow accent dash drifting source → target (the request
            flow) — motion that means "traffic is moving" */}
        {edges.map((edge, i) => {
          const s = byName.get(edge.source);
          const t = byName.get(edge.target);
          if (!s || !t) return null;
          const x1 = s.x + NODE_W;
          const y1 = s.y + NODE_H / 2;
          const x2 = t.x;
          const y2 = t.y + NODE_H / 2;
          const dx = Math.max(24, (x2 - x1) / 2);
          const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2 - 3} ${y2}`;
          return (
            <g key={`${edge.source}-${edge.target}-${i}`}>
              <path d={d} fill="none" stroke={pal.edge} strokeWidth={1.5} markerEnd="url(#eleven-arrow)" />
              <path
                d={d}
                fill="none"
                stroke={pal.accent}
                strokeOpacity={0.45}
                strokeWidth={1.5}
                strokeLinecap="round"
                strokeDasharray="4 10"
                className="eleven-edge-flow"
              />
            </g>
          );
        })}

        {positioned.map((p) => (
          <NodeView key={p.node.name} p={p} info={sizing[p.node.name]} isTarget={flagged.has(p.node.name)} pal={pal} />
        ))}
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
          bar = mean utilization · hover a node for detail
        </span>
      </div>
    </div>
  );
}
