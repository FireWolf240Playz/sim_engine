/**
 * 3.2 — the playback clock: the run, played back at a watchable speed.
 *
 * Measured 2026-11-15: a 120 s simulated run reaches the browser in under a
 * second, so "watching it live" cannot mean drawing ticks as they arrive.
 * The view plays the ticks it holds against a clock of its own, never past
 * the newest tick received (the live edge). On a run slow enough to take
 * real time, the clock catches the live edge and waits there. One model
 * serves both, and replay (3.4) is the same clock with a scrubber.
 *
 * Pure: the component owns the `requestAnimationFrame` loop and calls
 * `advance` with the milliseconds that passed. Every function returns the
 * SAME object when nothing changes, so a `setState(advance(...))` in that
 * loop re-renders nothing while paused or waiting.
 */

export interface Playback {
  /** Simulated seconds. */
  position: number;
  playing: boolean;
  /** Simulated seconds per real second. */
  speed: number;
}

export const PLAYBACK_SPEEDS = [1, 2, 5, 10, 20] as const;

/** How long a whole run should take to play at its default speed. */
export const TARGET_PLAY_SECONDS = 12;

/** The slowest speed that plays a run of `duration` seconds within the target. */
export function defaultSpeed(duration: number): number {
  return (
    PLAYBACK_SPEEDS.find((speed) => duration / speed <= TARGET_PLAY_SECONDS) ??
    PLAYBACK_SPEEDS[PLAYBACK_SPEEDS.length - 1]
  );
}

/** A new run: from the start, playing, at its default speed. */
export function startPlayback(duration: number): Playback {
  return { position: 0, playing: true, speed: defaultSpeed(duration) };
}

/** The time of the newest tick received (0 before any). */
export function liveEdge(ticks: ReadonlyArray<{ time: number }>): number {
  return ticks.length ? ticks[ticks.length - 1].time : 0;
}

/**
 * The clock after `elapsedMs` of real time. It never passes `edge`; at the
 * edge of a finished run (`complete`) it stops, at the edge of a run still
 * arriving it keeps "playing" and waits for more ticks.
 */
export function advance(
  playback: Playback,
  elapsedMs: number,
  edge: number,
  complete: boolean,
): Playback {
  if (!playback.playing || !(elapsedMs > 0)) return playback;
  const position = Math.min(edge, playback.position + (elapsedMs / 1000) * playback.speed);
  const finished = complete && position >= edge;
  if (position === playback.position && !finished) return playback;
  return { ...playback, position, playing: !finished };
}

/** Jump to `t` (kept inside what has been received) and pause there. */
export function seek(playback: Playback, t: number, edge: number): Playback {
  return { ...playback, position: Math.min(edge, Math.max(0, t)), playing: false };
}

/**
 * Pause or play. Playing a finished run that is at its end replays it from
 * the start; playing a run still arriving follows it again.
 */
export function togglePlay(playback: Playback, edge: number, complete: boolean): Playback {
  if (playback.playing) return { ...playback, playing: false };
  if (complete && playback.position >= edge) return { ...playback, position: 0, playing: true };
  return { ...playback, playing: true };
}

/** Change to one of `PLAYBACK_SPEEDS`; any other value changes nothing. */
export function setSpeed(playback: Playback, speed: number): Playback {
  if (speed === playback.speed || !(PLAYBACK_SPEEDS as readonly number[]).includes(speed)) {
    return playback;
  }
  return { ...playback, speed };
}

/** The clock has caught up with the newest tick. */
export function atLiveEdge(playback: Playback, edge: number): boolean {
  return playback.position >= edge;
}

/** Index of the newest tick at or before `position`; -1 when none is reached. */
export function frameIndex(ticks: ReadonlyArray<{ time: number }>, position: number): number {
  // Ticks are in time order: a binary search, since this runs per frame.
  let lo = 0;
  let hi = ticks.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (ticks[mid].time <= position) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}
