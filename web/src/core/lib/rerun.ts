/**
 * 1.3g — while a re-run is in flight, only what changed says so.
 *
 * The config patches the moment "Apply" is clicked, but the measured
 * numbers still belong to the OLD size. `staleNodes` names exactly the
 * nodes whose size moved, so the diagram marks only those as re-measured
 * (or not measured, if the re-run failed), and `fixPanelView` tells the
 * Fix-it panel the truth about what it is waiting for instead of
 * "Nothing to change" while the re-run is still deciding that.
 *
 * Pure and dependency-free on purpose: no React, no DOM — Vitest runs it
 * in node.
 */
import type { TopologyNode } from "../types";

export type RemeasureState = "running" | "unmeasured";

/**
 * Names in `current`, in order, that the measured run cannot speak to:
 * the capacity differs from the measured run's, or the measured run did
 * not have the node at all. `[]` before any run has been measured.
 */
export function staleNodes(
  measured: TopologyNode[] | null,
  current: TopologyNode[],
): string[] {
  if (measured === null) return [];
  const byName = new Map(measured.map((n) => [n.name, n] as const));
  return current
    .filter((n) => byName.get(n.name)?.max_capacity !== n.max_capacity)
    .map((n) => n.name);
}

export interface FixPanelInput {
  /** Suggestions not yet applied to the active config. */
  pending: number;
  /** Suggestions the last run proposed, applied or not. */
  suggestions: number;
  /** The last run carries a verified fix outcome. */
  hasOutcome: boolean;
  /** A re-run (or any run) is in flight right now. */
  isPending: boolean;
}

export type FixPanelNote = "rerunning" | "applied" | "nothing" | null;

export interface FixPanelView {
  /** Show the before → after diff table. */
  diff: boolean;
  /** Show the verified before → after outcome. */
  outcome: boolean;
  /** Which sentence to say when neither is on screen. */
  note: FixPanelNote;
}

/**
 * What the Fix-it panel shows and says:
 * - suggestions still pending → the diff (and the outcome, if verified);
 * - applied but not yet measured → the truth about the state — the re-run
 *   in flight ("rerunning"), or it failed and the new size is unmeasured
 *   ("applied");
 * - the run proposed nothing → the outcome alone (capacity cannot fix it),
 *   or "nothing to change".
 */
export function fixPanelView(input: FixPanelInput): FixPanelView {
  const { pending, suggestions, hasOutcome, isPending } = input;
  if (pending > 0) return { diff: true, outcome: hasOutcome, note: null };
  if (suggestions > 0) {
    return { diff: false, outcome: false, note: isPending ? "rerunning" : "applied" };
  }
  return { diff: false, outcome: hasOutcome, note: hasOutcome ? null : "nothing" };
}
