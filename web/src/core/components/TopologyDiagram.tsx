"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { ComponentRole, ComponentSizing, TopologyEdge, TopologyNode } from "../types";
import {
  SEVERITY_BG,
  SEVERITY_BORDER,
  SEVERITY_FILL,
  SEVERITY_FILL_SOFT,
  SEVERITY_STROKE,
  fmtPct,
  sizingSeverity,
} from "../lib/format";
import {
  NODE_H,
  NODE_W,
  canvasSize,
  computeEdges,
  edgeKey,
  edgePath,
  layout,
  type Positioned,
} from "../lib/graphLayout";

const MONO = "IBM Plex Mono, ui-monospace, monospace";

/**
 * Every color below is a Tailwind utility resolving to a CSS variable
 * from globals.css — `fill-surface-1`, `stroke-edge`, `fill-sev-crit`.
 * The diagram therefore carries no palette of its own and follows a
 * theme flip through CSS alone, with no re-render and no second copy of
 * the token values to keep in sync.
 *
 * The `dark:` variant (bound to `[data-theme="dark"]` in globals.css) is
 * used only where light and dark genuinely want DIFFERENT tokens rather
 * than the same token re-defined.
 */
const SURFACE = {
  card: "fill-surface-1",
  cardStroke: "stroke-line",
  chip: "fill-surface-1 dark:fill-surface-2",
  chipStroke: "stroke-line",
  name: "fill-ink",
  dim: "fill-ink-dim",
  dimStroke: "stroke-ink-dim",
  accentStroke: "stroke-accent",
  edge: "stroke-edge",
  track: "fill-surface-1 dark:fill-surface-0",
} as const;

