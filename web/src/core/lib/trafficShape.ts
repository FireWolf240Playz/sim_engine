/**
 * 3.5b — the traffic-shape picker's maths.
 *
 * The picker previews the curve the engine will run, so the curve is
 * mirrored from `sim_core/config.py::shape_multiplier` and pinned with the
 * same numbers as `tests/test_traffic_shapes.py`. Do not "improve" a
 * formula here without changing the engine's.
 *
 * Pure and dependency-free: no React, no DOM.
 */
import type { TrafficConfig, TrafficShape } from "../types";

/** `sim_core/config.py::DEFAULT_SHAPE_PEAK`. */
export const DEFAULT_SHAPE_PEAK: Record<Exclude<TrafficShape, "flat">, number> = {
  ramp: 3,
  diurnal: 3,
  black_friday: 5,
};

export const TRAFFIC_SHAPES: ReadonlyArray<{ name: TrafficShape; label: string; blurb: string }> = [
  { name: "flat", label: "Flat", blurb: "The same rate from start to end." },
  { name: "ramp", label: "Ramp", blurb: "Climbs steadily to three times the quiet rate." },
  { name: "diurnal", label: "Business day", blurb: "Quiet at both ends, three times busier at midday." },
  { name: "black_friday", label: "Black Friday", blurb: "A normal day, then a surge to five times the rate." },
];

/** The base rate's multiplier at fraction `u` (0–1) of the traffic window. */
export function shapeMultiplier(shape: TrafficShape, u: number, peak: number): number {
  if (shape === "ramp") return 1 + (peak - 1) * u;
  if (shape === "diurnal") return 1 + (peak - 1) * Math.sin(Math.PI * u) ** 2;
  if (shape === "black_friday") {
    if (u < 0.4) return 1;
    if (u < 0.5) return 1 + ((peak - 1) * (u - 0.4)) / 0.1;
    if (u < 0.8) return peak;
    return peak - ((peak - 1) * (u - 0.8)) / 0.2;
  }
  return 1;
}

/** How many times the quiet rate the shape peaks at (1 for flat). */
export function shapePeak(traffic: TrafficConfig): number {
  const shape = traffic.shape ?? "flat";
  if (shape === "flat") return 1;
  return traffic.shape_peak ?? DEFAULT_SHAPE_PEAK[shape];
}

function spikeRate(traffic: TrafficConfig, t: number): number | null {
  const { spike_rps: rps, spike_start: start, spike_duration: length } = traffic;
  if (rps == null || start == null || length == null) return null;
  return start <= t && t < start + length ? rps : null;
}

/** The arrival rate at `t` (`TrafficPattern.rate_at`): zero outside the window, a spike overrides. */
export function rateAt(traffic: TrafficConfig, t: number): number {
  const start = traffic.start_time ?? 0;
  if (t < start || t >= start + traffic.duration) return 0;
  const spike = spikeRate(traffic, t);
  if (spike !== null) return spike;
  const shape = traffic.shape ?? "flat";
  if (shape === "flat") return traffic.base_rps;
  return traffic.base_rps * shapeMultiplier(shape, (t - start) / traffic.duration, shapePeak(traffic));
}

/** The busiest rate of the day: the shape's peak, or a higher spike. */
export function peakRps(traffic: TrafficConfig): number {
  return Math.max(traffic.base_rps * shapePeak(traffic), traffic.spike_rps ?? 0);
}

/**
 * The traffic block with `shape` (and `peak`, when given). Flat removes both
 * keys: the engine refuses a peak on a flat pattern, and going back to Flat
 * must give back exactly the block the demo's scores are calibrated on.
 */
export function withShape(traffic: TrafficConfig, shape: TrafficShape, peak?: number): TrafficConfig {
  const rest: TrafficConfig = { ...traffic };
  delete rest.shape;
  delete rest.shape_peak;
  if (shape === "flat") return rest;
  return peak === undefined ? { ...rest, shape } : { ...rest, shape, shape_peak: peak };
}

/** Points the preview draws. */
const SAMPLES = 48;

/**
 * The curve as an SVG path, 49 points left to right. `scaleMax` is the
 * multiplier at the top edge: give every shape the same one to draw them to
 * one scale (Black Friday then towers over a flat day).
 */
export function shapePath(
  shape: TrafficShape,
  peak: number,
  width: number,
  height: number,
  scaleMax: number = peak,
): string {
  const parts: string[] = [];
  for (let i = 0; i <= SAMPLES; i += 1) {
    const u = i / SAMPLES;
    const x = Number((u * width).toFixed(2));
    const y = Number((height * (1 - shapeMultiplier(shape, u, peak) / scaleMax)).toFixed(2));
    parts.push(`${i === 0 ? "M" : "L"}${x},${y}`);
  }
  return parts.join(" ");
}
