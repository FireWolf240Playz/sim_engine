"use client";

import { useEffect, useState } from "react";
import { SEVERITY_STROKE, scoreSeverity, type Severity } from "../lib/format";
import { usePrefersReducedMotion } from "../lib/useReducedMotion";

const R = 52;
const CIRC = 2 * Math.PI * R;

function clamp100(value: number): number {
  return Math.min(100, Math.max(0, value));
}

/**
 * The progress arc. It mounts once: the first run sweeps it in from an
 * empty ring, and every later run glides from the previous score to the
 * new one (the CSS transition on `stroke-dashoffset`), so a re-run moves
 * the arc instead of emptying and refilling it.
 *
 * The sweep itself is a CSS transition (`.eleven-gauge-arc` in
 * `globals.css`); the double `requestAnimationFrame` just guarantees the
 * empty state has painted before the first value lands. Under
 * `prefers-reduced-motion` the CSS transition is off, so the effect sets
 * the final offset directly.
 */
function Arc({ score, severityClass }: { score: number; severityClass: string }) {
  const reduced = usePrefersReducedMotion();
  const final = CIRC * (1 - clamp100(score) / 100);
  // `false` = the empty ring, before the sweep; the render branch below
  // shows the final offset directly when motion is reduced, so no
  // state is ever needed in that path.
  const [swept, setSwept] = useState(false);

  useEffect(() => {
    if (reduced) return;
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setSwept(true));
    });
    return () => {
      cancelAnimationFrame(first);
      if (second) cancelAnimationFrame(second);
    };
  }, [reduced]);

  const offset = reduced || swept ? final : CIRC;

  return (
    <circle
      cx="64"
      cy="64"
      r={R}
      fill="none"
      strokeWidth="9"
      strokeLinecap="round"
      className={`eleven-gauge-arc ${severityClass}`}
      strokeDasharray={CIRC}
      strokeDashoffset={offset}
    />
  );
}

/**
 * The resilience score as an animated ring — the visual anchor of the
 * verdict. Colour follows `scoreSeverity` unless the caller passes the
 * verdict's own `severity` (`VerdictPanel` does, so a critical finding
 * turns the ring red even at a high score); the number is the run's exact
 * `resilience_score`.
 */
export function ScoreGauge({
  score,
  size = 132,
  severity,
}: {
  score: number;
  size?: number;
  severity?: Severity;
}) {
  const severityClass = SEVERITY_STROKE[severity ?? scoreSeverity(clamp100(score))];
  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Resilience score ${score} out of 100`}
    >
      <svg viewBox="0 0 128 128" className="h-full w-full" aria-hidden="true" focusable="false">
        <circle cx="64" cy="64" r={R} fill="none" strokeWidth="9" className="stroke-surface-2" />
        <g transform="rotate(-90 64 64)">
          <Arc score={score} severityClass={severityClass} />
        </g>
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[27px] font-semibold leading-none tracking-tight text-ink">{score}</span>
        <span className="mt-1 text-[11px] font-medium text-ink-dim">/100</span>
      </div>
    </div>
  );
}
