"use client";

import { memo, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ComponentRole } from "../types";
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
  layoutKey,
} from "../lib/graphLayout";
import {
  nodeViews,
  type NodeViewModel,
  type TopologyDiagramProps,
} from "../lib/topologyView";

/** The memo props of one node card: flat view + position. */
type NodeBodyProps = NodeViewModel & { x: number; y: number };

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

/** The card's tooltip, from the view model alone (primitives only). */
function tooltipFor(view: NodeViewModel): string {
  const parts: string[] = [
    `${view.name} — ${view.role.replace(/_/g, " ")}, ×${view.capacity} capacity`,
  ];
  if (view.status !== null && view.meanUtil !== null) {
    parts.push(
      `${view.status.replace("_", "-")}: mean ${fmtPct(view.meanUtil, 0)} · p95 ${fmtPct(
        view.p95Util ?? 0,
        0,
      )} · p95 queue ${view.p95Queue ?? 0} · recommended ×${view.recommended ?? 0}`,
    );
  }
  if (view.isTarget) parts.push("incident target");
  return parts.join(" · ");
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
 *
 * 1.3e split (1.3f simplified): `NodeShell` is the plain,
 * state-carrying half (focus, hover, dim, the active emphasis overlay) and
 * re-renders freely; `NodeBody` is the `memo`'d picture that draws only
 * from flat view props + `x`, `y` — with default compare, so hover and
 * unrelated re-renders skip it, and a changed node redraws only itself.
 */
function NodeShell({
  view,
  x,
  y,
  dimmed,
  isActive,
  onActivate,
}: {
  view: NodeViewModel;
  x: number;
  y: number;
  /** Focus is active elsewhere and this node is NOT in the focused subgraph. */
  dimmed: boolean;
  /** This is the pointed-at / focused node (accent stroke emphasis). */
  isActive: boolean;
  onActivate: (name: string | null) => void;
}) {
  return (
    <g
      role="group"
      tabIndex={0}
      aria-label={tooltipFor(view)}
      opacity={dimmed ? 0.22 : 1}
      style={{ transition: "opacity 160ms ease" }}
      className="outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      onMouseEnter={() => onActivate(view.name)}
      onMouseLeave={() => onActivate(null)}
      onFocus={() => onActivate(view.name)}
      onBlur={() => onActivate(null)}
    >
      <NodeBody {...view} x={x} y={y} />
      {isActive ? (
        // emphasis overlay: same box, the accent stroke that today changes
        // the card's own stroke — kept here so the memo'd body never sees it
        <rect
          x={x}
          y={y}
          width={NODE_W}
          height={NODE_H}
          rx={10}
          fill="none"
          className={SURFACE.accentStroke}
          strokeWidth={1.6}
        />
      ) : null}
    </g>
  );
}

/** The drawable half of the card: halos, ring, card, chip, glyph, texts, bar. */
export const NodeBody = memo(function NodeBody({ x, y, ...view }: NodeBodyProps) {
  const severity = view.status ? sizingSeverity(view.status) : null;
  const util = view.meanUtil ?? 0;
  const trackW = NODE_W - 18;
  // 1.3g — this node's size moved since the numbers were measured. The
  // old size's tint, word and bar must not pass as current: neutral card,
  // dashed accent stroke, and the card says what it is (or isn't).
  const remeasuring = view.remeasure !== null;

  const halo = {
    x: x - 5,
    y: y - 5,
    width: NODE_W + 10,
    height: NODE_H + 10,
    rx: 14,
    fill: "none",
  };

  return (
    <>
      <title>{tooltipFor(view)}</title>
      {view.isTarget ? (
        <>
          {/* soft halo */}
          <rect {...halo} className={SURFACE.accentStroke} strokeOpacity={0.16} strokeWidth={4} />
          {/* rotating dashed ring: the incident is "locked on" this node */}
          <rect
            {...halo}
            className={`${SURFACE.accentStroke} eleven-target-ring`}
            strokeWidth={1.4}
            strokeDasharray="5 7"
          />
        </>
      ) : view.inPath ? (
        // blast radius, not the victim: halo only, no ring
        <rect {...halo} className={SURFACE.accentStroke} strokeOpacity={0.1} strokeWidth={3} />
      ) : null}

      <rect
        x={x}
        y={y}
        width={NODE_W}
        height={NODE_H}
        rx={10}
        className={remeasuring ? `${SURFACE.card} stroke-accent` : `${severity ? SEVERITY_FILL_SOFT[severity] : SURFACE.card} ${
          severity ? SEVERITY_STROKE[severity] : SURFACE.cardStroke
        }`}
        strokeOpacity={remeasuring ? 0.8 : severity ? 0.4 : 1}
        strokeWidth={1}
        strokeDasharray={remeasuring ? "4 4" : undefined}
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
        role={view.role}
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
        {truncate(view.name, 13)}
      </text>

      {remeasuring ? (
        // no size to speak of yet: track stays, the fill does not
        <>
          <text
            x={x + 46}
            y={y + 37}
            className={SURFACE.dim}
            fontSize={8}
            fontWeight={600}
            letterSpacing="0.14em"
            fontFamily="Inter, system-ui, sans-serif"
          >
            {view.remeasure === "running" ? "MEASURING…" : "NOT MEASURED"}
          </text>
          <text
            x={x + NODE_W - 9}
            y={y + 37}
            textAnchor="end"
            className={SURFACE.dim}
            fontSize={10}
            fontFamily={MONO}
          >
            —
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
        </>
      ) : severity ? (
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
            {view.status?.replace("_", "-").toUpperCase()}
          </text>
          <text
            x={x + NODE_W - 9}
            y={y + 37}
            textAnchor="end"
            className={SURFACE.dim}
            fontSize={10}
            fontFamily={MONO}
          >
            {fmtPct(view.meanUtil ?? 0, 0)}
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
    </>
  );
});

interface EdgeViewProps {
  d: string;
  title: string;
  /** Spans more than one column — drawn quieter and thinner. */
  long: boolean;
  /** Not dimmed by an active focus trace. */
  lit: boolean;
  /** Part of the focused subgraph — accent, not edge grey. */
  emphasized: boolean;
}

/** One flow line: quiet base line + slow accent dash drifting source → target. */
export function EdgeView({ d, title, long, lit, emphasized }: EdgeViewProps) {
  return (
    <g opacity={lit ? (long ? 0.55 : 1) : 0.08} style={{ transition: "opacity 160ms ease" }}>
      <title>{title}</title>
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
 *
 * 1.3f: plain `memo` — unchanged result parts keep their identity
 * (`runResult.ts`), so a fresh-but-equal response or an unrelated page
 * re-render leaves the whole diagram alone by default compare. Hover is
 * this component's own state: it re-renders the shells and edges, and
 * `NodeBody` skips. Geometry is cached under `layoutKey` because it changes
 * exactly when the node names/order or the valid edge pairs change.
 */
export const TopologyDiagram = memo(function TopologyDiagram({
  nodes,
  edges,
  sizing,
  targetNames,
  wholePath,
  stale,
}: TopologyDiagramProps) {
  const [active, setActive] = useState<string | null>(null);

  const key = layoutKey(nodes, edges);
  const geometry = useMemo(
    () => {
      const positioned = layout(nodes, edges);
      if (positioned.length === 0) return null;
      const { width, height } = canvasSize(positioned);
      // Positions cached by NAME, never the node object: a cached
      // `Positioned.node` would show the old capacity after a downsize.
      const positions = new Map<string, { x: number; y: number; col: number }>();
      for (const p of positioned) positions.set(p.node.name, { x: p.x, y: p.y, col: p.col });
      return { width, height, positions, edgeGeoms: computeEdges(positioned, edges) };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` changes exactly when layout()/computeEdges() could return different geometry (node names + order, valid edge pairs); capacity, role, service time and probability are deliberately not in it, so a resize keeps the cached positions, and positions are cached by name so no stale node object is ever served.
    [key],
  );
  if (geometry === null) return null;
  const { width, height, positions, edgeGeoms } = geometry;

  const views = nodeViews(nodes, sizing, targetNames, wholePath, stale);
  const viewOf = new Map(views.map((v) => [v.name, v] as const));
  const metaOf = new Map(edges.map((e) => [edgeKey(e), e] as const));

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
          {/* edges first, so nodes sit on top of the lines */}
          {edgeGeoms.map((g) => {
            const meta = metaOf.get(g.key);
            const long = meta
              ? Math.abs(
                  (positions.get(meta.target)?.col ?? 0) - (positions.get(meta.source)?.col ?? 0),
                ) > 1
              : false;
            const lit = !active || focusEdges.has(g.key);
            const emphasized = active !== null && focusEdges.has(g.key);
            return (
              <EdgeView
                key={g.key}
                d={edgePath(g)}
                title={meta ? `${meta.source} → ${meta.target} · p=${meta.probability}` : g.key}
                long={long}
                lit={lit}
                emphasized={emphasized}
              />
            );
          })}

          {Array.from(positions.keys()).map((name) => {
            const view = viewOf.get(name);
            if (!view) return null;
            const pos = positions.get(name);
            if (!pos) return null;
            return (
              <NodeShell
                key={name}
                view={view}
                x={pos.x}
                y={pos.y}
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
});
