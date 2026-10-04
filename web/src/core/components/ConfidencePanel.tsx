"use client";

import { useState } from "react";
import {
  SEVERITY_BG,
  SEVERITY_TEXT,
  fmtPct,
  fmtSeconds,
  scoreSeverity,
} from "../lib/format";
import type { ProfileTri, ResilienceProfile } from "../types";

interface Props {
  profile: ResilienceProfile;
}

/** One row of the worst/typical/best table. */
interface MetricRow {
  label: string;
  key: keyof ProfileTri;
  fmt: (value: number | null) => string;
}

const METRIC_ROWS: MetricRow[] = [
  {
    label: "Resilience score",
    key: "resilience_score",
    fmt: (v) => (v === null ? "n/a" : v.toFixed(1)),
  },
  { label: "P95 latency", key: "p95_latency", fmt: (v) => fmtSeconds(v) },
  { label: "SLA compliance", key: "sla_compliance", fmt: (v) => fmtPct(v, 1) },
  {
    label: "Cost / request",
    key: "cost_per_completed_request",
    fmt: (v) => (v === null ? "n/a" : `$${v.toFixed(3)}`),
  },
];

/**
 * 0–100 resilience track with the observed worst→best span highlighted
 * (severity of the typical score) and a dot at the typical run.
 */
function RangeBar({ profile }: Props) {
  const worst = profile.worst.resilience_score;
  const typical = profile.typical.resilience_score;
  const best = profile.best.resilience_score;

  if (worst === null || typical === null || best === null) {
    return <p className="text-sm text-ink-dim">no resilience scores in these runs</p>;
  }

  const sev = scoreSeverity(typical);
  const spanLeft = Math.max(0, Math.min(100, worst));
  const spanWidth = Math.max(0, Math.min(100, best) - spanLeft);

  return (
    <div>
      <div className="relative h-2 rounded-full bg-surface-2">
        <div
          className={`absolute inset-y-0 rounded-full ${SEVERITY_BG[sev]}`}
          style={{ left: `${spanLeft}%`, width: `${spanWidth}%` }}
        />
        <div
          className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface-1 bg-ink shadow-card"
          style={{ left: `${Math.max(0, Math.min(100, typical))}%` }}
        />
      </div>
      <div className="mt-2 flex items-baseline justify-between text-sm">
        <span className="text-ink-dim">
          worst <span className="font-mono font-medium text-ink">{worst.toFixed(1)}</span>
        </span>
        <span className={`font-semibold ${SEVERITY_TEXT[sev]}`}>
          typical <span className="font-mono">{typical.toFixed(1)}</span>
        </span>
        <span className="text-ink-dim">
          best <span className="font-mono font-medium text-ink">{best.toFixed(1)}</span>
        </span>
      </div>
    </div>
  );
}

/**
 * Multi-seed confidence readout (roadmap 1.1): how much the verdict moves
 * when the seed moves. Worst/best are per-metric extremes across the runs;
 * typical is the middle run by score.
 */
export function ConfidencePanel({ profile }: Props) {
  const [showPerRun, setShowPerRun] = useState(false);

  return (
    <div className="flex flex-col gap-5">
      <RangeBar profile={profile} />

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-ink-dim">
              <th className="py-2 pr-4 font-medium">Metric</th>
              <th className="py-2 pr-4 font-medium">Worst</th>
              <th className="py-2 pr-4 font-medium">Typical</th>
              <th className="py-2 font-medium">Best</th>
            </tr>
          </thead>
          <tbody>
            {METRIC_ROWS.map((row) => (
              <tr key={row.key} className="border-b border-line/60 last:border-b-0">
                <td className="py-2.5 pr-4 text-sm text-ink">{row.label}</td>
                <td className="py-2.5 pr-4 font-mono text-sm text-ink-dim">
                  {row.fmt(profile.worst[row.key])}
                </td>
                <td className="py-2.5 pr-4 font-mono text-sm font-medium text-ink">
                  {row.fmt(profile.typical[row.key])}
                </td>
                <td className="py-2.5 font-mono text-sm text-ink-dim">
                  {row.fmt(profile.best[row.key])}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {profile.per_run.length > 1 ? (
        <>
          <button
            type="button"
            onClick={() => setShowPerRun((v) => !v)}
            aria-expanded={showPerRun}
            className="self-start text-[13px] font-medium text-accent outline-none transition-colors hover:text-accent-2 focus-visible:underline"
          >
            {showPerRun
              ? "hide per-run breakdown"
              : `show per-run breakdown (${profile.per_run.length})`}
          </button>

          {showPerRun ? (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-ink-dim">
                    <th className="py-2 pr-4 font-medium">Seed</th>
                    <th className="py-2 pr-4 font-medium">Score</th>
                    <th className="py-2 pr-4 font-medium">P95</th>
                    <th className="py-2 pr-4 font-medium">SLA</th>
                    <th className="py-2 font-medium">Completion</th>
                  </tr>
                </thead>
                <tbody>
                  {profile.per_run.map((run, i) => (
                    <tr key={`${run.seed ?? "run"}-${i}`} className="border-b border-line/60 last:border-b-0">
                      <td className="py-2 pr-4 font-mono text-sm text-ink-dim">
                        {run.seed ?? "—"}
                      </td>
                      <td className="py-2 pr-4 font-mono text-sm text-ink">
                        {run.score === null ? "n/a" : run.score.toFixed(1)}
                      </td>
                      <td className="py-2 pr-4 font-mono text-sm text-ink">
                        {fmtSeconds(run.p95)}
                      </td>
                      <td className="py-2 pr-4 font-mono text-sm text-ink">
                        {fmtPct(run.sla, 1)}
                      </td>
                      <td className="py-2 font-mono text-sm text-ink">
                        {fmtPct(run.completion, 1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}

      <p className="text-xs text-ink-dim">
        typical = middle run by score · worst/best are per-metric extremes
        {profile.score_spread !== null
          ? ` · score spread across seeds: ${profile.score_spread.toFixed(1)}`
          : ""}
      </p>
    </div>
  );
}
