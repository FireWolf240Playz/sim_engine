import type { ComponentRole, SimulationConfig } from "../types";

/**
 * The calibrated demo topology — the single source of truth for the
 * Simulator and Compare views.
 *
 * Shape: `lb → worker → {cache → db}` with a fan-out `worker → pricing_api`
 * (a third-party dependency), so all four incidents have a target:
 * database, whole path, cache, external_api.
 *
 * Numbers were grid-searched against the real engine (seed 42, 60 s) so that
 * the CLEAN run reads healthy-but-tense (98/100, db right-sized at ~66%
 * mean utilization) while every incident visibly degrades it:
 *
 *   db_failover                 98 → 81   (db loses all slots 5 s, half 30 s)
 *   cross_region_latency_spike  98 → 87   (every hop ~80% slower, 15 s)
 *   cache_eviction_storm        98 → 71   (hit-rate −90%, 20 s, DB absorbs)
 *   dependency_timeout_cascade  98 → 50   (pricing_api down 10 s + tail)
 *
 * Changing any of these invalidates that calibration — re-run the engine
 * before locking new numbers.
 */
export const DEMO_CONFIG: SimulationConfig = {
  seed: 42,
  duration: 60,
  metrics_interval: 2,
  sla_target: 10,
  topology: {
    nodes: [
      { name: "lb", role: "load_balancer", max_capacity: 10, service_time: 0.3, cost_per_hour: 0.04 },
      { name: "worker", role: "worker", max_capacity: 6, service_time: 1.0, cost_per_hour: 0.05 },
      { name: "cache", role: "cache", max_capacity: 8, service_time: 0.2, hit_rate: 0.7, cost_per_hour: 0.03 },
      { name: "db", role: "database", max_capacity: 3, service_time: 3.0, cost_per_hour: 0.06 },
      { name: "pricing_api", role: "external_api", max_capacity: 4, service_time: 1.2, cost_per_hour: 0.02 },
    ],
    edges: [
      { source: "lb", target: "worker", probability: 1.0 },
      { source: "worker", target: "cache", probability: 1.0 },
      { source: "worker", target: "pricing_api", probability: 1.0 },
      { source: "cache", target: "db", probability: 1.0 },
    ],
  },
  traffic: { base_rps: 2.0, duration: 60 },
  chaos: [],
};

/** One-line meta for headers / empty states. */
export const DEMO_META: string = "lb ×10 → worker ×6 → {cache ×8 → db ×3, pricing_api ×4} · 2.0 rps · SLA 10s";

/**
 * Which nodes a playbook hits, mirroring `sim_core/playbooks.py` (targeting
 * is by role; `cross_region_latency_spike` affects the whole path).
 * Returns the node names to flag in the diagram; an empty array with
 * `wholePath=true` means every node.
 */
export function playbookTargets(
  playbook: string | null,
  nodes: SimulationConfig["topology"]["nodes"],
): { names: string[]; wholePath: boolean } {
  if (!playbook) return { names: [], wholePath: false };

  const byRole = (role: ComponentRole): string[] =>
    nodes.filter((n) => n.role === role).map((n) => n.name);

  switch (playbook) {
    case "db_failover":
      return { names: byRole("database"), wholePath: false };
    case "cache_eviction_storm":
      return { names: byRole("cache"), wholePath: false };
    case "dependency_timeout_cascade": {
      // external_api if present, else the database (same fallback as the engine);
      // the latency tail of the cascade slows the whole path too
      const api = byRole("external_api");
      const names = api.length ? api : byRole("database");
      return { names, wholePath: true };
    }
    case "cross_region_latency_spike":
      return { names: nodes.map((n) => n.name), wholePath: true };
    default:
      return { names: [], wholePath: false };
  }
}
