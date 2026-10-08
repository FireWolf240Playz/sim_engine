import { describe, expect, it } from "vitest";
import { yAxisTop } from "@/core/components/TimelineChart";
import { withBand } from "@/core/lib/timeline";
import type { TimelineBandTick, TimelineTick } from "@/core/types";

function tick(time: number, p95: number | null): TimelineTick {
  return { time, p50_latency: p95, p95_latency: p95, sla_met: null, per_component: {} };
}

describe("withBand", () => {
  const ticks = [tick(1, 2), tick(2, null), tick(3, 3)];

  it("attaches the cross-seed P95 range by time", () => {
    const band: TimelineBandTick[] = [
      { time: 1, p95_min: 1.5, p95_max: 4 },
      { time: 2, p95_min: null, p95_max: null },
      { time: 3, p95_min: 2, p95_max: 6 },
    ];
    expect(withBand(ticks, band).map((t) => t.p95_band)).toEqual([[1.5, 4], null, [2, 6]]);
  });

  it("gives every tick a null band for single runs", () => {
    expect(withBand(ticks, undefined).every((t) => t.p95_band === null)).toBe(true);
    expect(withBand(ticks, null).every((t) => t.p95_band === null)).toBe(true);
  });

  it("keeps the tick fields and never mutates the input", () => {
    const before = structuredClone(ticks);
    const out = withBand(ticks, [{ time: 1, p95_min: 1, p95_max: 2 }]);
    expect(ticks).toEqual(before);
    expect(out[0]).toEqual({ ...ticks[0], p95_band: [1, 2] });
    expect(out[2].p95_band).toBeNull();
  });
});

describe("yAxisTop with a band", () => {
  it("covers the worst seed, not only the typical line", () => {
    const ticks = [tick(1, 2)];
    expect(yAxisTop(ticks, null)).toBe(2.5);
    expect(yAxisTop(ticks, null, [{ time: 1, p95_min: 1, p95_max: 9 }])).toBe(10);
  });
});
