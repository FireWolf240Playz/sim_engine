import type { ReactNode } from "react";
import type { Summary } from "../types";
import {
  SEVERITY_TEXT,
  SEVERITY_WORD,
  fmtMoney,
  fmtMoneyFull,
  gradeSeverity,
  scoreSeverity,
  type Severity,
} from "../lib/format";
import { SeverityIcon } from "./SeverityIcon";

function ShieldIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" focusable="false">
      <path
        d="M8 1.8 3.2 3.6v4c0 2.9 2 4.8 4.8 5.6 2.8-.8 4.8-2.7 4.8-5.6v-4L8 1.8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="m5.8 8 1.6 1.6 2.8-3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function GaugeIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" focusable="false">
      <path
        d="M3 11.5a5 5 0 1 1 10 0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path d="M8 11.5 10.4 7.9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="11.5" r="1" fill="currentColor" />
    </svg>
  );
}

function DollarIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" focusable="false">
      <path d="M8 2.2v11.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path
        d="M11 4.7c-.7-1-4.6-1.3-4.6 1 0 2.6 5.2 1.5 5.2 4.2 0 2.3-4 2.3-5.3.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconChip({ tone, children }: { tone: "neutral" | Severity; children: ReactNode }) {
  const cls =
    tone === "neutral"
      ? "bg-surface-2 text-ink-dim"
      : tone === "ok"
        ? "bg-sev-ok-soft text-sev-ok"
        : tone === "warn"
          ? "bg-sev-warn-soft text-sev-warn"
          : "bg-sev-crit-soft text-sev-crit";
  return (
    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${cls}`}>
      {children}
    </span>
  );
}

function StatCard({
  label,
  chip,
  value,
  word,
  wordClass,
  caption,
}: {
  label: string;
  chip: ReactNode;
  value: string;
  word?: string;
  wordClass?: string;
  caption: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface-1 p-5 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <span className="text-[13px] font-medium text-ink-dim">{label}</span>
        {chip}
      </div>
      <div className="mt-3 flex items-baseline gap-2.5">
        <span className="text-[28px] font-semibold leading-none tracking-tight text-ink">
          {value}
        </span>
        {word ? (
          <span className={`text-[11px] font-semibold tracking-[0.08em] ${wordClass}`}>
            {word}
          </span>
        ) : null}
      </div>
      <p className="mt-2.5 text-xs leading-5 text-ink-dim">{caption}</p>
    </div>
  );
}

/**
 * The run headline as three stat cards — the CLI one-liner
 * (`Resilience 81/100 · Cost grade C · $1.2k/mo`) rendered the way a
 * dashboard reads: big number, soft icon chip, one-line caption.
 * Renders meaningful pre-run placeholders instead of hiding.
 */
export function StatCards({
  summary,
  context,
}: {
  summary: Summary | null;
  /** e.g. "under db failover" or "clean run" — the incident in context. */
  context?: string;
}) {
  if (!summary) {
    return (
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Resilience"
          chip={
            <IconChip tone="neutral">
              <ShieldIcon />
            </IconChip>
          }
          value="—"
          caption="Run an incident to score this architecture"
        />
        <StatCard
          label="Cost grade"
          chip={
            <IconChip tone="neutral">
              <GaugeIcon />
            </IconChip>
          }
          value="—"
          caption="Run to grade the right-sizing"
        />
        <StatCard
          label="Projected bill"
          chip={
            <IconChip tone="neutral">
              <DollarIcon />
            </IconChip>
          }
          value="—"
          caption="Run to project the 24/7 bill"
        />
      </div>
    );
  }

  const score = summary.resilience_score;
  const scoreSev = scoreSeverity(score);
  const grade = summary.cost_grade;
  const gradeSev = gradeSeverity(grade);
  const extrap = summary.cost_extrapolation;
  const steady = summary.steady_state_cost_extrapolation;
  const nodes = summary.component_sizing ? Object.keys(summary.component_sizing).length : 0;

  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <StatCard
        label="Resilience"
        chip={
          <IconChip tone={scoreSev}>
            <SeverityIcon severity={scoreSev} />
          </IconChip>
        }
        value={score === null ? "n/a" : `${Math.round(score)}/100`}
        word={SEVERITY_WORD[scoreSev]}
        wordClass={SEVERITY_TEXT[scoreSev]}
        caption={
          context
            ? `Scored on SLA compliance and completion ${context}`
            : "Scored on SLA compliance and completion"
        }
      />
      <StatCard
        label="Cost grade"
        chip={
          <IconChip tone={gradeSev}>
            <GaugeIcon />
          </IconChip>
        }
        value={grade ?? "–"}
        caption={
          nodes > 0
            ? `Right-sizing across ${nodes} nodes — steady-state utilization bands`
            : "Right-sizing verdict from mean utilization bands"
        }
      />
      <StatCard
        label="Projected bill"
        chip={
          <IconChip tone="neutral">
            <DollarIcon />
          </IconChip>
        }
        value={extrap && extrap.month > 0 ? `${fmtMoneyFull(extrap.month)}/mo` : "n/a"}
        caption={
          extrap && extrap.month > 0
            ? steady
              ? `Steady state ${fmtMoneyFull(steady.month)}/mo — ${fmtMoney(extrap.year)}/yr, 24/7 assumption`
              : `${fmtMoney(extrap.year)}/yr, assuming the same load sustained 24/7`
            : "No rates configured — add cost_per_hour to nodes"
        }
      />
    </div>
  );
}
