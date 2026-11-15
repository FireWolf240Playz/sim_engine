/**
 * Contract for roadmap 3.2 (`.agents/tasks/3.2-live-view.md`): what the
 * diagram and the feed show at one moment of the run.
 *
 * `liveFrame` is the diagram's view of the tick under the playback clock:
 * each node's load, its live capacity against its configured size, whether
 * an incident is on it right now, and how much traffic each edge carries.
 * Pure; the numbers are the engine's ticks and chaos events.
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import {
  LIVE_CRIT,
  LIVE_WARN,
  PULSE_LEVELS,
  activeIncidents,
  feedTime,
  liveFrame,
  liveTone,
  maxEdgeRate,
  pulseLevel,
  visibleLines,
} from "@/core/lib/liveView";
import type { ChaosMark, TimelineTick } from "@/core/types";
import { CHAOS, INTERVAL, LINES, NODES, TICKS } from "./fixtures/runStream";

describe("liveTone", () => {
  it("uses the feed's two thresholds", () => {
    // sim_core/feed.py: SATURATED_AT and RECOVERED_BELOW.
    expect(LIVE_CRIT).toBe(0.9);
    expect(LIVE_WARN).toBe(0.75);
  });

  it("colours a node by its load right now", () => {
    expect(liveTone(0.2)).toBe("ok");
    expect(liveTone(0.749)).toBe("ok");
    expect(liveTone(0.75)).toBe("warn");
    expect(liveTone(0.899)).toBe("warn");
    expect(liveTone(0.9)).toBe("crit");
    expect(liveTone(1)).toBe("crit");
  });
});

describe("activeIncidents", () => {
  it("names the nodes with a window open at that moment", () => {
    expect(activeIncidents(CHAOS, 2.9)).toEqual({ nodes: [], wholePath: false });
    expect(activeIncidents(CHAOS, 3)).toEqual({ nodes: ["db"], wholePath: false });
    expect(activeIncidents(CHAOS, 6.9)).toEqual({ nodes: ["db"], wholePath: false });
    expect(activeIncidents(CHAOS, 7)).toEqual({ nodes: [], wholePath: false });
  });

  it("marks the whole path for an incident that names no node", () => {
    const spike: ChaosMark[] = [
      { t: 4, phase: "start", event_type: "network_latency", node: null, intensity: 0.5 },
      { t: 9, phase: "end", event_type: "network_latency", node: null, intensity: 0.5 },
    ];
    expect(activeIncidents(spike, 5)).toEqual({ nodes: [], wholePath: true });
    expect(activeIncidents(spike, 9)).toEqual({ nodes: [], wholePath: false });
  });

  it("keeps a node marked while any of its windows is open", () => {
    const overlapping: ChaosMark[] = [
      { t: 1, phase: "start", event_type: "component_failure", node: "db", intensity: 0.5 },
      { t: 2, phase: "start", event_type: "component_failure", node: "db", intensity: 0.5 },
      { t: 3, phase: "end", event_type: "component_failure", node: "db", intensity: 0.5 },
      { t: 5, phase: "end", event_type: "component_failure", node: "db", intensity: 0.5 },
    ];
    expect(activeIncidents(overlapping, 3.5).nodes).toEqual(["db"]);
    expect(activeIncidents(overlapping, 5).nodes).toEqual([]);
  });

  it("treats a window that never closed as open to the end", () => {
    expect(activeIncidents(CHAOS.slice(0, 1), 1000).nodes).toEqual(["db"]);
  });

  it("lists each node once, in the order it was first hit", () => {
    const two: ChaosMark[] = [
      { t: 1, phase: "start", event_type: "component_failure", node: "worker", intensity: 0.5 },
      { t: 2, phase: "start", event_type: "cache_outage", node: "db", intensity: 1 },
      { t: 3, phase: "start", event_type: "component_failure", node: "worker", intensity: 0.5 },
    ];
    expect(activeIncidents(two, 4).nodes).toEqual(["worker", "db"]);
  });
});

describe("liveFrame", () => {
  it("is nothing before the first tick", () => {
    expect(liveFrame(NODES, [], CHAOS, 5, INTERVAL)).toBeNull();
    expect(liveFrame(NODES, TICKS.slice(1), CHAOS, 1, INTERVAL)).toBeNull();
  });

  it("shows the tick under the clock", () => {
    // Position 5 sits on the tick sampled at t=4: the outage (t=3..7) has
    // halved the db and filled what is left.
    const frame = liveFrame(NODES, TICKS, CHAOS, 5, INTERVAL);
    expect(frame).toEqual({
      index: 2,
      time: 4,
      wholePath: false,
      edgeRates: [2.5, 2],
      nodes: [
        { name: "lb", utilization: 0.2, queue: 0, capacity: 10, configured: 10, inUse: 2, tone: "ok", degraded: false, struck: false },
        { name: "worker", utilization: 0.5, queue: 0, capacity: 6, configured: 6, inUse: 3, tone: "ok", degraded: false, struck: false },
        { name: "db", utilization: 0.95, queue: 2, capacity: 2, configured: 4, inUse: 2, tone: "crit", degraded: true, struck: true },
      ],
    });
  });

  it("marks the struck node the moment the incident begins", () => {
    // At 3.5 the newest tick is still the one from t=2, with all four
    // slots; the incident began at 3, so the node is already marked.
    const frame = liveFrame(NODES, TICKS, CHAOS, 3.5, INTERVAL);
    const db = frame?.nodes[2];
    expect(frame?.time).toBe(2);
    expect(db).toMatchObject({ capacity: 4, degraded: false, struck: true });
  });

  it("shows the node whole again after the incident", () => {
    const db = liveFrame(NODES, TICKS, CHAOS, 8.5, INTERVAL)?.nodes[2];
    expect(db).toMatchObject({ utilization: 0.8, capacity: 4, degraded: false, struck: false, tone: "warn" });
  });

  it("keeps the nodes in the config's order", () => {
    const reversed = [...NODES].reverse();
    const names = liveFrame(reversed, TICKS, CHAOS, 5, INTERVAL)?.nodes.map((node) => node.name);
    expect(names).toEqual(["db", "worker", "lb"]);
  });

  it("reads a tick from before 3.1, and a node the tick does not have", () => {
    const old: TimelineTick[] = [
      { time: 0, p50_latency: null, p95_latency: null, sla_met: null, per_component: { db: { utilization: 0.4, queue_length: 1 } } },
    ];
    const frame = liveFrame(NODES, old, [], 0, INTERVAL);
    expect(frame?.edgeRates).toEqual([]);
    expect(frame?.nodes[2]).toEqual({
      name: "db", utilization: 0.4, queue: 1, capacity: 4, configured: 4, inUse: 0, tone: "ok", degraded: false, struck: false,
    });
    expect(frame?.nodes[0]).toMatchObject({ name: "lb", utilization: 0, queue: 0, capacity: 10 });
  });

  it("marks every node for a whole-path incident", () => {
    const spike: ChaosMark[] = [{ t: 1, phase: "start", event_type: "network_latency", node: null, intensity: 0.5 }];
    const frame = liveFrame(NODES, TICKS, spike, 5, INTERVAL);
    expect(frame?.wholePath).toBe(true);
    expect(frame?.nodes.every((node) => node.struck)).toBe(true);
  });
});

describe("edge pulses", () => {
  it("finds the busiest edge of the run, in requests per second", () => {
    expect(maxEdgeRate(TICKS, INTERVAL)).toBe(3); // 6 requests in a 2 s tick
    expect(maxEdgeRate([], INTERVAL)).toBe(0);
  });

  it("gives an edge more pulses the more traffic it carries", () => {
    expect(PULSE_LEVELS).toBe(4);
    expect(pulseLevel(0, 3)).toBe(0);
    expect(pulseLevel(0.1, 3)).toBe(1);
    expect(pulseLevel(1.5, 3)).toBe(2);
    expect(pulseLevel(2, 3)).toBe(3);
    expect(pulseLevel(3, 3)).toBe(4);
    expect(pulseLevel(9, 3)).toBe(4);
  });

  it("shows nothing on a run with no traffic", () => {
    expect(pulseLevel(0, 0)).toBe(0);
    expect(pulseLevel(1, 0)).toBe(0);
  });
});

describe("the feed", () => {
  it("shows the lines up to the clock", () => {
    expect(visibleLines(LINES, 2.9)).toEqual([]);
    expect(visibleLines(LINES, 3)).toEqual([LINES[0]]);
    expect(visibleLines(LINES, 6)).toEqual(LINES.slice(0, 4));
    expect(visibleLines(LINES, 6.5)).toEqual(LINES.slice(0, 4));
  });

  it("hands back the same list once every line is visible", () => {
    expect(visibleLines(LINES, 100)).toBe(LINES);
  });

  it("writes the time the way the roadmap shows it", () => {
    expect(feedTime(62)).toBe("t=62s");
    expect(feedTime(0)).toBe("t=0s");
    expect(feedTime(2.5)).toBe("t=2.5s");
    expect(feedTime(63.04)).toBe("t=63s");
  });
});
