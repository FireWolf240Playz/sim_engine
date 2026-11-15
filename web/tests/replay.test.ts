/**
 * Contract for roadmap 3.4 (`.agents/tasks/3.4-timeline-replay.md`): pointing
 * at a moment of a finished run.
 *
 * Replay is the 3.2 playback over a run that has ended: the same clock, the
 * same frame, a scrubber on top. These are the scrubber's helpers: stepping
 * a tick at a time, the marks worth jumping to, and "where it broke".
 * Nothing here calls the engine; replay never simulates again.
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import type { Playback } from "@/core/lib/playback";
import { breakingMoment, positionFraction, replayMarks, stepTick } from "@/core/lib/replay";
import type { FeedLine } from "@/core/types";
import { LINES, TICKS } from "./fixtures/runStream";

const at = (position: number, isPlaying = false): Playback => ({ position, playing: isPlaying, speed: 5 });

describe("stepTick", () => {
  it("moves to the next tick and pauses", () => {
    expect(stepTick(at(5, true), TICKS, 1)).toEqual(at(6));
    expect(stepTick(at(4), TICKS, 1)).toEqual(at(6));
  });

  it("moves to the tick before", () => {
    expect(stepTick(at(5), TICKS, -1)).toEqual(at(4));
    expect(stepTick(at(4), TICKS, -1)).toEqual(at(2));
  });

  it("stops at both ends", () => {
    expect(stepTick(at(10), TICKS, 1)).toEqual(at(10));
    expect(stepTick(at(0), TICKS, -1)).toEqual(at(0));
    expect(stepTick(at(3), [], 1)).toEqual(at(3));
  });
});

describe("replayMarks", () => {
  it("marks each incident and the first failure", () => {
    expect(replayMarks(LINES)).toEqual([
      { t: 3, kind: "incident_start", text: "component failure on db: 50% of its capacity is gone" },
      { t: 6, kind: "failures", text: "2 requests failed" },
    ]);
  });

  it("marks the first failure only", () => {
    const more: FeedLine[] = [
      ...LINES,
      { t: 9, kind: "failures", severity: "crit", node: null, text: "1 request failed" },
      { t: 11, kind: "incident_start", severity: "crit", node: "lb", text: "component failure on lb: 50% of its capacity is gone" },
    ];
    expect(replayMarks(more).map((mark) => [mark.t, mark.kind])).toEqual([
      [3, "incident_start"],
      [6, "failures"],
      [11, "incident_start"],
    ]);
  });

  it("has nothing to mark on a quiet run", () => {
    expect(replayMarks([])).toEqual([]);
    expect(replayMarks(LINES.filter((line) => line.kind === "recovered"))).toEqual([]);
  });
});

describe("breakingMoment", () => {
  it("is the first failure", () => {
    expect(breakingMoment(LINES)).toBe(6);
  });

  it("falls back to the first saturation, then the first incident", () => {
    const noFailure = LINES.filter((line) => line.kind !== "failures");
    expect(breakingMoment(noFailure)).toBe(4);
    const incidentOnly = LINES.filter((line) => line.kind === "incident_start" || line.kind === "incident_end");
    expect(breakingMoment(incidentOnly)).toBe(3);
  });

  it("is null when nothing broke", () => {
    expect(breakingMoment([])).toBeNull();
    expect(breakingMoment(LINES.filter((line) => line.kind === "recovered"))).toBeNull();
  });
});

describe("positionFraction", () => {
  it("places a moment along the scrubber", () => {
    expect(positionFraction(3, 12)).toBe(0.25);
    expect(positionFraction(0, 12)).toBe(0);
    expect(positionFraction(12, 12)).toBe(1);
  });

  it("stays between the two ends", () => {
    expect(positionFraction(40, 12)).toBe(1);
    expect(positionFraction(-1, 12)).toBe(0);
    expect(positionFraction(5, 0)).toBe(0);
  });
});
