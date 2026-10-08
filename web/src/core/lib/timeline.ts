import type { TimelineBandTick, TimelineTick } from "../types";

/** A timeline tick with the multi-seed P95 range attached, as Recharts'
 * range `Area` wants it: `[low, high]`, or `null` to gap. */
export type BandedTick = TimelineTick & { p95_band: [number, number] | null };

/**
 * Attach the cross-seed P95 range to each tick, matched by `time`. Ticks
 * without a band entry (single runs, or a tick no seed completed a request
 * in) get `null`, so the chart gaps instead of drawing a fake range.
 */
export function withBand(
  ticks: TimelineTick[],
  band: TimelineBandTick[] | null | undefined,
): BandedTick[] {
  const byTime = new Map((band ?? []).map((b) => [b.time, b]));
  return ticks.map((tick) => {
    const b = byTime.get(tick.time);
    const range: [number, number] | null =
      b && b.p95_min !== null && b.p95_max !== null ? [b.p95_min, b.p95_max] : null;
    return { ...tick, p95_band: range };
  });
}
