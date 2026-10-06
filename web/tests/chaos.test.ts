import { describe, expect, it } from "vitest";
import { chaosWindows } from "@/core/lib/chaos";
import type { ChaosEventCfg } from "@/core/types";

function event(overrides: Partial<ChaosEventCfg> = {}): ChaosEventCfg {
  return {
    event_type: "component_failure",
    intensity: 0.5,
    start_time: 0,
    interval: 10,
    duration: 4,
    ...overrides,
  };
}

describe("chaosWindows", () => {
  it("repeats an event every interval until the horizon", () => {
    const windows = chaosWindows([event({ start_time: 5, interval: 10, duration: 3 })], 30);
    expect(windows).toEqual([
      { start: 5, end: 8, event_type: "component_failure", intensity: 0.5 },
      { start: 15, end: 18, event_type: "component_failure", intensity: 0.5 },
      { start: 25, end: 28, event_type: "component_failure", intensity: 0.5 },
    ]);
  });

  it("defaults the window length to the gap, matching the engine", () => {
    const windows = chaosWindows([event({ start_time: 0, interval: 20, duration: null })], 40);
    expect(windows.map((w) => [w.start, w.end])).toEqual([
      [0, 20],
      [20, 40],
    ]);
  });

  it("clamps the last window to the horizon", () => {
    const windows = chaosWindows([event({ start_time: 55, interval: 60, duration: 30 })], 60);
    expect(windows).toHaveLength(1);
    expect(windows[0].end).toBe(60);
  });

  it("drops events that start at or after the horizon", () => {
    expect(chaosWindows([event({ start_time: 60 })], 60)).toEqual([]);
    expect(chaosWindows([event({ start_time: 99 })], 60)).toEqual([]);
  });

  it("merges and sorts windows from several events", () => {
    const windows = chaosWindows(
      [
        event({ event_type: "cache_outage", start_time: 20, interval: 100, duration: 5 }),
        event({ event_type: "network_latency", start_time: 5, interval: 100, duration: 5 }),
      ],
      60,
    );
    expect(windows.map((w) => w.event_type)).toEqual(["network_latency", "cache_outage"]);
  });

  // Regression: `for (let t = start; t < horizon; t += interval)` never
  // advances when interval <= 0, which froze the browser tab. The data is
  // engine-response / imported-file data, so it is not trusted to be sane.
  it("does not hang on a non-positive interval", () => {
    const zero = chaosWindows([event({ interval: 0, duration: 5 })], 60);
    expect(zero).toEqual([
      { start: 0, end: 5, event_type: "component_failure", intensity: 0.5 },
    ]);

    const negative = chaosWindows([event({ interval: -10, duration: 5 })], 60);
    expect(negative).toHaveLength(1);
  });

  it("does not hang on non-finite numbers", () => {
    expect(chaosWindows([event({ interval: Number.NaN })], 60)).toHaveLength(1);
    expect(chaosWindows([event({ interval: Number.POSITIVE_INFINITY })], 60)).toHaveLength(1);
    expect(chaosWindows([event({ start_time: Number.NaN, interval: 25 })], 60)).toHaveLength(3);
    expect(chaosWindows([event()], Number.NaN)).toEqual([]);
  });

  it("caps a pathologically dense schedule instead of allocating forever", () => {
    const windows = chaosWindows([event({ interval: 0.0001, duration: 0.0001 })], 10_000);
    expect(windows.length).toBeLessThanOrEqual(512);
  });

  it("returns nothing for an empty schedule", () => {
    expect(chaosWindows([], 60)).toEqual([]);
  });
});
