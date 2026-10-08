/**
 * Roadmap 1.3 acceptance contract, frontend half: apply & re-run plus the
 * Fix diff. This file is the spec: make it pass, do not edit it.
 * The task card is `.agents/tasks/1.3-right-sizing.md`.
 *
 * The YAML round trip crosses languages without a YAML dependency: the last
 * test writes `configToYaml(...)` to `tests/fixtures/suggestions_applied.yaml`
 * (a file snapshot), and `tests/test_suggestions.py` loads that file through
 * Pydantic, so the engine itself proves the YAML the UI hands out is valid.
 */
import { describe, expect, it } from "vitest";
import {
  applySuggestions,
  buildFixDiff,
  configToYaml,
  fixYamlSnippet,
  pendingSuggestions,
  terraformSnippet,
} from "@/core/lib/suggestions";
import type { SimulationConfig, Suggestion } from "@/core/types";

function makeConfig(): SimulationConfig {
  return {
    seed: 7,
    duration: 60,
    metrics_interval: 1,
    sla_target: 2.5,
    topology: {
      nodes: [
        { name: "lb", role: "load_balancer", max_capacity: 12, service_time: 0.05, cost_per_hour: 0.1 },
        { name: "app-worker", role: "worker", max_capacity: 2, service_time: 0.4, cost_per_hour: 0.1 },
        { name: "db", role: "database", max_capacity: 4, service_time: 0.2 },
      ],
      edges: [
        { source: "lb", target: "app-worker", probability: 1 },
        { source: "app-worker", target: "db", probability: 1 },
      ],
    },
    traffic: { base_rps: 5, duration: 60, spike_rps: null },
    chaos: [
      {
        event_type: "component_failure",
        intensity: 1,
        start_time: 20,
        interval: 1000,
        duration: 5,
        target: "db",
      },
    ],
  };
}

const RAISE: Suggestion = {
  node: "app-worker",
  param: "max_capacity",
  current: 2,
  proposed: 3,
  reason: "undersized at 97% mean utilization",
  est_monthly_delta: 72,
};

const CUT: Suggestion = {
  node: "lb",
  param: "max_capacity",
  current: 12,
  proposed: 9,
  reason: "oversized at 10% mean utilization",
  est_monthly_delta: -216,
};

function capacities(config: SimulationConfig): Record<string, number> {
  return Object.fromEntries(config.topology.nodes.map((n) => [n.name, n.max_capacity]));
}

describe("applySuggestions", () => {
  it("changes only the target node's param", () => {
    const before = makeConfig();
    const after = applySuggestions(before, [RAISE]);
    expect(capacities(after)).toEqual({ lb: 12, "app-worker": 3, db: 4 });
    const { nodes: beforeNodes, ...beforeRest } = before.topology;
    const { nodes: afterNodes, ...afterRest } = after.topology;
    expect(afterRest).toEqual(beforeRest);
    expect(afterNodes[1]).toEqual({ ...beforeNodes[1], max_capacity: 3 });
    expect(afterNodes[0]).toEqual(beforeNodes[0]);
    expect(afterNodes[2]).toEqual(beforeNodes[2]);
    expect({ ...after, topology: null }).toEqual({ ...before, topology: null });
  });

  it("never mutates the config it was given", () => {
    const before = makeConfig();
    const snapshot = structuredClone(before);
    const after = applySuggestions(before, [RAISE, CUT]);
    expect(before).toEqual(snapshot);
    expect(after).not.toBe(before);
    expect(after.topology.nodes).not.toBe(before.topology.nodes);
  });

  it("applies a whole set at once", () => {
    expect(capacities(applySuggestions(makeConfig(), [RAISE, CUT]))).toEqual({
      lb: 9,
      "app-worker": 3,
      db: 4,
    });
  });

  it("is a no-op for an empty list and ignores unknown nodes", () => {
    const ghost: Suggestion = { ...RAISE, node: "ghost" };
    expect(applySuggestions(makeConfig(), [])).toEqual(makeConfig());
    expect(applySuggestions(makeConfig(), [ghost])).toEqual(makeConfig());
  });

  it("sets the proposed value, so applying twice changes nothing more", () => {
    const once = applySuggestions(makeConfig(), [RAISE]);
    expect(applySuggestions(once, [RAISE])).toEqual(once);
  });
});

