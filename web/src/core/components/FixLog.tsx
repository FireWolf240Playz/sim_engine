"use client";

import type { FindingRef, FixOutcome } from "../types";
import { recordDelta, type FixRecord } from "../lib/fixHistory";

function names(refs: FindingRef[]): string {
  return refs.map((f) => (f.node ? `${f.title} (${f.node})` : f.title)).join(", ");
}

function fmtScore(value: number | null): string {
  return value === null ? "n/a" : String(value);
}

function fmtMonthly(value: number | null): string | null {
  if (value === null) return null;
  const sign = value < 0 ? "−" : "+";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}/mo`;
}

/**
 * What "Apply & re-run" will do, before the user clicks: the engine already
 * simulated the fixed config to verify it, so this is a measured result,
 * not a promise. Findings no capacity clears are named, never hidden.
 */
export function FixOutcomePreview({ outcome }: { outcome: FixOutcome }) {
  const money = fmtMonthly(outcome.monthly_delta);
  return (
    <div className="mb-4 rounded-lg border border-line bg-surface-0/60 px-4 py-3 text-[13px] leading-6">
      <p className="text-ink">
        <span className="font-semibold">Applying all of it:</span> score{" "}
        <span className="font-mono">{fmtScore(outcome.score_before)}</span> →{" "}
        <span className="font-mono font-semibold">{fmtScore(outcome.score_after)}</span>
        {outcome.band_before && outcome.band_after && outcome.band_before !== outcome.band_after
          ? ` · ${outcome.band_before} → ${outcome.band_after}`
          : null}
        {money ? ` · ${money}` : null}
      </p>
      {outcome.resolved.length ? (
        <p className="text-sev-ok">Clears {names(outcome.resolved)}.</p>
      ) : null}
      {outcome.unfixed.length ? (
        <p className="text-sev-warn">
          Capacity can&apos;t fix {names(outcome.unfixed)}: the cause is not a too-small node
          (injected latency, timeouts, or retries). Change the design, not the size.
        </p>
      ) : null}
      <p className="text-[11.5px] text-ink-dim">
        Measured by simulating the fixed config on seed {outcome.seed}.
      </p>
    </div>
  );
}

/**
 * The change log: every apply, the run before it, and the re-run that came
 * back. "matches the prediction" ties each re-run to the preview above.
 */
export function FixHistoryList({ history }: { history: FixRecord[] }) {
  return (
    <ol className="flex flex-col gap-3">
      {history.map((record) => {
        const { cleared, introduced } = recordDelta(record);
        const money = fmtMonthly(record.monthlyDelta);
        const predicted = record.predicted?.score_after ?? null;
        const matches =
          record.after !== null && predicted !== null && record.after.score === predicted;
        return (
          <li
            key={record.step}
            className="rounded-lg border border-line bg-surface-0/60 px-4 py-3 text-[13px] leading-6"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-4">
              <p className="font-semibold text-ink">
                Fix {record.step}
                {record.playbook ? (
                  <span className="font-normal text-ink-dim">
                    {" "}
                    under {record.playbook.replace(/_/g, " ")}
                  </span>
                ) : null}
              </p>
              {money ? <span className="font-mono text-ink-dim">{money}</span> : null}
            </div>
            <p className="font-mono text-ink-dim">
              {record.changes.map((c) => `${c.node} ${c.before}→${c.after}`).join(" · ")}
            </p>
            {record.failed ? (
              <p className="text-sev-crit">The re-run failed; nothing was measured.</p>
            ) : record.after === null ? (
              <p className="text-ink-dim">Re-running…</p>
            ) : (
              <>
                <p className="text-ink">
                  score <span className="font-mono">{fmtScore(record.before.score)}</span> →{" "}
                  <span className="font-mono font-semibold">{fmtScore(record.after.score)}</span>
                  {record.after.band ? ` · ${record.after.band}` : null}
                  {matches ? (
                    <span className="text-ink-dim"> · matches the prediction</span>
                  ) : null}
                </p>
                {cleared.length ? <p className="text-sev-ok">Cleared {names(cleared)}.</p> : null}
                {introduced.length ? (
                  <p className="text-sev-crit">New: {names(introduced)}.</p>
                ) : null}
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}
