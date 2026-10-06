import type { Finding, FindingSeverity } from "../types";
import { SEVERITY_TEXT, SEVERITY_WORD, type Severity } from "../lib/format";
import { SeverityIcon } from "./SeverityIcon";

/**
 * One finding as a structured card — the core unit of the rich verdict
 * (roadmap 1.2 rework). Instead of a text stack, every card reads top to
 * bottom as: *what* (title) → *why it matters* → *what it costs* →
 * *the numbers* (evidence chips) → *what to do* (the fix, highlighted).
 *
 * Used both in the Verdict panel and in the full-report modal, so the
 * card design has exactly one home.
 */

/** BE tokens are `info|warn|crit`; the FE system is `ok|warn|crit`. */
const TONE: Record<FindingSeverity, Severity> = {
  info: "ok",
  warn: "warn",
  crit: "crit",
};

export function findingTone(severity: FindingSeverity): Severity {
  return TONE[severity];
}

export function chipCls(tone: Severity): string {
  if (tone === "ok") return "bg-sev-ok-soft text-sev-ok";
  if (tone === "warn") return "bg-sev-warn-soft text-sev-warn";
  return "bg-sev-crit-soft text-sev-crit";
}

/** The node name (when present) rendered as a distinct hoverable token —
 *  today a visual anchor, tomorrow (step 1.4) the node-inspector link. */
function NodeToken({ text, node }: { text: string; node?: string | null }) {
  if (!node) return <>{text}</>;
  const idx = text.indexOf(node);
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <span
        title="Node inspector — step 1.4"
        className="cursor-pointer rounded-sm bg-accent-soft px-1 font-semibold text-ink decoration-accent decoration-[1.5px] underline underline-offset-[3px]"
      >
        {node}
      </span>
      {text.slice(idx + node.length)}
    </>
  );
}

function LabeledRow({ label, content }: { label: string; content: string }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[64px_1fr] sm:gap-x-3">
      <span
        aria-hidden="true"
        className="text-[10px] font-semibold uppercase leading-[22px] tracking-[0.1em] text-ink-dim"
      >
        {label}
      </span>
      <span className="text-sm leading-[22px] text-ink">{content}</span>
    </div>
  );
}

export function FindingCard({ finding }: { finding: Finding }) {
  const tone = TONE[finding.severity];
  const heading = finding.title ?? finding.text;
  const evidence = finding.evidence ?? [];

  return (
    <article className="rounded-lg border border-line bg-surface-0/60 p-4 transition-colors hover:border-edge">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${chipCls(tone)}`}
          >
            <SeverityIcon severity={tone} className="h-3.5 w-3.5" />
          </span>
          <h3 className="min-w-0 text-sm font-semibold leading-5 text-ink">
            <NodeToken text={heading} node={finding.node} />
          </h3>
        </div>
        <span
          className={`shrink-0 pt-0.5 text-[11px] font-semibold tracking-[0.08em] ${SEVERITY_TEXT[tone]}`}
        >
          {SEVERITY_WORD[tone]}
        </span>
      </div>

      {finding.why || finding.impact ? (
        <div className="mt-3 flex flex-col gap-2">
          {finding.why ? <LabeledRow label="Why" content={finding.why} /> : null}
          {finding.impact ? <LabeledRow label="Impact" content={finding.impact} /> : null}
        </div>
      ) : null}

      {evidence.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Evidence">
          {evidence.map((pair) => (
            <span
              key={pair.label}
              className="inline-flex items-baseline gap-1.5 rounded-md border border-line bg-surface-1 px-2 py-1 font-mono text-[11px]"
            >
              <span className="text-ink-dim">{pair.label}</span>
              <span className="font-semibold text-ink">{pair.value}</span>
            </span>
          ))}
        </div>
      ) : null}

      {finding.recommendation ? (
        <div className="mt-3 rounded-md border border-accent/25 bg-accent-soft px-3 py-2.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-accent">
            Fix
          </p>
          <p className="mt-1 text-sm leading-6 text-ink">{finding.recommendation}</p>
        </div>
      ) : null}
    </article>
  );
}
