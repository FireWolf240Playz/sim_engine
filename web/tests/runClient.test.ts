/**
 * Contract for roadmap 3.1c (`.agents/tasks/3.1c-web-run-pipe.md`): starting
 * a run and watching its stream.
 *
 * `api.startRun` posts to `/runs`; `watchRun` reads the event stream through
 * an `EventSource`-shaped object (injected here, the browser's in the app),
 * hands every event on, stops at the end, and says so when the stream dies.
 * Make these pass. Do not edit them.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/core/api/client";
import { SILENCE_MS, watchRun, type StreamSource } from "@/core/api/runStream";
import { RUN_EVENT_NAMES, type RunEvent } from "@/core/lib/runStream";
import type { ChaosEventCfg, SimulationConfig } from "@/core/types";
import { LINES, RESULT, TICKS, WIRE } from "./fixtures/runStream";

const CONFIG: SimulationConfig = {
  seed: 42,
  duration: 60,
  topology: { nodes: [], edges: [] },
  traffic: { base_rps: 2, duration: 60 },
};

const FAILURE: ChaosEventCfg = {
  event_type: "component_failure",
  intensity: 1,
  start_time: 20,
  interval: 60,
  duration: 15,
  target: "db",
};

class FakeSource implements StreamSource {
  readonly listeners = new Map<string, (event: { data: string }) => void>();
  onerror: (() => void) | null = null;
  readyState = 1;
  closed = false;

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: (event: { data: string }) => void): void {
    this.listeners.set(type, listener);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  emit(name: string, data: string): void {
    this.listeners.get(name)?.({ data });
  }
}

function watch() {
  const events: RunEvent[] = [];
  const failures: string[] = [];
  let source!: FakeSource;
  const stop = watchRun(
    "http://engine/runs/run-1/events",
    { onEvent: (event) => events.push(event), onFail: (message) => failures.push(message) },
    (url) => (source = new FakeSource(url)),
  );
  return { events, failures, source, stop };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("api.startRun", () => {
  function stub(status = 202, body: unknown = { run_id: "run-1", events_url: "/runs/run-1/events" }) {
    const seen: { url: string; init?: RequestInit } = { url: "" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        seen.url = String(input);
        seen.init = init;
        return new Response(JSON.stringify(body), { status });
      }),
    );
    return seen;
  }

  it("posts the run and returns its id", async () => {
    const seen = stub();
    await expect(api.startRun({ config: CONFIG, playbook: "db_failover" })).resolves.toEqual({
      run_id: "run-1",
      events_url: "/runs/run-1/events",
    });
    expect(seen.url).toBe(`${api.base}/runs`);
    expect(seen.init?.method).toBe("POST");
    expect(JSON.parse(String(seen.init?.body))).toEqual({ config: CONFIG, playbook: "db_failover" });
  });

  it("sends the seeds and the injected failures when there are any", async () => {
    const seen = stub();
    await api.startRun({ config: CONFIG, playbook: null, n_seeds: 5, custom_chaos: [FAILURE] });
    expect(JSON.parse(String(seen.init?.body))).toEqual({
      config: CONFIG,
      playbook: null,
      n_seeds: 5,
      custom_chaos: [FAILURE],
    });
  });

  it("throws the engine's refusal as an ApiError", async () => {
    stub(422, { detail: "chaos event targets unknown component 'nope' (known nodes: ['db'])" });
    const error = await api.startRun({ config: CONFIG, playbook: null }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).message).toContain("unknown component 'nope'");
  });

  it("names the stream and the status of a run", async () => {
    expect(api.runEventsUrl("run-1")).toBe(`${api.base}/runs/run-1/events`);
    const seen = stub(200, { run_id: "run-1", status: "done", result: RESULT, error: null });
    await expect(api.runStatus("run-1")).resolves.toMatchObject({ status: "done" });
    expect(seen.url).toBe(`${api.base}/runs/run-1`);
  });
});

describe("watchRun", () => {
  it("opens the stream it was given and listens for every event and the ping", () => {
    const { source } = watch();
    expect(source.url).toBe("http://engine/runs/run-1/events");
    expect([...source.listeners.keys()].sort()).toEqual([...RUN_EVENT_NAMES, "ping"].sort());
  });

  it("hands every event on, typed and in order", () => {
    const { events, failures, source } = watch();
    for (const [name, data] of WIRE) source.emit(name, data);
    expect(events).toHaveLength(WIRE.length);
    expect(events.map((event) => event.type)).toEqual(WIRE.map(([name]) => name));
    expect(events[1]).toEqual({ type: "tick", data: TICKS[0] });
    expect(events.filter((event) => event.type === "feed").map((event) => event.data)).toEqual(LINES);
    expect(failures).toEqual([]);
  });

  it("closes the stream when the result has arrived", () => {
    const { events, source } = watch();
    for (const [name, data] of WIRE) source.emit(name, data);
    expect(source.closed).toBe(true);
    source.emit("tick", JSON.stringify(TICKS[0]));
    expect(events).toHaveLength(WIRE.length);
  });

  it("closes the stream on the engine's error event, and hands it on", () => {
    const { events, failures, source } = watch();
    source.emit("error", '{"detail":"the run failed: boom"}');
    expect(events).toEqual([{ type: "error", data: { detail: "the run failed: boom" } }]);
    expect(source.closed).toBe(true);
    expect(failures).toEqual([]);
  });

  it("skips what it cannot read without stopping", () => {
    const { events, source } = watch();
    source.emit("tick", "{not json");
    source.emit("tick", JSON.stringify(TICKS[0]));
    expect(events).toEqual([{ type: "tick", data: TICKS[0] }]);
    expect(source.closed).toBe(false);
  });

  it("stops when it is told to", () => {
    const { events, failures, source, stop } = watch();
    stop();
    expect(source.closed).toBe(true);
    source.emit("tick", JSON.stringify(TICKS[0]));
    source.onerror?.();
    expect(events).toEqual([]);
    expect(failures).toEqual([]);
  });

  it("lets the browser reconnect a dropped stream", () => {
    // readyState 0 = the EventSource is reconnecting by itself (it sends
    // Last-Event-ID, the engine resumes): not a failure.
    const { failures, source } = watch();
    source.readyState = 0;
    source.onerror?.();
    expect(failures).toEqual([]);
    expect(source.closed).toBe(false);
  });

  it("fails when the stream is closed for good before the run ended", () => {
    const { failures, source } = watch();
    source.readyState = 2;
    source.onerror?.();
    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatch(/closed before the run finished/);
    source.onerror?.();
    expect(failures).toHaveLength(1);
  });

  describe("a stream that goes silent", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    it("waits 45 seconds", () => {
      expect(SILENCE_MS).toBe(45_000);
    });

    it("fails after that long with nothing at all", () => {
      const { failures, source } = watch();
      vi.advanceTimersByTime(SILENCE_MS - 1);
      expect(failures).toEqual([]);
      vi.advanceTimersByTime(1);
      expect(failures).toHaveLength(1);
      expect(failures[0]).toMatch(/stopped answering/);
      expect(source.closed).toBe(true);
    });

    it("counts a ping as a sign of life", () => {
      const { failures, source } = watch();
      vi.advanceTimersByTime(SILENCE_MS - 1000);
      source.emit("ping", "{}");
      vi.advanceTimersByTime(SILENCE_MS - 1000);
      expect(failures).toEqual([]);
      source.emit("tick", JSON.stringify(TICKS[0]));
      vi.advanceTimersByTime(SILENCE_MS - 1000);
      expect(failures).toEqual([]);
      vi.advanceTimersByTime(1000);
      expect(failures).toHaveLength(1);
    });

    it("stops the clock once the run has ended or the watch was stopped", () => {
      const ended = watch();
      for (const [name, data] of WIRE) ended.source.emit(name, data);
      const stopped = watch();
      stopped.stop();
      vi.advanceTimersByTime(SILENCE_MS * 3);
      expect(ended.failures).toEqual([]);
      expect(stopped.failures).toEqual([]);
    });
  });
});