describe("buildFixDiff", () => {
  it("lists before → after per suggestion, in suggestion order, with the total", () => {
    const diff = buildFixDiff(makeConfig(), [RAISE, CUT]);
    expect(diff.rows).toEqual([
      { node: "app-worker", param: "max_capacity", before: 2, after: 3, monthlyDelta: 72 },
      { node: "lb", param: "max_capacity", before: 12, after: 9, monthlyDelta: -216 },
    ]);
    expect(diff.totalMonthlyDelta).toBeCloseTo(-144);
  });

  it("totals only the known deltas, and is null when none is known", () => {
    const unpriced: Suggestion = { ...CUT, est_monthly_delta: null };
    expect(buildFixDiff(makeConfig(), [RAISE, unpriced]).totalMonthlyDelta).toBeCloseTo(72);
    expect(buildFixDiff(makeConfig(), [unpriced]).totalMonthlyDelta).toBeNull();
    expect(buildFixDiff(makeConfig(), [])).toEqual({ rows: [], totalMonthlyDelta: null });
  });
});

describe("snippets", () => {
  it("fixYamlSnippet shows only the changed nodes, deterministically", () => {
    const snippet = fixYamlSnippet(makeConfig(), [RAISE]);
    expect(snippet).toBe(fixYamlSnippet(makeConfig(), [RAISE]));
    expect(snippet).toContain("app-worker");
    expect(snippet).toContain("max_capacity: 3");
    expect(snippet).not.toMatch(/name: lb\b/);
    expect(snippet).not.toMatch(/name: db\b/);
    expect(fixYamlSnippet(makeConfig(), [])).toBe("");
  });

  it("terraformSnippet is labeled illustrative and carries the change", () => {
    const tf = terraformSnippet(makeConfig(), [RAISE]);
    expect(tf).toMatch(/illustrative/i);
    expect(tf).toContain("app-worker");
    expect(tf).toMatch(/\b3\b/);
    expect(terraformSnippet(makeConfig(), [RAISE])).toBe(tf);
  });

  it("configToYaml is deterministic and round-trips through the engine", async () => {
    const applied = applySuggestions(makeConfig(), [RAISE]);
    const yaml = configToYaml(applied);
    expect(configToYaml(structuredClone(applied))).toBe(yaml);
    // Validated by tests/test_suggestions.py::test_frontend_yaml_round_trips_into_a_valid_config.
    await expect(yaml).toMatchFileSnapshot("../../tests/fixtures/suggestions_applied.yaml");
  });
});

// ---------------------------------------------------------------------------
// Review round 2 (2026-10-08): defects found in attempt 1
// ---------------------------------------------------------------------------

describe("pendingSuggestions (stale-after-apply guard)", () => {
  // After "Apply & re-run" the config is patched at once, but the panel still
  // holds the previous run's suggestions until the re-run lands, and forever
  // if it fails. Those rows then read "×3 → ×3", and Apply re-applies a no-op.
  it("drops suggestions the config already carries", () => {
    const applied = applySuggestions(makeConfig(), [RAISE]);
    expect(pendingSuggestions(applied, [RAISE, CUT])).toEqual([CUT]);
    expect(pendingSuggestions(makeConfig(), [RAISE, CUT])).toEqual([RAISE, CUT]);
  });

  it("drops suggestions for nodes the config does not have", () => {
    expect(pendingSuggestions(makeConfig(), [{ ...RAISE, node: "ghost" }])).toEqual([]);
  });

  it("feeds buildFixDiff, so applied rows leave the diff", () => {
    const applied = applySuggestions(makeConfig(), [RAISE]);
    expect(buildFixDiff(applied, [RAISE, CUT]).rows.map((r) => r.node)).toEqual(["lb"]);
    expect(buildFixDiff(applied, [RAISE]).totalMonthlyDelta).toBeNull();
  });
});

describe("terraformSnippet resource types", () => {
  it("names a real provider resource for a cache", () => {
    const config = makeConfig();
    config.topology.nodes.push({
      name: "redis",
      role: "cache",
      max_capacity: 8,
      service_time: 0.01,
      hit_rate: 0.9,
    });
    const tf = terraformSnippet(config, [{ ...CUT, node: "redis", current: 8, proposed: 6 }]);
    // `aws_elasticache` is not a resource type; the cluster is.
    expect(tf).toContain('resource "aws_elasticache_cluster" "redis"');
  });
});

describe("configToYaml robustness", () => {
  it("does not throw on an empty mapping inside a list", () => {
    const config = { ...makeConfig(), chaos: [{}] } as unknown as SimulationConfig;
    expect(() => configToYaml(config)).not.toThrow();
    expect(configToYaml(config)).toContain("chaos:\n  - {}");
  });
});
