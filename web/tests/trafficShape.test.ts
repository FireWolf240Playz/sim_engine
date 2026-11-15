/**
 * Contract for roadmap 3.5b (`.agents/tasks/3.5b-shape-picker.md`): the
 * traffic-shape picker's maths.
 *
 * The picker previews the curve the engine will run, so the curve is
 * mirrored here from `sim_core/config.py` and pinned with the same numbers
 * as `tests/test_traffic_shapes.py`. With `u` the fraction of the traffic
 * window elapsed and `P` the peak, the rate is `base_rps` times:
 *
 *   ramp          1 + (P - 1) u
 *   diurnal       1 + (P - 1) sin^2(pi u)
 *   black_friday  1 until u = 0.4, a straight climb to P at 0.5,
 *                 P until 0.8, a straight decline back to 1 at 1.0
 *
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import {
  DEFAULT_SHAPE_PEAK,
  TRAFFIC_SHAPES,
  peakRps,
  rateAt,
  shapeMultiplier,
  shapePath,
  shapePeak,
  withShape,
} from "@/core/lib/trafficShape";
import type { TrafficConfig, TrafficShape } from "@/core/types";

const traffic = (shape?: TrafficShape, extra: Partial<TrafficConfig> = {}): TrafficConfig => ({
  base_rps: 2,
  duration: 100,
  ...(shape ? { shape } : {}),
  ...extra,
});

function points(path: string): Array<[number, number]> {
  return path
    .split(/[ML]/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.split(",").map(Number) as [number, number]);
}

describe("the shapes on offer", () => {
  it("lists four, flat first", () => {
    expect(TRAFFIC_SHAPES.map((shape) => shape.name)).toEqual(["flat", "ramp", "diurnal", "black_friday"]);
    expect(TRAFFIC_SHAPES.map((shape) => shape.label)).toEqual(["Flat", "Ramp", "Business day", "Black Friday"]);
    expect(TRAFFIC_SHAPES.every((shape) => shape.blurb.length > 10)).toBe(true);
  });

  it("has the engine's default peaks", () => {
    expect(DEFAULT_SHAPE_PEAK).toEqual({ ramp: 3, diurnal: 3, black_friday: 5 });
  });
});

describe("rateAt", () => {
  // base 2 rps over 100 s, default peak: the table in test_traffic_shapes.py.
  it.each<[TrafficShape, number, number]>([
    ["ramp", 0, 2],
    ["ramp", 25, 3],
    ["ramp", 50, 4],
    ["ramp", 75, 5],
    ["diurnal", 0, 2],
    ["diurnal", 25, 4],
    ["diurnal", 50, 6],
    ["diurnal", 75, 4],
    ["black_friday", 0, 2],
    ["black_friday", 39, 2],
    ["black_friday", 45, 6],
    ["black_friday", 50, 10],
    ["black_friday", 79, 10],
    ["black_friday", 90, 6],
  ])("%s at %d s is %d rps", (shape, t, rate) => {
    expect(rateAt(traffic(shape), t)).toBeCloseTo(rate, 9);
  });

  it("is the base rate when there is no shape", () => {
    expect(rateAt(traffic(), 37)).toBe(2);
    expect(rateAt(traffic("flat"), 37)).toBe(2);
  });

  it("uses a chosen peak", () => {
    expect(rateAt(traffic("ramp", { shape_peak: 10 }), 50)).toBeCloseTo(11, 9);
    expect(rateAt(traffic("black_friday", { shape_peak: 2 }), 60)).toBeCloseTo(4, 9);
  });

  it("follows the traffic window, not the clock", () => {
    const late = traffic("ramp", { start_time: 40 });
    expect(rateAt(late, 40)).toBeCloseTo(2, 9);
    expect(rateAt(late, 90)).toBeCloseTo(4, 9);
  });

  it("is zero outside the window", () => {
    const late = traffic("diurnal", { start_time: 10 });
    expect(rateAt(late, 9.99)).toBe(0);
    expect(rateAt(late, 110)).toBe(0);
  });

  it("lets a spike override the curve", () => {
    const spiky = traffic("ramp", { spike_rps: 50, spike_start: 20, spike_duration: 10 });
    expect(rateAt(spiky, 25)).toBe(50);
    expect(rateAt(spiky, 30)).toBeCloseTo(2 * (1 + 2 * 0.3), 9);
  });
});

describe("shapeMultiplier", () => {
  it("is 1 for a flat day", () => {
    expect(shapeMultiplier("flat", 0.5, 3)).toBe(1);
  });

  it("bends Black Friday at 40%, 50% and 80% of the window", () => {
    expect(shapeMultiplier("black_friday", 0.4, 5)).toBeCloseTo(1, 9);
    expect(shapeMultiplier("black_friday", 0.5, 5)).toBeCloseTo(5, 9);
    expect(shapeMultiplier("black_friday", 0.8, 5)).toBeCloseTo(5, 9);
    expect(shapeMultiplier("black_friday", 1, 5)).toBeCloseTo(1, 9);
  });

  it("never drops below the quiet rate", () => {
    for (const { name } of TRAFFIC_SHAPES) {
      for (let u = 0; u <= 1; u += 0.05) expect(shapeMultiplier(name, u, 4)).toBeGreaterThanOrEqual(1 - 1e-9);
    }
  });
});

describe("the peak", () => {
  it("is the shape's default unless one is chosen", () => {
    expect(shapePeak(traffic())).toBe(1);
    expect(shapePeak(traffic("flat"))).toBe(1);
    expect(shapePeak(traffic("ramp"))).toBe(3);
    expect(shapePeak(traffic("black_friday"))).toBe(5);
    expect(shapePeak(traffic("ramp", { shape_peak: 10 }))).toBe(10);
    expect(shapePeak(traffic("ramp", { shape_peak: null }))).toBe(3);
  });

  it("is turned into the busiest rate of the day", () => {
    expect(peakRps(traffic())).toBe(2);
    expect(peakRps(traffic("black_friday"))).toBe(10);
    expect(peakRps(traffic("ramp", { spike_rps: 50, spike_start: 20, spike_duration: 10 }))).toBe(50);
    expect(peakRps(traffic("black_friday", { spike_rps: 4, spike_start: 20, spike_duration: 10 }))).toBe(10);
  });
});

describe("withShape", () => {
  it("sets the shape and leaves the peak to the engine's default", () => {
    expect(withShape(traffic(), "black_friday")).toEqual({ base_rps: 2, duration: 100, shape: "black_friday" });
  });

  it("sets a chosen peak", () => {
    expect(withShape(traffic(), "ramp", 4)).toEqual({ base_rps: 2, duration: 100, shape: "ramp", shape_peak: 4 });
  });

  it("drops the peak when the shape changes without one", () => {
    const shaped = withShape(traffic(), "ramp", 4);
    expect(withShape(shaped, "diurnal")).toEqual({ base_rps: 2, duration: 100, shape: "diurnal" });
  });

  it("goes back to exactly the config it started from", () => {
    // The engine refuses a peak on a flat pattern, and the demo's scores
    // are calibrated on a traffic block with no shape key at all.
    const original = traffic(undefined, { spike_rps: null });
    expect(withShape(withShape(original, "black_friday", 6), "flat")).toEqual(original);
  });

  it("never changes the traffic it was given", () => {
    const original = traffic();
    withShape(original, "ramp", 4);
    expect(original).toEqual({ base_rps: 2, duration: 100 });
  });
});

describe("shapePath", () => {
  it("draws a line from the left edge to the right", () => {
    const path = shapePath("ramp", 3, 120, 30);
    expect(path.startsWith("M")).toBe(true);
    expect(/^[ML0-9., -]+$/.test(path)).toBe(true);
    const drawn = points(path);
    expect(drawn).toHaveLength(49);
    expect(drawn[0][0]).toBe(0);
    expect(drawn[48][0]).toBe(120);
  });

  it("puts the peak at the top and the quiet rate a third of the way up", () => {
    const drawn = points(shapePath("ramp", 3, 120, 30));
    expect(drawn[0][1]).toBeCloseTo(20, 2); // 1/3 of the peak: 30 * (1 - 1/3)
    expect(drawn[48][1]).toBeCloseTo(0, 2);
  });

  it("draws every shape to one scale when given one", () => {
    // Side by side in the picker, Black Friday towers over a flat day.
    const flat = points(shapePath("flat", 1, 100, 20, 5));
    expect(new Set(flat.map(([, y]) => y))).toEqual(new Set([16]));
    const friday = points(shapePath("black_friday", 5, 100, 20, 5));
    expect(friday[0][1]).toBeCloseTo(16, 2);
    expect(Math.min(...friday.map(([, y]) => y))).toBeCloseTo(0, 2);
    const day = points(shapePath("diurnal", 3, 100, 20, 5));
    expect(Math.min(...day.map(([, y]) => y))).toBeCloseTo(8, 2); // 3/5 of the way up
    expect(day[24][1]).toBeCloseTo(8, 2);
  });

  it("writes short numbers", () => {
    const longest = Math.max(...shapePath("diurnal", 3, 120, 30).split(/[ML, ]/).map((n) => n.length));
    expect(longest).toBeLessThanOrEqual(6);
  });
});
