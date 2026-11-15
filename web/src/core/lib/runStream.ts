/**
 * 3.1c — a run's event stream, folded into the state the page draws from.
 *
 * `GET /runs/{id}/events` sends, in order: `started`, (`seed` per seed of a
 * multi-seed run), `tick` / `chaos` / `feed` as the engine goes, `measured`
 * (the summary before the Fix-it plan), then `result` (exactly what
 * `/simulate` returns) or `error`. See `api/routes/runs.py`.
 *
 * Pure and dependency-free: no fetch, no React. `reduceRun` returns a new
 * feed and replaces only the array an event touches, so a panel that only
 * reads `lines` keeps the same array while ticks arrive.
 */
import type {
  ChaosMark,
  FeedLine,
  RunMeasured,
  RunStarted,
  SeedProgress,
  SimulateResponse,
  Summary,
  TimelineTick,
} from "../types";

/** Every event a run sends, besides the id-less `ping`. */
export const RUN_EVENT_NAMES = [
  "started",
  "tick",
  "chaos",
  "feed",
  "seed",
  "measured",
  "result",
  "error",
] as const;

export type RunEventName = (typeof RUN_EVENT_NAMES)[number];

export type RunEvent =
  | { type: "started"; data: RunStarted }
  | { type: "tick"; data: TimelineTick }
  | { type: "chaos"; data: ChaosMark }
  | { type: "feed"; data: FeedLine }
  | { type: "seed"; data: SeedProgress }
  | { type: "measured"; data: RunMeasured }
  | { type: "result"; data: SimulateResponse }
  | { type: "error"; data: { detail: string } };

/**
 * Where a run is:
 * - `idle`      nothing started;
 * - `running`   simulating (the previous result may still be on screen);
 * - `measured`  the summary is in, the Fix-it plan is being verified;
 * - `done`      the engine's result is in;
 * - `failed`    the run ended with an error (what arrived before is kept).
 */
export type RunPhase = "idle" | "running" | "measured" | "done" | "failed";

export interface RunFeed {
  phase: RunPhase;
  started: RunStarted | null;
  ticks: TimelineTick[];
  chaos: ChaosMark[];
  lines: FeedLine[];
  /** Multi-seed: how many seeds have been measured. */
  seedsDone: number;
  /** The summary before the plan; null until `measured`. */
  measured: Summary | null;
  typicalSeed: number | null;
  result: SimulateResponse | null;
  error: string | null;
}

export const EMPTY_FEED: RunFeed = {
  phase: "idle",
  started: null,
  ticks: [],
  chaos: [],
  lines: [],
  seedsDone: 0,
  measured: null,
  typicalSeed: null,
  result: null,
  error: null,
};

const KNOWN = new Set<string>(RUN_EVENT_NAMES);

/**
 * One server-sent event as a typed one; `null` for a ping, a name this
 * client does not know (a newer engine), or a body that is not JSON.
 */
export function parseRunEvent(name: string, data: string): RunEvent | null {
  if (!KNOWN.has(name)) return null;
  try {
    return { type: name, data: JSON.parse(data) } as RunEvent;
  } catch {
    return null;
  }
}

/** The feed after `event`. A new `started` starts over; after the end, nothing changes. */
export function reduceRun(feed: RunFeed, event: RunEvent): RunFeed {
  if (event.type === "started") return { ...EMPTY_FEED, phase: "running", started: event.data };
  if (feed.phase === "done" || feed.phase === "failed") return feed;
  switch (event.type) {
    case "tick":
      return { ...feed, ticks: [...feed.ticks, event.data] };
    case "chaos":
      return { ...feed, chaos: [...feed.chaos, event.data] };
    case "feed":
      return { ...feed, lines: [...feed.lines, event.data] };
    case "seed":
      return { ...feed, seedsDone: feed.seedsDone + 1 };
    case "measured":
      return {
        ...feed,
        phase: "measured",
        measured: event.data.summary,
        typicalSeed: event.data.typical_seed,
      };
    case "result":
      return { ...feed, phase: "done", result: event.data };
    case "error":
      return { ...feed, phase: "failed", error: event.data.detail };
  }
}

/**
 * What the page shows for this run: nothing before `measured`; then the
 * summary with the stream's own ticks, in the shape `/simulate` returns,
 * while the plan is verified; then the engine's result itself.
 */
export function provisionalResult(feed: RunFeed): SimulateResponse | null {
  if (feed.result) return feed.result;
  if (!feed.measured) return null;
  return {
    summary: feed.measured,
    report_png_b64: null,
    chaos: feed.started?.chaos ?? [],
    timeseries: feed.ticks,
    ...(feed.started?.seeds ? { seeds: feed.started.seeds } : {}),
    ...(feed.typicalSeed !== null ? { typical_seed: feed.typicalSeed } : {}),
  };
}

/** True while the verdict is in and the Fix-it plan is still being verified. */
export function planPending(feed: RunFeed): boolean {
  return feed.phase === "measured";
}
