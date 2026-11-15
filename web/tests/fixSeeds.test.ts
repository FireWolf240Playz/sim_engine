/**
 * 1.3i — the Fix-it preview names every seed the plan was verified on.
 *
 * Before: a 5-seed run showed "Measured by simulating the fixed config on
 * seed 44" — one seed, the typical one, which moved after the apply, so the
 * re-run never matched. Now the plan is verified on all five and says so.
 */
import { describe, expect, it } from "vitest";
import { measuredOn } from "../src/core/lib/fixHistory";
import type { FixOutcome } from "../src/core/types";

function outcome(extra: Partial<FixOutcome>): FixOutcome {
  return {
    seed: 42,
    score_before: 60,
    score_after: 97,
    band_before: "At risk",
    band_after: "Resilient",
    resolved: [],
    unfixed: [],
    monthly_delta: null,
    ...extra,
  };
}

describe("measuredOn", () => {
  it("names a single seed", () => {
    expect(measuredOn(outcome({ seed: 44, seeds: [44] }))).toBe("seed 44");
  });

  it("names a run of consecutive seeds as a range", () => {
    expect(measuredOn(outcome({ seeds: [42, 43, 44, 45, 46] }))).toBe("seeds 42–46");
  });

  it("lists seeds that are not consecutive", () => {
    expect(measuredOn(outcome({ seed: 101, seeds: [101, 202, 303] }))).toBe("seeds 101, 202, 303");
  });

  it("falls back to `seed` from an engine that predates `seeds`", () => {
    expect(measuredOn(outcome({ seed: 7 }))).toBe("seed 7");
  });
});
