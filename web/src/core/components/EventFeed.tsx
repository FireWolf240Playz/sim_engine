"use client";

import { useEffect, useRef } from "react";
import type { FeedLine, FindingSeverity } from "../types";
import { SEVERITY_BG, type Severity } from "../lib/format";
import { feedTime } from "../lib/liveView";

/** Engine severity → the dot's colour (`info` reads as the healthy colour). */
const DOT: Record<FindingSeverity, Severity> = { info: "ok", warn: "warn", crit: "crit" };

/** How close to the bottom still counts as "following" the feed, in px. */
const FOLLOW_SLACK = 24;

/**
 * 3.2 — the run, told as it plays: the engine's feed lines up to the
 * playback clock, newest at the bottom. Every word is the engine's
 * (`sim_core/feed.py`); this only lays them out. It follows the newest line
 * unless the user has scrolled up to read.
 */
export function EventFeed({ lines }: { lines: readonly FeedLine[] }) {
  const box = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <div
      ref={box}
      role="log"
      aria-live="polite"
      aria-label="Event feed"
      onScroll={(event) => {
        const el = event.currentTarget;
        following.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLACK;
      }}
      className="h-full overflow-y-auto rounded-lg border border-line bg-surface-0/60 px-3 py-2"
    >
      {lines.length === 0 ? (
        <p className="py-1 text-[12.5px] leading-5 text-ink-dim">Nothing to report yet.</p>
      ) : (
        <ol className="flex flex-col">
          {lines.map((line, i) => (
            <li
              key={`${line.t}-${i}`}
              className="grid grid-cols-[auto_auto_1fr] items-baseline gap-x-2 py-[3px] text-[12.5px] leading-5"
            >
              <span className="font-mono text-[11px] tabular-nums text-ink-dim">{feedTime(line.t)}</span>
              <span
                aria-hidden="true"
                className={`inline-block h-2 w-2 translate-y-[-1px] rounded-full ${SEVERITY_BG[DOT[line.severity]]}`}
              />
              <span className="text-ink">{line.text}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
