"use client";

import type { Suggestion } from "../types";
import {
  SEVERITY_TEXT,
  fmtMoney,
  fmtPct,
  fmtSeconds,
  sizingSeverity,
} from "../lib/format";
import type { NodeInspection } from "../lib/nodeInspector";
import { findingKeys } from "../lib/verdictView";
import { FindingCard } from "./FindingCard";
import { Modal } from "./Modal";
import { SeverityIcon } from "./SeverityIcon";

const TITLE_ID = "eleven-node-inspector-title";

/** Human word per sizing status — color is never the sole signal. */
const STATUS_WORD: Record<string, string> = {
  right_sized: "right-sized",
  oversized: "oversized",
  undersized: "undersized",
};

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

/** One fact in the measurement grid: small label, mono value. */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface-1 px-3 py-2">
      <dt className="text-[11px] text-ink-dim">{label}</dt>
      <dd className="mt-0.5 font-mono text-sm text-ink">{value}</dd>
    </div>
  );
}

/**
 * 1.4 — the node inspector. One node, one answer, in an accessible dialog:
 * its config (role, ×N, service time), what the last run measured at that
 * size (the verified label + the utilization facts), its cost, the findings
 * that name it, and — when "Fix it" has a pending change for it — that one
 * change, applied through the same "Apply & re-run" path as the panel.
 *
 * Everything comes from `inspectNode()` — this component only formats.
 * What is not in `summary()` (per-node failures, retries, p95 contribution)
 * is not shown; inventing it here would make the dialog lie.
 */
export function NodeInspector({
  inspection,
  onClose,
  onApply,
  isPending,
}: {
  inspection: NodeInspection;
  onClose: () => void;
  onApply: (suggestions: Suggestion[]) => void;
  isPending: boolean;
}) {
  const { measured, remeasure, findings, fix } = inspection;
  const sev = measured ? sizingSeverity(measured.status) : null;

  return (
    <Modal open onClose={onClose} labelledBy={TITLE_ID} className="max-w-xl">
      <div className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id={TITLE_ID} className="font-mono text-lg font-semibold tracking-tight text-ink">
              {inspection.name}
            </h2>
            <p className="mt-1 text-sm text-ink-dim">
              {inspection.role.replace(/_/g, " ")} · ×{inspection.capacity}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close inspector"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line text-ink-dim transition-colors outline-none hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="mt-6 flex flex-col gap-6">
          <section>
            <SectionLabel>Sizing</SectionLabel>
            {measured && sev ? (
              <>
                <p className={`mt-3 flex items-center gap-1.5 text-sm ${SEVERITY_TEXT[sev]}`}>
                  <SeverityIcon severity={sev} className="h-3 w-3" />
                  {STATUS_WORD[measured.status] ?? measured.status}
                  {measured.recommended !== inspection.capacity
                    ? ` · ×${inspection.capacity} → ×${measured.recommended}`
                    : null}
                </p>
                <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Fact label="Mean utilization" value={fmtPct(measured.meanUtil, 1)} />
                  <Fact label="P95 utilization" value={fmtPct(measured.p95Util, 1)} />
                  <Fact
                    label="Peak utilization"
                    value={measured.peakUtil === null ? "n/a" : fmtPct(measured.peakUtil, 0)}
                  />
                  <Fact label="P95 queue" value={measured.p95Queue.toFixed(1)} />
                  <Fact
                    label="Peak queue"
                    value={measured.peakQueue === null ? "n/a" : measured.peakQueue.toFixed(0)}
                  />
                  <Fact label="Service time" value={fmtSeconds(inspection.serviceTime, 2)} />
                </dl>
              </>
            ) : (
              <p className="mt-3 text-sm leading-6 text-ink-dim">
                {remeasure === "running"
                  ? "Measuring the new size…"
                  : remeasure === "unmeasured"
                    ? "Not measured at this size yet: run again to see it."
                    : "No measurement for this node yet."}
              </p>
            )}
          </section>

          <section>
            <SectionLabel>Cost</SectionLabel>
            <dl className="mt-3 grid grid-cols-2 gap-2">
              <Fact
                label="Per hour, per slot"
                value={inspection.costPerHour === null ? "no rate" : fmtMoney(inspection.costPerHour)}
              />
              <Fact
                label={`Per month, 24/7 at ×${inspection.capacity}`}
                value={inspection.monthlyCost === null ? "no rate" : fmtMoney(inspection.monthlyCost)}
              />
            </dl>
          </section>

          {findings.length ? (
            <section>
              <SectionLabel>Findings</SectionLabel>
              <div className="mt-3 flex flex-col gap-3">
                {findingKeys(findings).map((key, i) => (
                  <FindingCard key={key} finding={findings[i]} />
                ))}
              </div>
            </section>
          ) : null}

          {fix ? (
            <section>
              <SectionLabel>Fix it</SectionLabel>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface-1 px-3.5 py-3">
                <div className="min-w-0">
                  <p className="font-mono text-sm font-semibold text-ink">
                    ×{fix.current} → ×{fix.proposed}
                  </p>
                  <p className="mt-1 text-[13px] leading-5 text-ink-dim">{fix.reason}</p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    onApply([fix]);
                    onClose();
                  }}
                  disabled={isPending}
                  className="h-8 rounded-lg bg-accent px-5 text-sm font-semibold text-white shadow-card transition-colors outline-none hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isPending ? "Running…" : "Apply & re-run"}
                </button>
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}
