import type { ChaosEventCfg, ChaosWindow } from "../types";

/**
 * Hard ceiling on the windows one event may expand into. A chaos
 * schedule that would paint more bands than this is unreadable anyway,
 * and the cap keeps a hostile or malformed config from producing a
 * multi-million element array.
 */
const MAX_WINDOWS_PER_EVENT = 512;

/**
 * Expand a chaos schedule into concrete [start, end] windows for the
 * timeline. Mirrors the engine's semantics: an event fires at
 * `start_time`, then again every `interval` seconds until the horizon;
 * each disruption lasts `duration` (defaulting to the gap).
 *
 * Defensive by design. The input is response/import data rather than
 * literals, so a non-finite or non-positive `interval` is possible, and
 * the obvious `t += interval` loop would then never advance — freezing
 * the tab. Such an event is emitted as the single window it describes
 * and never repeated.
 */
export function chaosWindows(
  events: ChaosEventCfg[],
  horizon: number,
): ChaosWindow[] {
  const windows: ChaosWindow[] = [];
  const end = Number.isFinite(horizon) ? horizon : 0;

  for (const event of events) {
    const start = Number.isFinite(event.start_time) ? event.start_time : 0;
    if (start >= end) continue;

    const interval =
      Number.isFinite(event.interval) && event.interval > 0 ? event.interval : null;
    const rawSpan = event.duration ?? interval ?? 0;
    const span = Number.isFinite(rawSpan) && rawSpan > 0 ? rawSpan : 0;

    const push = (from: number) => {
      windows.push({
        start: from,
        end: Math.min(end, from + span),
        event_type: event.event_type,
        intensity: event.intensity,
      });
    };

    // A non-repeating (or malformed-interval) event fires exactly once.
    if (interval === null) {
      push(start);
      continue;
    }

    let fired = 0;
    for (let t = start; t < end && fired < MAX_WINDOWS_PER_EVENT; t += interval) {
      push(t);
      fired += 1;
    }
  }

  return windows.sort((a, b) => a.start - b.start);
}