/** Role glyph (16×16 stroke icon) — shape, not color, carries the role. */
function RoleGlyph({
  role,
  x,
  y,
  strokeClass,
}: {
  role: ComponentRole;
  x: number;
  y: number;
  strokeClass: string;
}) {
  const stroke = {
    className: strokeClass,
    fill: "none",
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
    external_api: (
      <path
        d="M5 12.5h8a2.6 2.6 0 0 0 .4-5.2A4.3 4.3 0 0 0 5.3 5.8 3.3 3.3 0 0 0 5 12.5Z"
        {...stroke}
      />
    ),
    generic: <path d="M8 1.2 14.3 4.6v6.8L8 14.8 1.7 11.4V4.6Z" {...stroke} />,
  };
  return <g transform={`translate(${x}, ${y})`}>{paths[role]}</g>;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
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
 *
 * The card is focusable (`tabIndex=0` on an SVG `<g>` with a role), so
 * the connection-tracing focus state is reachable by keyboard and not
 * hover-only.
 */
function NodeView({
  p,
  info,
  isTarget,
  inPath,
  dimmed,
  isActive,
  onActivate,
}: {
  p: Positioned;
  info: ComponentSizing | undefined;
  isTarget: boolean;
  /** Whole-path incident: soft halo on every node in the blast radius. */
  inPath: boolean;
  /** Focus is active elsewhere and this node is NOT in the focused subgraph. */
  dimmed: boolean;
  /** This is the pointed-at / focused node (accent stroke emphasis). */
  isActive: boolean;
  onActivate: (name: string | null) => void;
}) {
  const { node, x, y } = p;
  const severity = info ? sizingSeverity(info.status) : null;
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

  const halo = {
    x: x - 5,
    y: y - 5,
    width: NODE_W + 10,
    height: NODE_H + 10,
    rx: 14,
    fill: "none",
  };

  return (
    <g
      role="group"
      tabIndex={0}
      aria-label={tooltip}
      opacity={dimmed ? 0.22 : 1}
      style={{ transition: "opacity 160ms ease" }}
      className="outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      onMouseEnter={() => onActivate(node.name)}
      onMouseLeave={() => onActivate(null)}
      onFocus={() => onActivate(node.name)}
      onBlur={() => onActivate(null)}
    >
      <title>{tooltip}</title>
      {isTarget ? (
        <>
          {/* soft halo */}
          <rect
            {...halo}
            className={SURFACE.accentStroke}
            strokeOpacity={0.16}
            strokeWidth={4}
          />
          {/* rotating dashed ring: the incident is "locked on" this node */}
          <rect
            {...halo}
            className={`${SURFACE.accentStroke} eleven-target-ring`}
            strokeWidth={1.4}
            strokeDasharray="5 7"
          />
        </>
      ) : inPath ? (
        // blast radius, not the victim: halo only, no ring
        <rect
          {...halo}
          className={SURFACE.accentStroke}
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
        className={`${severity ? SEVERITY_FILL_SOFT[severity] : SURFACE.card} ${
          isActive
            ? SURFACE.accentStroke
            : severity
              ? SEVERITY_STROKE[severity]
              : SURFACE.cardStroke
        }`}
        strokeOpacity={isActive ? 1 : severity ? 0.4 : 1}
        strokeWidth={isActive ? 1.6 : 1}
      />
      <rect
        x={x + 9}
        y={y + 9}
        width={28}
        height={28}
        rx={8}
        className={`${SURFACE.chip} ${SURFACE.chipStroke}`}
        strokeWidth={1}
      />
      <RoleGlyph
        role={node.role}
        x={x + 15}
        y={y + 15}
        strokeClass={severity ? SEVERITY_STROKE[severity] : SURFACE.dimStroke}
      />

      <text
        x={x + 46}
        y={y + 22}
        className={SURFACE.name}
        fontSize={12}
        fontFamily={MONO}
        fontWeight={600}
        letterSpacing="0.01em"
      >
        {truncate(node.name, 13)}
      </text>

      {severity && info ? (
        <>
          <text
            x={x + 46}
            y={y + 37}
            className={SEVERITY_FILL[severity]}
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
            className={SURFACE.dim}
            fontSize={10}
            fontFamily={MONO}
          >
            {fmtPct(info.mean_utilization, 0)}
          </text>
          <rect
            x={x + 9}
            y={y + 45}
            width={trackW}
            height={4}
            rx={2}
            className={SURFACE.track}
            fillOpacity={0.7}
          />
          {util > 0 ? (
            <rect
              x={x + 9}
              y={y + 45}
              width={Math.max(4, trackW * Math.min(1, util))}
              height={4}
              rx={2}
              className={SEVERITY_FILL[severity]}
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
 *
 * The SVG never scales below its natural size — it scrolls horizontally
 * instead. A `viewBox` alone made a wide graph on a phone shrink 12px
 * labels to roughly 4px, i.e. a picture of a diagram rather than a
 * diagram.
 */
export function TopologyDiagram({ nodes, edges, sizing, targetNames, wholePath }: Props) {
  const [active, setActive] = useState<string | null>(null);
  const positioned = layout(nodes, edges);
  if (positioned.length === 0) return null;

  const { width, height } = canvasSize(positioned);

  const flagged = new Set(targetNames);
  const edgeGeoms = computeEdges(positioned, edges);

  const metaOf = new Map(edges.map((e) => [edgeKey(e), e] as const));
  const colOf = new Map(positioned.map((p) => [p.node.name, p.col] as const));

  // Focus tracing: pointing at (or tabbing to) a node keeps it, its direct
  // neighbors, and the edges between them at full strength and fades
  // everything else — the direct answer to "what is connected to what" in
  // a dense graph.
  const focusEdges = new Set<string>();
  const focusNodes = new Set<string>();
  if (active) {
    focusNodes.add(active);
    for (const e of edges) {
      if (e.source === active || e.target === active) {
        focusEdges.add(edgeKey(e));
        focusNodes.add(e.source);
        focusNodes.add(e.target);
      }
    }
  }

  return (
    <div>
      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          style={{ minWidth: width, maxWidth: "100%" }}
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
            const lit = !active || focusEdges.has(g.key);
            const emphasized = active !== null && focusEdges.has(g.key);
            const d = edgePath(g);
            return (
              <g
                key={g.key}
                opacity={lit ? (long ? 0.55 : 1) : 0.08}
                style={{ transition: "opacity 160ms ease" }}
              >
                <title>
                  {meta ? `${meta.source} → ${meta.target} · p=${meta.probability}` : g.key}
                </title>
                <path
                  d={d}
                  fill="none"
                  className={emphasized ? SURFACE.accentStroke : SURFACE.edge}
                  strokeOpacity={emphasized ? 0.9 : 1}
                  strokeWidth={emphasized ? 2 : long ? 1.1 : 1.5}
                />
                <path
                  d={d}
                  fill="none"
                  className={`${SURFACE.accentStroke} eleven-edge-flow`}
                  strokeOpacity={emphasized ? 0.8 : long ? 0.25 : 0.45}
                  strokeWidth={1.5}
                  strokeLinecap="round"
                  strokeDasharray="4 10"
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
                dimmed={active !== null && !focusNodes.has(name)}
                isActive={active === name}
                onActivate={setActive}
              />
            );
          })}
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-ink-dim">
        {(["right_sized", "oversized", "undersized"] as const).map((status) => {
          const sev = sizingSeverity(status);
          return (
            <span key={status} className="flex items-center gap-1.5">
              <span
                className={`inline-block h-2.5 w-2.5 rounded-[4px] border ${SEVERITY_BORDER[sev]} ${SEVERITY_BG[sev]}`}
              />
              {status.replace("_", "-")}
            </span>
          );
        })}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-4 rounded-[4px] border border-dashed border-accent" />
          incident target
        </span>
        <span className="ml-auto hidden font-mono text-[10px] sm:inline">
          bar = mean utilization · hover or tab to a node to trace its connections
        </span>
      </div>
    </div>
  );
}
