import type { ReactNode } from "react";
import type { Finding, Summary } from "../types";
import {
  SEVERITY_TEXT,
  SEVERITY_WORD,
  fmtMoney,
  fmtMoneyFull,
  fmtPct,
  fmtSeconds,
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

function ClockIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M8 4.8V8l2.2 1.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DroppedIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="m5.9 5.9 4.2 4.2M10.1 5.9l-4.2 4.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

type Tone = "neutral" | Severity;

function IconChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  const cls =
    tone === "neutral"
      ? "bg-surface-2 text-ink-dim"
      : tone === "ok"
        ? "bg-sev-ok-soft text-sev-ok"
        : tone === "warn"
          ? "bg-sev-warn-soft text-sev-warn"
          : "bg-sev-crit-soft text-sev-crit";
  return (
    <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${cls}`}>
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
  provisional = false,
}: {
  label: string;
  chip: ReactNode;
  value: string;
  word?: string;
  wordClass?: string;
  caption: string;
  /** 3.1c: the number will move once the Fix-it plan is verified. */
  provisional?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border border-line bg-surface-1 px-4 py-3.5 shadow-card transition-opacity ${
        provisional ? "opacity-50" : ""
      }`}
      title={provisional ? "verifying…" : undefined}
      aria-busy={provisional || undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12.5px] font-medium text-ink-dim">{label}</span>
        {chip}
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-2xl font-semibold leading-none tracking-tight text-ink tabular-nums">
          {value}
        </span>
        {word ? (
          <span className={`text-[11px] font-semibold tracking-[0.08em] ${wordClass}`}>
            {word}
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-xs leading-[18px] text-ink-dim">{caption}</p>
    </div>
  );
}

/** The tone of the finding with this id, or `null` when the run has none. */
function findingTone(findings: Finding[] | null | undefined, id: string): Severity | null {
  const hit = findings?.find((finding) => finding.id === id);
  if (!hit) return null;
  return hit.severity === "crit" ? "crit" : hit.severity === "warn" ? "warn" : "ok";
}

const GRID = "grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6";

/**
 * The run headline as a strip of six tiles: the CLI one-liner (resilience,
 * cost grade, projected bill) plus the three numbers behind it (SLA
 * compliance, P95 latency, failed requests). Big number, small icon chip,
 * one short caption each.
 *
 * A tile's colour is never decided here: the SLA and P95 tiles take the tone
 * of the engine's own `sla_breach` / `p95_headroom` findings, so a tile can
 * never disagree with the verdict under it. Failed requests carry no tone.
 * Renders meaningful pre-run placeholders instead of hiding.
 *
 * 3.1c: while the Fix-it plan is verified (`provisional`) the cost grade is
 * graded on unverified sizing labels and will be taken again, so its tile is
 * dimmed. The other five are final the moment the run is measured.
 */
export function StatCards({
  summary,
  context,
  provisional = false,
}: {
  summary: Summary | null;
  /** e.g. "under db failover" or "on the clean run" — the incident in context. */
  context?: string;
  /** 3.1c: the run is measured and its Fix-it plan is still being verified. */
  provisional?: boolean;
}) {
  if (!summary) {
    const blank: Array<{ label: string; icon: ReactNode; caption: string }> = [
      { label: "Resilience", icon: <ShieldIcon />, caption: "Run to score this architecture" },
      { label: "SLA compliance", icon: <ShieldIcon />, caption: "Run to see how many requests made it in time" },
      { label: "P95 latency", icon: <ClockIcon />, caption: "Run to measure the slow tail" },
      { label: "Failed requests", icon: <DroppedIcon />, caption: "Run to count what never completed" },
      { label: "Cost grade", icon: <GaugeIcon />, caption: "Run to grade the right-sizing" },
      { label: "Projected bill", icon: <DollarIcon />, caption: "Run to project the 24/7 bill" },
    ];
    return (
      <div className={GRID}>
        {blank.map((tile) => (
          <StatCard
            key={tile.label}
            label={tile.label}
            chip={<IconChip tone="neutral">{tile.icon}</IconChip>}
            value="—"
            caption={tile.caption}
          />
        ))}
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

  const hasSla = summary.sla_target !== null && summary.sla_target !== undefined;
  const slaTone: Tone =
    summary.sla_compliance === null ? "neutral" : (findingTone(summary.findings, "sla_breach") ?? "ok");
  const p95Tone: Tone =
    summary.p95_latency === null || !hasSla
      ? "neutral"
      : (findingTone(summary.findings, "p95_headroom") ?? "ok");
  const unfinished = summary.unfinished_requests ?? 0;

  return (
    <div className={GRID}>
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
        caption={context ? `Scored ${context}` : "Scored on SLA compliance and completion"}
      />
      <StatCard
        label="SLA compliance"
        chip={
          <IconChip tone={slaTone}>
            {slaTone === "neutral" ? <ShieldIcon /> : <SeverityIcon severity={slaTone} />}
          </IconChip>
        }
        value={fmtPct(summary.sla_compliance, 1)}
        caption={
          hasSla
            ? `Requests answered within ${summary.sla_target}s`
            : "No SLA target set: every completed request counts"
        }
      />
      <StatCard
        label="P95 latency"
        chip={
          <IconChip tone={p95Tone}>
            {p95Tone === "neutral" ? <ClockIcon /> : <SeverityIcon severity={p95Tone} />}
          </IconChip>
        }
        value={fmtSeconds(summary.p95_latency)}
        caption={`Median ${fmtSeconds(summary.p50_latency)}, p99 ${fmtSeconds(summary.p99_latency)}`}
      />
      <StatCard
        label="Failed requests"
        chip={
          <IconChip tone="neutral">
            <DroppedIcon />
          </IconChip>
        }
        value={String(summary.failed_requests)}
        caption={
          unfinished > 0
            ? `Of ${summary.requests} recorded; ${unfinished} still in flight at the end`
            : `Of ${summary.requests} recorded`
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
          provisional
            ? "Verifying the right-sizing…"
            : nodes > 0
              ? `Right-sizing across ${nodes} nodes`
              : "Right-sizing of the nodes"
        }
        provisional={provisional}
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
              ? `Steady state ${fmtMoneyFull(steady.month)}/mo, ${fmtMoney(extrap.year)}/yr`
              : `${fmtMoney(extrap.year)}/yr at this load, 24/7`
            : "No rates configured on the nodes"
        }
      />
    </div>
  );
}
