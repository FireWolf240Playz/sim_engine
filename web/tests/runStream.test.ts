/**
 * Contract for roadmap 3.1c (`.agents/tasks/3.1c-web-run-pipe.md`): the run
 * stream, folded into the state the page draws from.
 *
 * `parseRunEvent` turns one server-sent event into a typed one;
 * `reduceRun` folds events into a `RunFeed`. Pure: no fetch, no React. The
 * roadmap's 3.2 acceptance ("the event feed matches the engine's emitted
 * events exactly, with a mocked SSE stream") is pinned here: the lines on
 * screen are the stream's `feed` events, in order, word for word.
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import {
  EMPTY_FEED,
  RUN_EVENT_NAMES,
  parseRunEvent,
  planPending,
  provisionalResult,
  reduceRun,
  type RunEvent,
  type RunFeed,
} from "@/core/lib/runStream";
import { CHAOS, LINES, MEASURED, RESULT, SCHEDULE, STARTED, TICKS, WIRE } from "./fixtures/runStream";

function fold(wire: Array<[string, string]>, from: RunFeed = EMPTY_FEED): RunFeed {
  let feed = from;
  for (const [name, data] of wire) {
    const event = parseRunEvent(name, data);
    if (event) feed = reduceRun(feed, event);
  }
  return feed;
}

const upTo = (name: string): Array<[string, string]> =>
  WIRE.slice(0, WIRE.findIndex(([n]) => n === name) + 1);

describe("parseRunEvent", () => {
  it("knows the eight events of a run", () => {
    expect([...RUN_EVENT_NAMES]).toEqual([
      "started", "tick", "chaos", "feed", "seed", "measured", "result", "error",
    ]);
  });

  it("types an event by its name", () => {
    expect(parseRunEvent("tick", JSON.stringify(TICKS[1]))).toEqual({ type: "tick", data: TICKS[1] });
    expect(parseRunEvent("feed", JSON.stringify(LINES[0]))).toEqual({ type: "feed", data: LINES[0] });
    expect(parseRunEvent("error", '{"detail":"the run failed: boom"}')).toEqual({
      type: "error",
      data: { detail: "the run failed: boom" },
    });
  });

  it("skips a ping, an event it does not know, and a body that is not JSON", () => {
    expect(parseRunEvent("ping", "{}")).toBeNull();
    expect(parseRunEvent("something_new", "{}")).toBeNull();
    expect(parseRunEvent("tick", "{not json")).toBeNull();
  });
});

describe("reduceRun", () => {
  it("starts idle and empty", () => {
    expect(EMPTY_FEED).toEqual({
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
    });
  });

  it("is running once the run has started", () => {
    const feed = fold(upTo("started"));
    expect(feed.phase).toBe("running");
    expect(feed.started).toEqual(STARTED);
  });

  it("collects the ticks and the chaos windows in order", () => {
    const feed = fold(WIRE);
    expect(feed.ticks).toEqual(TICKS);
    expect(feed.chaos).toEqual(CHAOS);
  });

  it("shows the engine's feed lines, exactly and in order", () => {
    const sent = WIRE.filter(([name]) => name === "feed").map(([, data]) => JSON.parse(data).text);
    const feed = fold(WIRE);
    expect(feed.lines.map((line) => line.text)).toEqual(sent);
    expect(feed.lines).toEqual(LINES);
  });

  it("has the summary before the plan", () => {
    const feed = fold(upTo("measured"));
    expect(feed.phase).toBe("measured");
    expect(feed.measured).toEqual(MEASURED.summary);
    expect(feed.result).toBeNull();
  });

  it("is done when the result arrives", () => {
    const feed = fold(WIRE);
    expect(feed.phase).toBe("done");
    expect(feed.result).toEqual(RESULT);
    expect(feed.error).toBeNull();
  });

  it("keeps what it had when the run fails", () => {
    const feed = fold([...upTo("measured"), ["error", '{"detail":"the run failed: boom"}']]);
    expect(feed.phase).toBe("failed");
    expect(feed.error).toBe("the run failed: boom");
    expect(feed.ticks).toEqual(TICKS);
    expect(feed.measured).toEqual(MEASURED.summary);
  });

  it("ignores anything after the run has ended", () => {
    const done = fold(WIRE);
    const late: RunEvent = { type: "tick", data: TICKS[0] };
    expect(reduceRun(done, late)).toBe(done);
    const failed = fold([...upTo("started"), ["error", '{"detail":"x"}']]);
    expect(reduceRun(failed, late)).toBe(failed);
  });

  it("starts over on a new run", () => {
    const next = reduceRun(fold(WIRE), { type: "started", data: { ...STARTED, run_id: "run-2" } });
    expect(next).toEqual({ ...EMPTY_FEED, phase: "running", started: { ...STARTED, run_id: "run-2" } });
  });

  it("never changes the feed it was given", () => {
    const before = fold(upTo("started"));
    const snapshot = JSON.stringify(before);
    const after = reduceRun(before, { type: "tick", data: TICKS[0] });
    expect(after).not.toBe(before);
    expect(JSON.stringify(before)).toBe(snapshot);
  });

  it("only replaces the part an event changes", () => {
    // Unchanged arrays keep their identity, so a memoised feed panel does
    // not redraw on every tick and the diagram does not redraw on a line.
    const before = fold(upTo("chaos"));
    const afterTick = reduceRun(before, { type: "tick", data: TICKS[2] });
    expect(afterTick.lines).toBe(before.lines);
    expect(afterTick.chaos).toBe(before.chaos);
    expect(afterTick.ticks).not.toBe(before.ticks);
    const afterLine = reduceRun(before, { type: "feed", data: LINES[0] });
    expect(afterLine.ticks).toBe(before.ticks);
    expect(afterLine.lines).not.toBe(before.lines);
  });

  it("counts the seeds of a multi-seed run", () => {
    let feed = reduceRun(EMPTY_FEED, { type: "started", data: { ...STARTED, seeds: [42, 43, 44] } });
    feed = reduceRun(feed, { type: "seed", data: { seed: 42, index: 1, of: 3, resilience_score: 70 } });
    feed = reduceRun(feed, { type: "seed", data: { seed: 43, index: 2, of: 3, resilience_score: 64 } });
    expect(feed.seedsDone).toBe(2);
    expect(feed.phase).toBe("running");
    feed = reduceRun(feed, { type: "measured", data: { summary: MEASURED.summary, typical_seed: 43 } });
    expect(feed.typicalSeed).toBe(43);
  });
});

describe("provisionalResult", () => {
  it("is nothing until the run is measured", () => {
    expect(provisionalResult(EMPTY_FEED)).toBeNull();
    expect(provisionalResult(fold(upTo("tick")))).toBeNull();
  });

  it("is the summary with the stream's own ticks while the plan is verified", () => {
    // What the page draws in the meantime: the same shape `/simulate`
    // returns, built from what the stream already delivered.
    expect(provisionalResult(fold(upTo("measured")))).toEqual({
      summary: MEASURED.summary,
      report_png_b64: null,
      chaos: SCHEDULE,
      timeseries: TICKS,
    });
  });

  it("names the typical seed of a multi-seed run", () => {
    let feed = reduceRun(EMPTY_FEED, { type: "started", data: { ...STARTED, seeds: [42, 43, 44] } });
    feed = reduceRun(feed, { type: "tick", data: TICKS[0] });
    feed = reduceRun(feed, { type: "measured", data: { summary: MEASURED.summary, typical_seed: 43 } });
    expect(provisionalResult(feed)).toEqual({
      summary: MEASURED.summary,
      report_png_b64: null,
      chaos: SCHEDULE,
      timeseries: [TICKS[0]],
      seeds: [42, 43, 44],
      typical_seed: 43,
    });
  });

  it("is the engine's result once it has arrived", () => {
    const feed = fold(WIRE);
    expect(provisionalResult(feed)).toBe(feed.result);
  });

  it("is still the measured run when the plan failed", () => {
    const feed = fold([...upTo("measured"), ["error", '{"detail":"the run failed: boom"}']]);
    expect(provisionalResult(feed)?.summary).toEqual(MEASURED.summary);
  });
});

describe("planPending", () => {
  it("is true only between the summary and the result", () => {
    expect(planPending(EMPTY_FEED)).toBe(false);
    expect(planPending(fold(upTo("started")))).toBe(false);
    expect(planPending(fold(upTo("measured")))).toBe(true);
    expect(planPending(fold(WIRE))).toBe(false);
    expect(planPending(fold([...upTo("measured"), ["error", '{"detail":"x"}']]))).toBe(false);
  });
});
