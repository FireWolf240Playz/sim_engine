"use client";

import { useEffect, useRef, useState } from "react";
import type { Summary } from "../types";
import { buildVerdictText } from "../lib/verdict";
import { FindingCard } from "./FindingCard";
import { Modal } from "./Modal";
import { ScoreTermsTable } from "./ScoreTerms";

const TITLE_ID = "eleven-verdict-report-title";

function SectionLabel({ children }: { children: string }) {
  return (
    <h3 className="text-[10px] font-semibold uppercase tracking-[0.1em] text-ink-dim">{children}</h3>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 12 12" className="h-3.5 w-3.5" aria-hidden="true" focusable="false">
      <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function contextPairs(
  summary: Summary,
  architectureLabel: string | undefined,
  context: string | undefined,
  seeds: number[] | null | undefined,
): Array<{ label: string; value: string }> {
  const pairs: Array<{ label: string; value: string }> = [];
  if (architectureLabel) pairs.push({ label: "Architecture", value: architectureLabel });
  if (context) pairs.push({ label: "Scenario", value: context });
  pairs.push({
    label: "Seeds",
    value: seeds && seeds.length > 1 ? `${seeds.length} (${seeds.join(", ")})` : "single run",
  });
  pairs.push({
    label: "SLA target",
    value: summary.sla_target ? `${summary.sla_target.toFixed(1)}s` : "not set",
  });
  pairs.push({ label: "Requests", value: String(summary.requests) });
  return pairs;
}

/**
 * The "full report" surface (roadmap 1.2 rework): the complete verdict —
 * score math, every finding, the run context, and a one-click plain-text
 * copy — inside an accessible modal so the detail never stacks over the
 * panel. All content is deterministic BE output; this component only
 * formats.
 */
export function VerdictReportModal({
  open,
  onClose,
  summary,
  context,
  architectureLabel,
  seeds,
}: {
  open: boolean;
  onClose: () => void;
  summary: Summary;
  context?: string;
  architectureLabel?: string;
  seeds?: number[] | null;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  // Clear any pending "Copied" reset timer on unmount. (The feedback
  // itself resets naturally: the parent mounts this dialog fresh on
  // every open.)
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  async function copyVerdict() {
    const text = buildVerdictText(summary, { architectureLabel, context, seeds });
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return; // clipboard unavailable (permissions / insecure context) — leave the button as is
    }
    setCopied(true);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(false), 2000);
  }

  const rows = summary.findings ?? [];
  const explanation = summary.score_explanation ?? null;
  const subtitle = [architectureLabel, context].filter(Boolean).join(" · ");

  return (
    <Modal open={open} onClose={onClose} labelledBy={TITLE_ID}>
      <div className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id={TITLE_ID} className="text-lg font-semibold tracking-tight text-ink">
              Full verdict report
            </h2>
            <p className="mt-1 truncate text-sm text-ink-dim">{subtitle || "This run"}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close report"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line text-ink-dim transition-colors outline-none hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="mt-6 flex flex-col gap-6">
          <section>
            <SectionLabel>How the score is built</SectionLabel>
            {explanation ? (
              <div className="mt-3">
                <ScoreTermsTable explanation={explanation} />
              </div>
            ) : (
              <p className="mt-3 text-sm leading-6 text-ink-dim">
                This run produced no requests, so there is no score to decompose.
              </p>
            )}
          </section>

          <section>
            <SectionLabel>Findings</SectionLabel>
            <div className="mt-3 flex flex-col gap-3">
              {rows.length ? (
                rows.map((row, index) => <FindingCard key={`${row.id}-${index}`} finding={row} />)
              ) : (
                <p className="text-sm leading-6 text-ink-dim">
                  This run produced no requests, so there is nothing to find.
                </p>
              )}
            </div>
          </section>

          <section>
            <SectionLabel>Run context</SectionLabel>
            <dl className="mt-3 grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2">
              {contextPairs(summary, architectureLabel, context, seeds).map(({ label, value }) => (
                <div key={label} className="bg-surface-1 px-3.5 py-3">
                  <dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-ink-dim">
                    {label}
                  </dt>
                  <dd className="mt-1 text-sm font-medium text-ink">{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
          <p className="max-w-sm text-xs leading-5 text-ink-dim">
            Deterministic report — the same input always produces these same words.
          </p>
          <button
            type="button"
            onClick={copyVerdict}
            className={`h-8 rounded-lg px-4 text-[13px] font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              copied ? "bg-sev-ok-soft text-sev-ok" : "bg-accent text-white hover:bg-accent-2"
            }`}
          >
            {copied ? "Copied" : "Copy verdict"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
