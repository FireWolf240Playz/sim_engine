"use client";

import type { ReactNode } from "react";
import type { ComponentRole, ComponentSizing, TopologyEdge, TopologyNode } from "../types";
import {
  SEVERITY_BG,
  fmtPct,
  sizingSeverity,
  type Severity,
} from "../lib/format";
import { useTheme } from "../state/ThemeContext";

const NODE_W = 132;
const NODE_H = 84;
const GAP_X = 88;
const GAP_Y = 48;
const PAD = 12;
const LABEL_H = 26;

/**
 * SVG presentation attributes can't read CSS variables, so the diagram
 * picks an explicit palette per theme (values mirror the tokens in
 * globals.css) and re-renders when the theme flips.
 */
const PALETTES = {
  light: {
    neutral: "#9aa3b5",
    glyph: "#667085",
    nodeBase: "#f1f3f7",
    nameInk: "#171a21",
    dimInk: "#667085",
    chipBg: "#ffffff",
    target: "#d92d20",
    sev: { ok: "#178a50", warn: "#b26209", crit: "#d92d20" } as Record<Severity, string>,
  },
  dark: {
    neutral: "#26334d",
    glyph: "#8b96ac",
    nodeBase: "#151d2e",
    nameInk: "#e8edf5",
    dimInk: "#8b96ac",
    chipBg: "#0e1420",
    target: "#e5484d",
    sev: { ok: "#2fbf71", warn: "#e8a13a", crit: "#e5484d" } as Record<Severity, string>,
  },
} as const;

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

/** Severity mark as raw SVG (the HTML SeverityIcon can't render inside SVG). */
function SeverityMark({
  severity,
  x,
  y,
  color,
}: {
  severity: Severity;
  x: number;
  y: number;
  color: string;
}) {
  if (severity === "ok") {
    return <path d={`M${x} ${y + 6} l2.2 2.2 4.3-4.8`} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />;
  }
  if (severity === "warn") {
    return (
      <g>
        <path d={`M${x + 6} ${y} L${x + 11} ${y + 8.6} L${x + 1} ${y + 8.6} Z`} fill="none" stroke={color} strokeWidth="1.3" strokeLinejoin="round" />
        <path d={`M${x + 6} ${y + 3.4}v2.4`} stroke={color} strokeWidth="1.3" strokeLinecap="round" />
        <circle cx={x + 6} cy={y + 7.6} r="0.8" fill={color} stroke="none" />
      </g>
    );
  }
  return <path d={`M${x + 1.5} ${y + 1.5} l8 8 M${x + 9.5} ${y + 1.5} l-8 8`} stroke={color} strokeWidth="1.6" strokeLinecap="round" />;
}

interface Positioned {
  node: TopologyNode;
  x: number;
  y: number;
  col: number;
}

/**
 * Layered graph layout: entries (nodes nothing points at) sit in column 0,
 * BFS depth gives the rest their column, and columns center vertically.
 * Handles fan-out (worker → cache, worker → pricing_api) and fan-in.
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

  const maxColH = Math.max(...[...columns.values()].map((col) => col.length * NODE_H + (col.length - 1) * GAP_Y));
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
 * The request path as a live diagram (never a text list): layered by
 * request flow, each node's fill encodes mean utilization, its ring + word
 * encode the sizing verdict, and a dashed TARGET chip marks the nodes the
 * selected incident will hit.
 */
