/**
 * Contract for roadmap 3.3 (`.agents/tasks/3.3-click-to-fail.md`), web half:
 * "inject failure here".
 *
 * Clicking a node builds one chaos event: a full outage of that node for 15
 * seconds, a third of the way into the run, once. The events ride on the
 * request as `custom_chaos` (`tests/test_custom_chaos.py`), apart from the
 * config: the config is the architecture, this is what is done to it.
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import {
  FAILURE_SECONDS,
  MAX_INJECTED,
  failedNodes,
  failureEvent,
  failureLabel,
  withFailure,
  withoutFailure,
} from "@/core/lib/clickToFail";
import { exportRequest } from "@/core/lib/exports";
import type { ChaosEventCfg, SimulationConfig } from "@/core/types";

function config(duration: number): SimulationConfig {
  return {
    seed: 42,
    duration,
    topology: { nodes: [], edges: [] },
    traffic: { base_rps: 2, duration },
  };
}

describe("failureEvent", () => {
  it("is a full outage for fifteen seconds, a third of the way in", () => {
    expect(FAILURE_SECONDS).toBe(15);
    expect(failureEvent("db", config(60))).toEqual({
      event_type: "component_failure",
      target: "db",
      intensity: 1,
      start_time: 20,
      duration: 15,
      interval: 60,
    });
    expect(failureEvent("order-service", config(120))).toMatchObject({
      target: "order-service",
      start_time: 40,
      duration: 15,
      interval: 120,
    });
  });

  it("happens once: the next strike would fall after the run", () => {
    for (const duration of [6, 20, 60, 120]) {
      const event = failureEvent("db", config(duration));
      expect(event.start_time + (event.duration ?? 0) + event.interval).toBeGreaterThan(duration);
    }
  });

  it("is shortened to fit a short run", () => {
    expect(failureEvent("db", config(20))).toMatchObject({ start_time: 7, duration: 13, interval: 20 });
    expect(failureEvent("db", config(6))).toMatchObject({ start_time: 2, duration: 4, interval: 6 });
  });
});

describe("failureLabel", () => {
  it("says who fails, when, and for how long", () => {
    expect(failureLabel(failureEvent("db", config(60)))).toBe("db fails at 20s for 15s");
    expect(failureLabel(failureEvent("db", config(20)))).toBe("db fails at 7s for 13s");
  });
});

describe("the injected failures", () => {
  const db = failureEvent("db", config(60));
  const worker = failureEvent("worker", config(60));

  it("adds a failure", () => {
    expect(withFailure([], db)).toEqual([db]);
    expect(withFailure([db], worker)).toEqual([db, worker]);
  });

  it("keeps one failure per node", () => {
    const again: ChaosEventCfg = { ...db, start_time: 5 };
    expect(withFailure([db, worker], again)).toEqual([worker, again]);
  });

  it("holds as many as the API takes, dropping the oldest", () => {
    expect(MAX_INJECTED).toBe(8);
    let list: ChaosEventCfg[] = [];
    for (let i = 0; i < 9; i += 1) list = withFailure(list, failureEvent(`n${i}`, config(60)));
    expect(failedNodes(list)).toEqual(["n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8"]);
  });

  it("removes a node's failure", () => {
    expect(withoutFailure([db, worker], "db")).toEqual([worker]);
    const list = [db];
    expect(withoutFailure(list, "nope")).toBe(list);
  });

  it("never changes the list it was given", () => {
    const list = [db];
    withFailure(list, worker);
    withoutFailure(list, "db");
    expect(list).toEqual([db]);
  });

  it("names the failed nodes", () => {
    expect(failedNodes([db, worker])).toEqual(["db", "worker"]);
    expect(failedNodes([])).toEqual([]);
  });
});

describe("exporting a run with an injected failure", () => {
  const db = failureEvent("db", config(60));

  it("tells the engine about the failure, so the file is the run on screen", () => {
    const request = exportRequest(
      { config: config(60), playbook: "db_failover", customChaos: [db] },
      { typical_seed: null },
      "csv",
    );
    expect(request).toEqual({
      config: config(60),
      playbook: "db_failover",
      format: "csv",
      custom_chaos: [db],
    });
  });

  it("sends no such field for a run without one", () => {
    const plain = exportRequest({ config: config(60), playbook: null }, { typical_seed: null }, "json");
    const empty = exportRequest({ config: config(60), playbook: null, customChaos: [] }, { typical_seed: null }, "json");
    expect("custom_chaos" in plain).toBe(false);
    expect("custom_chaos" in empty).toBe(false);
  });
});
