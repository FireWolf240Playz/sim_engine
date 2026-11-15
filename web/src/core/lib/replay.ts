/**
 * 3.4 — pointing at a moment of a finished run.
 *
 * Replay is the 3.2 playback over a run that has ended: the same clock, the
 * same frame, a scrubber on top. These are the scrubber's helpers. Nothing
 * here calls the engine; replay never simulates again.
 *
 * Pure and dependency-free: no React, no DOM.
 */
import type { Playback } from "./playback";
import type { FeedKind, FeedLine } from "../types";

/** A moment worth jumping to, drawn as a mark on the scrubber's track. */
export interface ReplayMark {
  t: number;
  kind: FeedKind;
  text: string;
}

/** The next tick after the clock (or the last one before it), paused; stays put at either end. */
export function stepTick(
  playback: Playback,
  ticks: ReadonlyArray<{ time: number }>,
  direction: 1 | -1,
): Playback {
  let target: number | undefined;
  if (direction === 1) {
    target = ticks.find((tick) => tick.time > playback.position)?.time;
  } else {
    for (let i = ticks.length - 1; i >= 0; i -= 1) {
      if (ticks[i].time < playback.position) {
        target = ticks[i].time;
        break;
      }
    }
  }
  return { ...playback, position: target ?? playback.position, playing: false };
}

/** Every incident's start, and the first failure, in order. */
export function replayMarks(lines: readonly FeedLine[]): ReplayMark[] {
  const marks: ReplayMark[] = [];
  let failed = false;
  for (const line of lines) {
    const firstFailure = line.kind === "failures" && !failed;
    if (line.kind === "incident_start" || firstFailure) {
      if (firstFailure) failed = true;
      marks.push({ t: line.t, kind: line.kind, text: line.text });
    }
  }
  return marks;
}

/**
 * "Where it broke": the first failure; without one, the first saturation;
 * without that, the first incident; `null` when nothing broke.
 */
export function breakingMoment(lines: readonly FeedLine[]): number | null {
  for (const kind of ["failures", "saturated", "incident_start"] as const) {
    const line = lines.find((candidate) => candidate.kind === kind);
    if (line) return line.t;
  }
  return null;
}

/** Where `position` sits along a scrubber of `duration` seconds, 0–1. */
export function positionFraction(position: number, duration: number): number {
  if (!(duration > 0)) return 0;
  return Math.min(1, Math.max(0, position / duration));
}
