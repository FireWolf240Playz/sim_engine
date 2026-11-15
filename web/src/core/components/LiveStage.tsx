"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { liveFrame, maxEdgeRate, visibleLines } from "../lib/liveView";
import {
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
} from "../lib/playback";
import { breakingMoment, replayMarks, stepTick } from "../lib/replay";
import type { RunFeed } from "../lib/runStream";
import type { TopologyDiagramProps } from "../lib/topologyView";
import { usePrefersReducedMotion } from "../lib/useReducedMotion";
import { EventFeed } from "./EventFeed";
import { PlaybackBar, type PlaybackMode } from "./PlaybackBar";
import { TopologyDiagram } from "./TopologyDiagram";

/** A frame after the tab was hidden can be seconds long; never jump that far. */
const MAX_FRAME_MS = 250;

/**
 * The run this tab has already played from the start. Coming back to the
 * simulator page shows a finished run at its end instead of replaying it;
 * a new run plays.
 */
let autoplayedRun: string | null = null;

/** 3.4: a request from outside (the timeline chart) to show a moment. */
export interface SeekRequest {
  t: number;
  /** Changes on every request, so asking for the same moment twice works. */
  n: number;
}

type Props = Omit<TopologyDiagramProps, "live" | "maxEdgeRate" | "paused" | "reducedMotion"> & {
  feed: RunFeed;
  /** 3.4: told the time of the tick on screen when it changes (null: none). */
  onCursor?: (time: number | null) => void;
  /** 3.4: the newest request to show a moment. */
  seekRequest?: SeekRequest | null;
};

/**
 * 3.2 — mission control: the topology plays the run, the event feed tells
 * it, and a transport bar drives the clock.
 *
 * This component owns the playback clock (a `requestAnimationFrame` loop,
 * 60 times a second while playing), so the clock never re-renders the page.
 * The diagram is memo'd and receives a frame that changes once per tick,
 * so it redraws a few times a second, not per animation frame.
 *
 * Without ticks (no streamed run yet) it is the plain diagram. When the run
 * has played to its end, the diagram goes back to the run as a whole (mean
 * utilization, the sizing verdict).
 */
export function LiveStage({ feed, onCursor, seekRequest = null, ...diagram }: Props) {
  const runId = feed.started?.run_id ?? null;
  const duration = feed.started?.duration ?? 0;
  const interval = feed.started?.metrics_interval ?? 1;
  const edge = liveEdge(feed.ticks);
  const complete = feed.phase === "measured" || feed.phase === "done" || feed.phase === "failed";
  const reducedMotion = usePrefersReducedMotion();

  const [playback, setPlayback] = useState<Playback>(() =>
    runId !== null && runId !== autoplayedRun
      ? startPlayback(duration)
      : { position: edge, playing: false, speed: defaultSpeed(duration) },
  );

  // A new run plays from the start (state adjusted while rendering, the
  // React way to reset on a changed input).
  const [shownRun, setShownRun] = useState(runId);
  if (runId !== shownRun) {
    setShownRun(runId);
    setPlayback(startPlayback(duration));
  }
  useEffect(() => {
    if (runId !== null) autoplayedRun = runId;
  }, [runId]);

  // 3.4: a moment asked for from outside (a click on the timeline).
  const [handledSeek, setHandledSeek] = useState(seekRequest);
  if (seekRequest !== handledSeek) {
    setHandledSeek(seekRequest);
    if (seekRequest) setPlayback((p) => seek(p, seekRequest.t, edge));
  }

  // The loop reads the newest edge and completion without restarting.
  const edgeRef = useRef(edge);
  const completeRef = useRef(complete);
  useEffect(() => {
    edgeRef.current = edge;
    completeRef.current = complete;
  });

  useEffect(() => {
    if (!playback.playing) return;
    let frame = 0;
    let last: number | null = null;
    const step = (now: number) => {
      if (last !== null) {
        const elapsed = Math.min(now - last, MAX_FRAME_MS);
        setPlayback((p) => advance(p, elapsed, edgeRef.current, completeRef.current));
      }
      last = now;
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playback.playing]);

  const hasTicks = feed.ticks.length > 0;
  const showLive = hasTicks && (playback.playing || playback.position < edge);

  // The moment the frame depends on: the newest tick or incident mark at or
  // before the clock. It changes a few times a second, not per frame, and
  // the frame read there is exactly the frame at the clock.
  const index = frameIndex(feed.ticks, playback.position);
  let anchor = index >= 0 ? feed.ticks[index].time : -1;
  for (const mark of feed.chaos) {
    if (mark.t <= playback.position && mark.t > anchor) anchor = mark.t;
  }
  const { nodes } = diagram;
  const frame = useMemo(
    () => (showLive ? liveFrame(nodes, feed.ticks, feed.chaos, anchor, interval) : null),
    [showLive, nodes, feed.ticks, feed.chaos, anchor, interval],
  );
  const busiest = useMemo(() => maxEdgeRate(feed.ticks, interval), [feed.ticks, interval]);

  // The feed up to the clock, as the same array until a line is added.
  let count = 0;
  while (count < feed.lines.length && feed.lines[count].t <= playback.position) count += 1;
  const cutoff = count > 0 ? feed.lines[count - 1].t : -1;
  const shownLines = useMemo(() => visibleLines(feed.lines, cutoff), [feed.lines, cutoff]);

  // 3.4: the chart's cursor follows the tick on screen — a few times a
  // second while playing, never per animation frame.
  const cursor = showLive && index >= 0 ? feed.ticks[index].time : null;
  useEffect(() => {
    onCursor?.(cursor);
  }, [cursor, onCursor]);

  const marks = useMemo(() => replayMarks(feed.lines), [feed.lines]);
  const breakAt = useMemo(() => breakingMoment(feed.lines), [feed.lines]);

  const mode: PlaybackMode =
    feed.phase === "running" && atLiveEdge(playback, edge) && playback.playing
      ? "live"
      : showLive
        ? "replay"
        : "ended";

  const diagramView = (
    <TopologyDiagram
      {...diagram}
      live={frame}
      maxEdgeRate={busiest}
      paused={!playback.playing}
      reducedMotion={reducedMotion}
    />
  );

  if (!hasTicks) return diagramView;
  // Replay never calls the engine: every control below only moves the clock.

  return (
    <div>
      {/* The diagram never shrinks below its natural size, so the feed sits
          beside it only on a canvas wide enough for both (measured: the
          5-node demo needs ~1080px; at 1440 there is ~1110), else below. */}
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">{diagramView}</div>
        {/* As tall as the diagram beside it, scrolling inside itself. */}
        <div className="relative h-40 2xl:h-auto">
          <div className="absolute inset-0">
            <EventFeed lines={shownLines} />
          </div>
        </div>
      </div>
      <PlaybackBar
        position={playback.position}
        frameTime={index >= 0 ? feed.ticks[index].time : null}
        duration={duration}
        edge={edge}
        step={interval}
        playing={playback.playing}
        speed={playback.speed}
        mode={mode}
        marks={marks}
        breakAt={breakAt}
        onToggle={() => setPlayback((p) => togglePlay(p, edgeRef.current, completeRef.current))}
        onSpeed={(speed) => setPlayback((p) => setSpeed(p, speed))}
        onSeek={(t) => setPlayback((p) => seek(p, t, edgeRef.current))}
        onStep={(direction) => setPlayback((p) => stepTick(p, feed.ticks, direction))}
      />
    </div>
  );
}
