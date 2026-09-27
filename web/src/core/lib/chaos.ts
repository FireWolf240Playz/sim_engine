import type { ChaosEventCfg, ChaosWindow } from "../types";

/**
 * Expand a chaos schedule into concrete [start, end] windows for the
 * timeline. Mirrors the engine's semantics: an event fires at
 * `start_time`, then again every `interval` seconds until the horizon;
 * each disruption lasts `duration` (defaulting to the gap).
 */
export function chaosWindows(
  events: ChaosEventCfg[],
  horizon: number,
): ChaosWindow[] {
  const windows: ChaosWindow[] = [];
  for (const event of events) {
    const span = event.duration ?? event.interval;
    for (let t = event.start_time; t < horizon; t += event.interval) {
      windows.push({
        start: t,
        end: Math.min(horizon, t + span),
        event_type: event.event_type,
        intensity: event.intensity,
      });
    }
  }
  return windows.sort((a, b) => a.start - b.start);
}
