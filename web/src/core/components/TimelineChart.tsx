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
import type { ChaosWindow, TimelineTick } from "../types";
import { usePrefersReducedMotion } from "../lib/useReducedMotion";
import { useTheme } from "../state/ThemeContext";

const MONO = "IBM Plex Mono, ui-monospace, monospace";

/** Recharts paints with hex strings, so each theme gets its own palette. */
const PALETTES = {
  light: {
    grid: "#e4e7ee",
    tick: "#667085",
    axis: "#e4e7ee",
    tooltipBg: "#ffffff",
    tooltipBorder: "#e4e7ee",
    tooltipInk: "#171a21",
    tooltipDim: "#667085",
    p50: "#667085",
    p95: "#6d5df0",
    sla: "#667085",
    chaos: "#d92d20",
  },
  dark: {
    grid: "#26334d",
    tick: "#8b96ac",
    axis: "#26334d",
    tooltipBg: "#151d2e",
    tooltipBorder: "#26334d",
    tooltipInk: "#e8edf5",
    tooltipDim: "#8b96ac",
    p50: "#8b96ac",
    p95: "#8b7bff",
    sla: "#8b96ac",
    chaos: "#e5484d",
  },
} as const;

/** Round a positive value UP to a "nice" axis bound (1/1.5/2/2.5/3/4/5/7.5 × 10ⁿ). */
function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const base = Math.pow(10, Math.floor(Math.log10(value)));
  const fraction = value / base;
  const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
  return (steps.find((step) => fraction <= step) ?? 10) * base;
}

interface Props {
  ticks: TimelineTick[];
  slaTarget: number | null | undefined;
  windows: ChaosWindow[];
  horizon: number;
}

/**
 * Latency percentiles and chaos windows on the SAME axis — cause and
 * effect in one glance. The single deliberate motion moment is the line
 * sweep when a new run lands (disabled under prefers-reduced-motion).
 */
export function TimelineChart({ ticks, slaTarget, windows, horizon }: Props) {
  const reducedMotion = usePrefersReducedMotion();
  const { theme } = useTheme();
  const pal = PALETTES[theme];
  const animate = !reducedMotion && ticks.length > 1;

  // The Y domain must always cover the SLA line — auto-scaling to the data
  // alone pushes a 10 s SLA off-screen when latency stays near 8 s.
  const dataMax = ticks.reduce(
    (max, t) => Math.max(max, t.p50_latency ?? 0, t.p95_latency ?? 0),
    0,
  );
  const top = niceCeil(Math.max(slaTarget ?? 0, dataMax, 1) * 1.1);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-ink-dim">
        <span className="flex items-center gap-2">
          <span className="h-0.5 w-5 rounded-full bg-accent" /> P95 latency
        </span>
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

      <div className="h-[280px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={ticks} margin={{ top: 16, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={pal.grid} strokeOpacity={theme === "light" ? 0.9 : 0.5} />
            <XAxis
              dataKey="time"
              type="number"
              domain={[0, Math.max(horizon, 1)]}
              tickFormatter={(t: number) => `${t}s`}
              tick={{ fill: pal.tick, fontSize: 11, fontFamily: MONO }}
              tickLine={false}
              axisLine={{ stroke: pal.axis }}
            />
            <YAxis
              width={48}
              domain={[0, top]}
              tickFormatter={(v: number) => `${v}s`}
              tick={{ fill: pal.tick, fontSize: 11, fontFamily: MONO }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              contentStyle={{
                background: pal.tooltipBg,
                border: `1px solid ${pal.tooltipBorder}`,
                borderRadius: 8,
                color: pal.tooltipInk,
                fontFamily: MONO,
                fontSize: 12,
                boxShadow: "0 4px 12px rgb(23 26 33 / 0.08)",
              }}
              labelStyle={{ color: pal.tooltipDim, fontFamily: MONO }}
              labelFormatter={(label: unknown) => `t = ${label}s`}
              formatter={(value: unknown, name: unknown) => [
                `${Number(value).toFixed(2)}s`,
                String(name ?? ""),
              ]}
            />

            {windows.map((w, i) => (
              <ReferenceArea
                key={`${w.start}-${w.event_type}-${i}`}
                x1={w.start}
                x2={w.end}
                fill={pal.chaos}
                fillOpacity={0.07}
                stroke={pal.chaos}
                strokeOpacity={0.35}
                strokeDasharray="3 3"
                label={{
                  value: w.event_type.replace(/_/g, " "),
                  position: "insideTop",
                  fill: pal.chaos,
                  fontSize: 10,
                  fontFamily: MONO,
                }}
              />
            ))}

            {slaTarget ? (
              <ReferenceLine y={slaTarget} stroke={pal.sla} strokeDasharray="5 4" strokeOpacity={0.8} />
            ) : null}

            <Area
              type="monotone"
              dataKey="p50_latency"
              name="P50"
              stroke={pal.p50}
              strokeWidth={1.5}
              fill={pal.p50}
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
              stroke={pal.p95}
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive={animate}
              animationDuration={1100}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
