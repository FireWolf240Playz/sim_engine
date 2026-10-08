/**
 * The "what we changed" log: every Apply & re-run, with the run before, the
 * engine's verified prediction, and the re-run that actually came back.
 * Pure functions over plain data; `RunStateContext` only holds the array.
 */
import type { FindingRef, FixOutcome, Suggestion, Summary } from "../types";

/** The parts of one run a before → after comparison needs. */
export interface RunSnapshot {
  score: number | null;
  band: string | null;
  /** crit/warn findings, in engine order. */
  serious: FindingRef[];
}

export interface FixRecord {
  /** 1-based apply number within the current architecture. */
  step: number;
  /** The incident the fix was found under, or null for a clean run. */
  playbook: string | null;
  changes: Array<{ node: string; param: string; before: number; after: number }>;
  monthlyDelta: number | null;
  before: RunSnapshot;
  /** The engine's verified before → after for this exact change set. */
  predicted: FixOutcome | null;
  /** The re-run's result; null while it is in flight. */
  after: RunSnapshot | null;
  /** True when the re-run failed (engine error), so `after` never arrived. */
  failed: boolean;
}

export function snapshot(summary: Summary): RunSnapshot {
  return {
    score: summary.resilience_score,
    band: summary.score_explanation?.band ?? null,
    serious: (summary.findings ?? [])
      .filter((f) => f.severity === "crit" || f.severity === "warn")
      .map((f) => ({
        id: f.id,
        node: f.node ?? null,
        severity: f.severity,
        title: f.title ?? f.text,
      })),
  };
}

/** Append a pending record for an apply about to re-run. */
export function startRecord(
  history: FixRecord[],
  summary: Summary,
  suggestions: Suggestion[],
  playbook: string | null,
): FixRecord[] {
  const priced = suggestions
    .map((s) => s.est_monthly_delta)
    .filter((d): d is number => d !== null);
  // The prediction covers the whole set; for a partial apply it does not hold.
  const all = summary.suggestions ?? [];
  const isWholeSet =
    all.length === suggestions.length &&
    all.every((s) => suggestions.some((t) => t.node === s.node && t.proposed === s.proposed));
  return [
    ...history,
    {
      step: history.length + 1,
      playbook,
      changes: suggestions.map((s) => ({
        node: s.node,
        param: s.param,
        before: s.current,
        after: s.proposed,
      })),
      monthlyDelta: priced.length ? priced.reduce((a, b) => a + b, 0) : null,
      before: snapshot(summary),
      predicted: isWholeSet ? (summary.fix_outcome ?? null) : null,
      after: null,
      failed: false,
    },
  ];
}

/** Fill the newest pending record with the re-run that came back. */
export function completeRecord(history: FixRecord[], summary: Summary | null): FixRecord[] {
  let index = -1;
  for (let i = history.length - 1; i >= 0; i -= 1) {
    if (history[i].after === null && !history[i].failed) {
      index = i;
      break;
    }
  }
  if (index === -1) return history;
  const next = history.slice();
  next[index] =
    summary === null
      ? { ...history[index], failed: true }
      : { ...history[index], after: snapshot(summary) };
  return next;
}

function key(f: FindingRef): string {
  return `${f.id}@${f.node ?? ""}`;
}

/** Findings the re-run cleared, and any it introduced. */
export function recordDelta(record: FixRecord): { cleared: FindingRef[]; introduced: FindingRef[] } {
  if (!record.after) return { cleared: [], introduced: [] };
  const before = new Set(record.before.serious.map(key));
  const after = new Set(record.after.serious.map(key));
  return {
    cleared: record.before.serious.filter((f) => !after.has(key(f))),
    introduced: record.after.serious.filter((f) => !before.has(key(f))),
  };
}

function fmtScore(value: number | null): string {
  return value === null ? "n/a" : String(value);
}

function fmtMoney(value: number): string {
  const sign = value < 0 ? "−" : "+";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}/mo`;
}

/** One plain-text line per record — the report's "Changes applied" list. */
export function describeRecord(record: FixRecord): string {
  const changes = record.changes.map((c) => `${c.node} ${c.before}→${c.after}`).join(", ");
  const where = record.playbook ? ` under ${record.playbook.replace(/_/g, " ")}` : "";
  const parts = [`Fix ${record.step}${where}: ${changes}`];
  if (record.failed) {
    parts.push("re-run failed");
  } else if (!record.after) {
    parts.push("re-running…");
  } else {
    const band = record.after.band ? ` ${record.after.band}` : "";
    parts.push(
      `score ${fmtScore(record.before.score)} → ${fmtScore(record.after.score)}${band}`,
    );
    const { cleared, introduced } = recordDelta(record);
    if (cleared.length) parts.push(`cleared ${cleared.map((f) => f.title).join(", ")}`);
    if (introduced.length) parts.push(`NEW ${introduced.map((f) => f.title).join(", ")}`);
  }
  if (record.monthlyDelta !== null) parts.push(fmtMoney(record.monthlyDelta));
  return parts.join(" · ");
}

/** The full log as text, for "Copy verdict". Empty string when nothing was applied. */
export function historyText(history: FixRecord[]): string {
  if (!history.length) return "";
  return [
    "Changes applied (each verified by a re-run):",
    ...history.map((r) => `  ${describeRecord(r)}`),
  ].join("\n");
}
