"use client";

import { useState, type ReactNode } from "react";
import type { Finding, Summary } from "../types";
import type { FixRecord } from "../lib/fixHistory";
import {
  SEVERITY_BG,
  SEVERITY_TEXT,
  verdictSeverity,
  type Severity,
} from "../lib/format";
import { findingKeys } from "../lib/verdictView";
import { SeverityIcon } from "./SeverityIcon";
import { FindingCard, chipCls, findingTone } from "./FindingCard";
import { ScoreGauge } from "./ScoreGauge";
import { ScoreTermsStrip } from "./ScoreTerms";
import { VerdictReportModal } from "./VerdictReportModal";

const TONE_RANK: Record<Severity, number> = { crit: 0, warn: 1, ok: 2 };

function worstTone(rows: Finding[]): Severity {
  let worst: Severity = "ok";
  for (const row of rows) {
    const tone = findingTone(row.severity);
    if (TONE_RANK[tone] < TONE_RANK[worst]) worst = tone;
  }
  return worst;
}

function CountChip({ tone, children }: { tone: Severity; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-1 text-[11px] font-semibold ${chipCls(tone)}`}>
      {children}
    </span>
  );
}

/**
 * The Verdict (roadmap 1.2, "rich verdict" rework) — a report, not a
 * text stack:
 *
 *   ┌────────────────────────────────────────────────────────┐
 *   │ icon  Verdict  ·band·                context  [report] │
 *   │ ┌────┐                                                 │
 *   │ │gauge│  one-sentence headline                         │
 *   │ └────┘  severity count chips                           │
 *   │        SLA +54  Completion +39 …  score 71/100         │
 *   │ ┌─ finding card ───────────────────────────────────┐   │
 *   │ └─ finding card ───────────────────────────────────┘   │
 *   └────────────────────────────────────────────────────────┘
 *
 * Every word comes from the deterministic BE contract
 * (`sim_core/findings.py` + `sim_core/score.py`); this component only
 * formats. The deep detail lives one click away in `VerdictReportModal`
 * — the panel stays scannable.
 *
 * BE tokens are `info|warn|crit`; the FE severity system is
 * `ok|warn|crit` — `info` maps to `ok` (the green "STABLE" lane).
 */
export function VerdictPanel({
  summary,
  context,
  architectureLabel,
  seeds,
  fixHistory,
}: {
  summary: Summary | null;
  /** The apply → re-run log, shown and copied in the full report. */
  fixHistory?: FixRecord[];
  /** e.g. "under db failover" or "on the clean run" — the incident in context. */
  context?: string;
  /** e.g. "demo architecture" or the user's named architecture. */
  architectureLabel?: string;
  /** Multi-seed confidence run (roadmap 1.1) — present when `n_seeds > 1`. */
  seeds?: number[] | null;
}) {
  const [reportOpen, setReportOpen] = useState(false);

  const rows = summary?.findings ?? [];
  const score = summary?.resilience_score ?? null;
  const explanation = summary?.score_explanation ?? null;
  const headline = summary?.verdict_headline ?? null;
  // One colour for the whole verdict: bar, icon, band word and gauge. A
  // high score next to a critical finding reads red, never green.
  const tone: Severity | "neutral" = summary
    ? verdictSeverity(score, rows.length ? worstTone(rows) : null)
    : "neutral";

  const critCount = rows.filter((row) => row.severity === "crit").length;
  const warnCount = rows.filter((row) => row.severity === "warn").length;

  const bandWord = explanation?.band ?? null;
  const bandClass = tone !== "neutral" ? SEVERITY_TEXT[tone] : "text-ink-dim";
  const cappedBy = explanation?.band_capped_by ?? null;

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface-1 shadow-card">
      {/* Overall-tone accent: the whole verdict reads as one color story. */}
      <div className={`h-1 w-full ${tone === "neutral" ? "bg-surface-2" : SEVERITY_BG[tone]}`} />

      <div className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex items-center gap-3">
            <span
              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                tone === "neutral" ? "bg-surface-2 text-ink-dim" : chipCls(tone)
              }`}
            >
              <SeverityIcon severity={tone === "neutral" ? "ok" : tone} className="h-3.5 w-3.5" />
            </span>
            <h2 className="text-[15px] font-semibold text-ink">Verdict</h2>
            {bandWord ? (
              <span
                className={`text-[11px] font-semibold tracking-[0.08em] ${bandClass}`}
                title={
                  cappedBy
                    ? `Capped by the ${cappedBy.replace(/_/g, " ")} finding — the score alone would read higher`
                    : undefined
                }
              >
                {bandWord}
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-3">
            {context ? <span className="text-xs text-ink-dim">{context}</span> : null}
            {summary ? (
              <button
                type="button"
                onClick={() => setReportOpen(true)}
                className="h-8 rounded-lg border border-line bg-surface-1 px-3 text-[13px] font-medium text-ink transition-colors outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent"
              >
                Open full report
              </button>
            ) : null}
          </div>
        </div>

        {summary ? (
          <>
            <div className="mt-5 grid items-center gap-x-8 gap-y-4 md:grid-cols-[auto_1fr]">
              {score !== null ? (
                <ScoreGauge score={score} severity={tone === "neutral" ? undefined : tone} />
              ) : null}
              <div className="min-w-0">
                {headline ? (
                  <p className="max-w-2xl text-[17px] font-medium leading-7 text-ink">{headline}</p>
                ) : null}

                {rows.length ? (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="Finding counts">
                    {critCount > 0 ? (
                      <CountChip tone="crit">
                        {critCount} critical{critCount > 1 ? "s" : ""}
                      </CountChip>
                    ) : null}
                    {warnCount > 0 ? (
                      <CountChip tone="warn">
                        {warnCount} structural risk{warnCount > 1 ? "s" : ""}
                      </CountChip>
                    ) : null}
                    {critCount === 0 && warnCount === 0 ? <CountChip tone="ok">all clear</CountChip> : null}
                  </div>
                ) : null}

                {explanation ? (
                  <div className="mt-4">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-ink-dim">
                      How the score is built
                    </p>
                    <div className="mt-2">
                      <ScoreTermsStrip explanation={explanation} />
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="mt-5 flex flex-col gap-3">
              {findingKeys(rows).map((key, i) => (
                <FindingCard key={key} finding={rows[i]} />
              ))}
            </div>
          </>
        ) : (
          <div className="mt-4 flex items-start gap-3 px-1 py-1">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-ink-dim">
              <SeverityIcon severity="ok" className="h-3 w-3" />
            </span>
            <p className="text-sm leading-6 text-ink-dim">
              Run an incident to get a plain-English verdict for this architecture — what
              breaks, what it costs, and how to fix it.
            </p>
          </div>
        )}
      </div>

      {/* Mounted only while open: every open gets a fresh dialog (and a
          fresh "Copy verdict" button), and unmount restores focus to the
          trigger via the Modal's cleanup. */}
      {summary && reportOpen ? (
        <VerdictReportModal
          open
          onClose={() => setReportOpen(false)}
          summary={summary}
          context={context}
          architectureLabel={architectureLabel}
          seeds={seeds}
          fixHistory={fixHistory}
        />
      ) : null}
    </div>
  );
}
