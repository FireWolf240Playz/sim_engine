"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChaosWindow, TimelineBandTick, TimelineTick } from "../types";
import { withBand } from "../lib/timeline";
import { usePrefersReducedMotion } from "../lib/useReducedMotion";
import { useThemeTokens } from "../lib/useThemeTokens";

const MONO = "IBM Plex Mono, ui-monospace, monospace";
const CHART_H = 280;

/** Round a positive value UP to a "nice" axis bound (1/1.5/2/2.5/3/4/5/7.5 × 10ⁿ). */
export function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const base = Math.pow(10, Math.floor(Math.log10(value)));
  const fraction = value / base;
  const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
  return (steps.find((step) => fraction <= step) ?? 10) * base;
}

/**
 * The Y domain must always cover the SLA line — auto-scaling to the data
 * alone pushes a 10 s SLA off-screen when latency stays near 8 s.
 */
export function yAxisTop(
  ticks: TimelineTick[],
  slaTarget: number | null | undefined,
  band?: TimelineBandTick[] | null,
): number {
  const dataMax = ticks.reduce(
    (max, t) => Math.max(max, t.p50_latency ?? 0, t.p95_latency ?? 0),
    0,
  );
  // The band's top edge is data too: a worst seed must not clip off-chart.
  const bandMax = (band ?? []).reduce((max, b) => Math.max(max, b.p95_max ?? 0), 0);
  return niceCeil(Math.max(slaTarget ?? 0, dataMax, bandMax, 1) * 1.1);
}

interface Props {
  ticks: TimelineTick[];
  slaTarget: number | null | undefined;
  windows: ChaosWindow[];
  horizon: number;
  /** Multi-seed runs: P95 range across every seed, drawn behind the line. */
  band?: TimelineBandTick[] | null;
  /** How many seeds the band spans (legend text). */
  seedCount?: number;
}

function fmtTooltip(value: unknown): string {
  if (Array.isArray(value)) {
    return `${Number(value[0]).toFixed(2)}–${Number(value[1]).toFixed(2)}s`;
  }
  return `${Number(value).toFixed(2)}s`;
}

/**
 * Latency percentiles and chaos windows on the SAME axis — cause and
 * effect in one glance. The single deliberate motion moment is the line
 * sweep when a new run lands (disabled under prefers-reduced-motion).
 *
 * Recharts needs resolved color strings rather than `var(...)`
 * references, so the palette comes from `useThemeTokens()`, which reads
 * the very tokens globals.css defines. The chart holds no hex values of
 * its own; the fixed-height wrapper means the one frame before the
 * tokens resolve costs no layout shift.
 */
export function TimelineChart({ ticks, slaTarget, windows, horizon, band, seedCount }: Props) {
  const reducedMotion = usePrefersReducedMotion();
  const tokens = useThemeTokens();
  const animate = !reducedMotion && ticks.length > 1;
  const top = yAxisTop(ticks, slaTarget, band);
  const data = withBand(ticks, band);
  const hasBand = data.some((t) => t.p95_band !== null);

  if (ticks.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-ink-dim"
        style={{ height: CHART_H }}
      >
        No timeline data came back for this run.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-ink-dim">
        <span className="flex items-center gap-2">
          <span className="h-0.5 w-5 rounded-full bg-accent" /> P95 latency
        </span>
        {hasBand ? (
          <span className="flex items-center gap-2">
            <span className="h-2.5 w-5 rounded-[3px] bg-accent/20" /> P95 range
            {seedCount ? ` across ${seedCount} seeds` : ""}
          </span>
        ) : null}
        <span className="flex items-center gap-2">
          <span className="h-0.5 w-5 rounded-full bg-ink-dim" /> P50 latency
        </span>
        <span className="flex items-center gap-2">
          <span className="h-2.5 w-5 rounded-[3px] border border-sev-crit/60 bg-sev-crit/10" /> chaos window
        </span>
        {slaTarget ? (
          <span className="flex items-center gap-2">
            <span className="h-0 w-5 border-t border-dashed border-ink-dim" /> SLA {slaTarget}s
          </span>
        ) : null}
      </div>

      <div className="w-full" style={{ height: CHART_H }}>
        {tokens ? (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 16, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid
                vertical={false}
                stroke={tokens.color["--color-line"]}
                strokeOpacity={tokens.number["--chart-grid-opacity"]}
              />
              <XAxis
                dataKey="time"
                type="number"
                domain={[0, Math.max(horizon, 1)]}
                tickFormatter={(t: number) => `${t}s`}
                tick={{ fill: tokens.color["--color-ink-dim"], fontSize: 11, fontFamily: MONO }}
                tickLine={false}
                axisLine={{ stroke: tokens.color["--color-line"] }}
              />
              <YAxis
                width={48}
                domain={[0, top]}
                tickFormatter={(v: number) => `${v}s`}
                tick={{ fill: tokens.color["--color-ink-dim"], fontSize: 11, fontFamily: MONO }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                contentStyle={{
                  background: tokens.color["--color-surface-1"],
                  border: `1px solid ${tokens.color["--color-line"]}`,
                  borderRadius: 8,
                  color: tokens.color["--color-ink"],
                  fontFamily: MONO,
                  fontSize: 12,
                  boxShadow: "0 4px 12px rgb(23 26 33 / 0.08)",
                }}
                labelStyle={{ color: tokens.color["--color-ink-dim"], fontFamily: MONO }}
                labelFormatter={(label: unknown) => `t = ${label}s`}
                formatter={(value: unknown, name: unknown) => [
                  fmtTooltip(value),
                  String(name ?? ""),
                ]}
              />

              {windows.map((w, i) => (
                <ReferenceArea
                  key={`${w.start}-${w.event_type}-${i}`}
                  x1={w.start}
                  x2={w.end}
                  fill={tokens.color["--color-sev-crit"]}
                  fillOpacity={0.07}
                  stroke={tokens.color["--color-sev-crit"]}
                  strokeOpacity={0.35}
                  strokeDasharray="3 3"
                  label={{
                    value: w.event_type.replace(/_/g, " "),
                    position: "insideTop",
                    fill: tokens.color["--color-sev-crit"],
                    fontSize: 10,
                    fontFamily: MONO,
                  }}
                />
              ))}

              {slaTarget ? (
                <ReferenceLine
                  y={slaTarget}
                  stroke={tokens.color["--color-ink-dim"]}
                  strokeDasharray="5 4"
                  strokeOpacity={0.8}
                />
              ) : null}

              {hasBand ? (
                <Area
                  type="monotone"
                  dataKey="p95_band"
                  name="P95 range"
                  stroke="none"
                  fill={tokens.color["--color-accent"]}
                  fillOpacity={0.14}
                  connectNulls
                  isAnimationActive={animate}
                  animationDuration={900}
                />
              ) : null}
              <Area
                type="monotone"
                dataKey="p50_latency"
                name="P50"
                stroke={tokens.color["--color-ink-dim"]}
                strokeWidth={1.5}
                fill={tokens.color["--color-ink-dim"]}
                fillOpacity={0.08}
                dot={false}
                connectNulls
                isAnimationActive={animate}
                animationDuration={900}
              />
              <Line
                type="monotone"
                dataKey="p95_latency"
                name="P95"
                stroke={tokens.color["--color-accent"]}
                strokeWidth={2}
                dot={false}
                connectNulls
                isAnimationActive={animate}
                animationDuration={1100}
              />
            </ComposedChart>
          </ResponsiveContainer>
        ) : null}
      </div>
    </div>
  );
}