export function TopologyDiagram({ nodes, edges, sizing, targetNames, wholePath }: Props) {
  const { theme } = useTheme();
  const pal = PALETTES[theme];
  const positioned = layout(nodes, edges);
  const byName = new Map(positioned.map((p) => [p.node.name, p]));
  const colCounts = new Map<number, number>();
  for (const p of positioned) colCounts.set(p.col, (colCounts.get(p.col) ?? 0) + 1);
  const maxCol = Math.max(...colCounts.keys());
  const maxColNodes = Math.max(...colCounts.values());
  const width = PAD * 2 + (maxCol + 1) * NODE_W + maxCol * GAP_X;
  const height = PAD * 2 + maxColNodes * NODE_H + Math.max(0, maxColNodes - 1) * GAP_Y + LABEL_H;

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
            <path d="M0 0 8 4 0 8Z" fill={pal.neutral} />
          </marker>
        </defs>

        {/* edges first, so nodes sit on top of the lines */}
        {edges.map((edge, i) => {
          const s = byName.get(edge.source);
          const t = byName.get(edge.target);
          if (!s || !t) return null;
          const x1 = s.x + NODE_W;
          const y1 = s.y + NODE_H / 2;
          const x2 = t.x;
          const y2 = t.y + NODE_H / 2;
          const dx = Math.max(30, (x2 - x1) / 2);
          return (
            <path
              key={`${edge.source}-${edge.target}-${i}`}
              d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2 - 2} ${y2}`}
              fill="none"
              stroke={pal.neutral}
              strokeWidth="1.5"
              markerEnd="url(#eleven-arrow)"
            />
          );
        })}

        {positioned.map(({ node, x, y }) => {
          const info = sizing[node.name];
          const severity: Severity | null = info ? sizingSeverity(info.status) : null;
          const ring = severity ? pal.sev[severity] : pal.neutral;
          const util = info?.mean_utilization ?? 0;
          const fillOpacity = severity ? 0.08 + 0.28 * Math.min(1, util) : 0.05;
          const isTarget = flagged.has(node.name);

          return (
            <g key={node.name}>
              <rect
                x={x}
                y={y}
                width={NODE_W}
                height={NODE_H}
                rx="10"
                fill={severity ? ring : pal.nodeBase}
                fillOpacity={fillOpacity}
                stroke={isTarget && !severity ? pal.target : ring}
                strokeWidth={isTarget ? 1.5 : 1}
              />
              <RoleGlyph role={node.role} x={x + 10} y={y + 10} color={pal.glyph} />
              <text x={x + 10} y={y + 46} fill={pal.nameInk} fontSize="12.5" fontFamily="IBM Plex Mono, ui-monospace, monospace" fontWeight="600">
                {node.name}
              </text>
              <text x={x + 10} y={y + 60} fill={pal.dimInk} fontSize="9" fontFamily="Inter, system-ui, sans-serif">
                {node.role.replace("_", " ")}
              </text>
              <text x={x + 10} y={y + 73} fill={pal.dimInk} fontSize="9.5" fontFamily="IBM Plex Mono, ui-monospace, monospace">
                ×{node.max_capacity} slots
              </text>
              <text
                x={x + NODE_W - 10}
                y={y + 46}
                textAnchor="end"
                fill={severity ? ring : pal.dimInk}
                fontSize="12"
                fontFamily="IBM Plex Mono, ui-monospace, monospace"
              >
                {info ? fmtPct(info.mean_utilization, 0) : "n/a"}
              </text>
              {isTarget ? (
                <g>
                  <rect
                    x={x + NODE_W - 54}
                    y={y + 8}
                    width={46}
                    height={14}
                    rx="4"
                    fill={pal.chipBg}
                    stroke={pal.target}
                    strokeWidth="1"
                    strokeDasharray="3 2"
                  />
                  <text
                    x={x + NODE_W - 31}
                    y={y + 18}
                    textAnchor="middle"
                    fill={pal.target}
                    fontSize="8"
                    letterSpacing="0.12em"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                  >
                    TARGET
                  </text>
                </g>
              ) : null}
              {severity && info ? (
                <>
                  <SeverityMark severity={severity} x={x + 10} y={y + NODE_H + 7} color={ring} />
                  <text
                    x={x + 26}
                    y={y + NODE_H + 15}
                    fill={ring}
                    fontSize="9.5"
                    letterSpacing="0.1em"
                    fontFamily="Inter, system-ui, sans-serif"
                  >
                    {info.status.replace("_", "-").toUpperCase()}
                  </text>
                </>
              ) : null}
            </g>
          );
        })}
      </svg>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-ink-dim">
        <span className="font-medium uppercase tracking-[0.08em]">verdict</span>
        {(["right_sized", "oversized", "undersized"] as const).map((status) => {
          const sev = sizingSeverity(status);
          return (
            <span key={status} className="flex items-center gap-1.5">
              <span className={`inline-block h-2 w-2 rounded-[3px] ${SEVERITY_BG[sev]}`} />
              {status.replace("_", "-")}
            </span>
          );
        })}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-4 rounded-[3px] border border-dashed border-sev-crit" />
          incident target
        </span>
        <span className="ml-auto hidden font-mono sm:inline">
          fill = mean utilization · ring = verdict
        </span>
      </div>
    </div>
  );
}
