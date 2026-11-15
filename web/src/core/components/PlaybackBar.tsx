"use client";

import type { KeyboardEvent } from "react";
import { PLAYBACK_SPEEDS } from "../lib/playback";
import { positionFraction, type ReplayMark } from "../lib/replay";

/** `t=62s`, the clock as the transport shows it. */
function clock(t: number): string {
  return `t=${Math.floor(t)}s`;
}

export type PlaybackMode = "live" | "replay" | "ended";

/**
 * 3.2 / 3.4 — the transport under the live view: play/pause, the speed, the
 * clock, whether you are watching the run arrive (`LIVE`) or a recording of
 * it (`REPLAY`), and a scrubber over the whole run with a mark on every
 * incident and on the first failure.
 *
 * Keys, while the bar has focus: Space plays or pauses, ← / → step one tick,
 * Home / End go to the start / the newest tick.
 */
export function PlaybackBar({
  position,
  frameTime,
  duration,
  edge,
  step,
  playing,
  speed,
  mode,
  marks,
  breakAt,
  onToggle,
  onSpeed,
  onSeek,
  onStep,
}: {
  position: number;
  /** Time of the tick on screen (shown while paused). */
  frameTime: number | null;
  duration: number;
  /** The newest tick received: the scrubber cannot go past it. */
  edge: number;
  /** The tick interval: the scrubber's step. */
  step: number;
  playing: boolean;
  speed: number;
  mode: PlaybackMode;
  marks: readonly ReplayMark[];
  /** "Where it broke", or null when nothing did. */
  breakAt: number | null;
  onToggle: () => void;
  onSpeed: (speed: number) => void;
  onSeek: (t: number) => void;
  onStep: (direction: 1 | -1) => void;
}) {
  const action = playing ? "Pause" : mode === "ended" ? "Replay" : "Play";
  const shown = !playing && frameTime !== null ? frameTime : position;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const onButton = (event.target as HTMLElement).tagName === "BUTTON";
    if (event.key === " " && !onButton) {
      event.preventDefault();
      onToggle();
    } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      onStep(event.key === "ArrowRight" ? 1 : -1);
    } else if (event.key === "Home") {
      event.preventDefault();
      onSeek(0);
    } else if (event.key === "End") {
      event.preventDefault();
      onSeek(edge);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-2" onKeyDown={onKeyDown}>
      {/* The scrubber: the thumb and the marks share one 0..duration scale. */}
      <div className="relative h-5">
        <input
          type="range"
          min={0}
          max={Math.max(duration, step)}
          step={step}
          value={Math.min(position, Math.max(duration, step))}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-label="Moment of the run"
          aria-valuetext={`${clock(shown)} of ${Math.round(duration)}s`}
          className="absolute inset-x-0 top-1/2 h-1.5 w-full -translate-y-1/2 cursor-pointer appearance-auto accent-accent"
        />
        {marks.map((mark) => (
          <button
            key={`${mark.kind}-${mark.t}`}
            type="button"
            title={`t=${mark.t}s ${mark.text}`}
            aria-label={`Go to t=${mark.t}s: ${mark.text}`}
            onClick={() => onSeek(mark.t)}
            style={{ left: `calc(${positionFraction(mark.t, duration) * 100}% - 1.5px)` }}
            className="absolute top-0 h-5 w-[3px] rounded-sm bg-sev-crit outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="button"
          onClick={onToggle}
          aria-label={`${action} the run`}
          className="flex h-8 min-w-[84px] items-center justify-center gap-2 rounded-lg border border-line bg-surface-1 px-3 text-[13px] font-medium text-ink outline-none transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent"
        >
          <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden="true" focusable="false">
            {playing ? (
              <path d="M3 2h2v8H3zM7 2h2v8H7z" fill="currentColor" />
            ) : (
              <path d="M3 1.8v8.4L10 6Z" fill="currentColor" />
            )}
          </svg>
          {action}
        </button>

        <div
          role="group"
          aria-label="Playback speed"
          className="flex h-8 items-center rounded-lg border border-line bg-surface-1 p-0.5"
        >
          {PLAYBACK_SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onSpeed(s)}
              aria-pressed={speed === s}
              className={`h-[26px] rounded-md px-2.5 font-mono text-[12px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent ${
                speed === s ? "bg-accent text-white" : "text-ink-dim hover:text-ink"
              }`}
            >
              {s}×
            </button>
          ))}
        </div>

        <span className="font-mono text-[12px] tabular-nums text-ink-dim">
          <span className="text-ink">{clock(shown)}</span> / {Math.round(duration)}s
        </span>

        {mode === "live" ? (
          <span className="flex items-center gap-1.5 rounded-md bg-sev-crit-soft px-2 py-0.5 text-[11px] font-semibold tracking-[0.08em] text-sev-crit">
            <span className="h-1.5 w-1.5 rounded-full bg-sev-crit" aria-hidden="true" />
            LIVE
          </span>
        ) : mode === "replay" ? (
          <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-semibold tracking-[0.08em] text-ink-dim">
            REPLAY
          </span>
        ) : null}

        {breakAt !== null ? (
          <button
            type="button"
            onClick={() => onSeek(breakAt)}
            className="ml-auto h-8 rounded-lg px-3 text-[13px] font-medium text-accent outline-none transition-colors hover:bg-accent-soft focus-visible:ring-2 focus-visible:ring-accent"
          >
            Jump to where it broke
          </button>
        ) : null}
      </div>
    </div>
  );
}
