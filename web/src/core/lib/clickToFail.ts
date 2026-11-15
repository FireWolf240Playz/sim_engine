/**
 * 3.3 — "inject failure here": the failure a click on a node builds.
 *
 * A full outage of that node for 15 seconds, a third of the way into the
 * run, once. The events ride on the request as `custom_chaos`, apart from
 * the config: the config is the architecture, this is what is done to it
 * (`api/routes/simulate.py::effective_config`).
 *
 * Pure and dependency-free: no React, no DOM. Nothing here mutates its input.
 */
import type { ChaosEventCfg, SimulationConfig } from "../types";

/** How long an injected outage lasts (shortened to fit a short run). */
export const FAILURE_SECONDS = 15;

/** The most failures one request may carry (`api/schemas.py::MAX_CUSTOM_CHAOS`). */
export const MAX_INJECTED = 8;

/**
 * A full outage of `node`, a third of the way in. The interval is the run's
 * length, so the next strike would fall after the run: it happens once.
 */
export function failureEvent(node: string, config: SimulationConfig): ChaosEventCfg {
  const start = Math.round(config.duration / 3);
  return {
    event_type: "component_failure",
    target: node,
    intensity: 1,
    start_time: start,
    duration: Math.min(FAILURE_SECONDS, config.duration - start),
    interval: config.duration,
  };
}

/** `db fails at 20s for 15s`. */
export function failureLabel(event: ChaosEventCfg): string {
  return `${event.target} fails at ${event.start_time}s for ${event.duration ?? event.interval}s`;
}

/** The list with `event` added: one failure per node, the oldest dropped past the limit. */
export function withFailure(list: readonly ChaosEventCfg[], event: ChaosEventCfg): ChaosEventCfg[] {
  return [...list.filter((other) => other.target !== event.target), event].slice(-MAX_INJECTED);
}

/** The list without `node`'s failure; the same list when it had none. */
export function withoutFailure<T extends readonly ChaosEventCfg[]>(list: T, node: string): T | ChaosEventCfg[] {
  return list.some((event) => event.target === node)
    ? list.filter((event) => event.target !== node)
    : list;
}

/** The nodes with an injected failure, in order. */
export function failedNodes(list: readonly ChaosEventCfg[]): string[] {
  return list.flatMap((event) => (event.target ? [event.target] : []));
}
