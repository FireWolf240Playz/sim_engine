/**
 * Contract for roadmap 3.2 (`.agents/tasks/3.2-live-view.md`): the playback
 * clock.
 *
 * Measured 2026-11-15: a 120 s simulated run reaches the browser in under a
 * second (60 ticks, 0.09 s of engine time), so "watching it live" cannot
 * mean drawing ticks as they arrive: they arrive at once. The view plays
 * the ticks it holds against a clock of its own, never past the newest tick
 * received (the live edge). On a run large enough to take real time, the
 * clock catches the live edge and waits there. One model serves both.
 *
 * Pure: the component owns the `requestAnimationFrame` loop and calls
 * `advance` with the milliseconds that passed.
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import {
  PLAYBACK_SPEEDS,
  TARGET_PLAY_SECONDS,
  advance,
  atLiveEdge,
  defaultSpeed,
  frameIndex,
  liveEdge,
  seek,
  setSpeed,
  startPlayback,
  togglePlay,
  type Playback,
} from "@/core/lib/playback";
import { TICKS } from "./fixtures/runStream";

const playing = (position: number, speed = 5): Playback => ({ position, playing: true, speed });
const paused = (position: number, speed = 5): Playback => ({ position, playing: false, speed });

describe("speed", () => {
  it("offers five speeds and aims at a twelve-second show", () => {
    expect([...PLAYBACK_SPEEDS]).toEqual([1, 2, 5, 10, 20]);
    expect(TARGET_PLAY_SECONDS).toBe(12);
  });

  it("picks the slowest speed that plays the run within the target", () => {
    expect(defaultSpeed(6)).toBe(1);
    expect(defaultSpeed(12)).toBe(1);
    expect(defaultSpeed(20)).toBe(2);
    expect(defaultSpeed(60)).toBe(5); // the demo: 12 s
    expect(defaultSpeed(120)).toBe(10); // the examples: 12 s
    expect(defaultSpeed(240)).toBe(20);
  });

  it("uses the fastest speed for a run too long for the target", () => {
    expect(defaultSpeed(3600)).toBe(20);
  });

  it("changes to a listed speed and ignores any other", () => {
    expect(setSpeed(playing(4), 10)).toEqual(playing(4, 10));
    const same = playing(4);
    expect(setSpeed(same, 3)).toBe(same);
    expect(setSpeed(same, 5)).toBe(same);
  });
});

describe("startPlayback", () => {
  it("starts at zero, playing, at the run's default speed", () => {
    expect(startPlayback(60)).toEqual({ position: 0, playing: true, speed: 5 });
    expect(startPlayback(120)).toEqual({ position: 0, playing: true, speed: 10 });
  });
});

describe("liveEdge", () => {
  it("is the time of the newest tick", () => {
    expect(liveEdge([])).toBe(0);
    expect(liveEdge(TICKS)).toBe(10);
    expect(liveEdge(TICKS.slice(0, 3))).toBe(4);
  });
});

describe("advance", () => {
  it("moves by the time that passed, times the speed", () => {
    expect(advance(playing(0), 1000, 10, false)).toEqual(playing(5));
    expect(advance(playing(2, 2), 500, 10, false)).toEqual(playing(3, 2));
  });

  it("does not pass the live edge of a run still arriving, and keeps playing", () => {
    const next = advance(playing(8), 1000, 10, false);
    expect(next).toEqual(playing(10));
  });

  it("stops at the end of a finished run", () => {
    expect(advance(playing(8), 1000, 10, true)).toEqual(paused(10));
  });

  it("returns the same object when nothing moves", () => {
    // The loop runs every frame; a new object each time would re-render
    // the page sixty times a second while it waits for data.
    const waiting = playing(10);
    expect(advance(waiting, 16, 10, false)).toBe(waiting);
    const stopped = paused(4);
    expect(advance(stopped, 16, 10, false)).toBe(stopped);
    const still = playing(4);
    expect(advance(still, 0, 10, false)).toBe(still);
    expect(advance(still, -5, 10, false)).toBe(still);
    expect(advance(still, Number.NaN, 10, false)).toBe(still);
  });

  it("resumes when more ticks arrive", () => {
    const waiting = playing(10);
    expect(advance(waiting, 200, 14, false)).toEqual(playing(11));
  });

  it("plays the demo run in about twelve seconds", () => {
    let playback = startPlayback(60);
    let frames = 0;
    while (playback.playing && frames < 10_000) {
      playback = advance(playback, 16, 58, true);
      frames += 1;
    }
    expect(playback.position).toBe(58);
    expect((frames * 16) / 1000).toBeGreaterThan(11);
    expect((frames * 16) / 1000).toBeLessThan(12.1);
  });
});

describe("seek", () => {
  it("jumps to a moment and pauses there", () => {
    expect(seek(playing(2), 7, 10)).toEqual(paused(7));
  });

  it("stays inside what has been received", () => {
    expect(seek(playing(2), 99, 10)).toEqual(paused(10));
    expect(seek(playing(2), -3, 10)).toEqual(paused(0));
  });
});

describe("togglePlay", () => {
  it("pauses and resumes", () => {
    expect(togglePlay(playing(4), 10, true)).toEqual(paused(4));
    expect(togglePlay(paused(4), 10, true)).toEqual(playing(4));
  });

  it("replays a finished run from the start", () => {
    expect(togglePlay(paused(10), 10, true)).toEqual(playing(0));
  });

  it("goes back to following a run that is still arriving", () => {
    expect(togglePlay(paused(10), 10, false)).toEqual(playing(10));
  });
});

describe("atLiveEdge", () => {
  it("is true when the clock has caught up with the newest tick", () => {
    expect(atLiveEdge(playing(10), 10)).toBe(true);
    expect(atLiveEdge(playing(9.9), 10)).toBe(false);
    expect(atLiveEdge(playing(0), 0)).toBe(true);
  });
});

describe("frameIndex", () => {
  it("is the newest tick at or before the position", () => {
    expect(frameIndex(TICKS, 0)).toBe(0);
    expect(frameIndex(TICKS, 1.9)).toBe(0);
    expect(frameIndex(TICKS, 2)).toBe(1);
    expect(frameIndex(TICKS, 9.99)).toBe(4);
    expect(frameIndex(TICKS, 10)).toBe(5);
    expect(frameIndex(TICKS, 99)).toBe(5);
  });

  it("is -1 when no tick has been reached", () => {
    expect(frameIndex([], 5)).toBe(-1);
    expect(frameIndex(TICKS.slice(1), 1)).toBe(-1);
  });
});
