import type { Finding, FindingSeverity } from "../types";
import {
  SEVERITY_BG,
  SEVERITY_TEXT,
  SEVERITY_WORD,
  type Severity,
} from "../lib/format";
import { SeverityIcon } from "./SeverityIcon";

/**
 * The "Verdict" block (roadmap 1.2): the run's plain-English explanation
 * of the score — 3–5 severity-ordered lines, deterministic server-side
 * (see `sim_core/findings.py`). This is the block a non-technical person
 * screenshots.
 *
 * BE tokens are `info|warn|crit`; the FE severity system is `ok|warn|crit`
 * — `info` maps to `ok` (the green "STABLE" lane).
 */
const TONE: Record<FindingSeverity, Severity> = {
  info: "ok",
  warn: "warn",
  crit: "crit",
};

const TONE_RANK: Record<Severity, number> = { crit: 0, warn: 1, ok: 2 };

function chipCls(tone: Severity | "neutral"): string {
  if (tone === "neutral") return "bg-surface-2 text-ink-dim";
  if (tone === "ok") return "bg-sev-ok-soft text-sev-ok";
  if (tone === "warn") return "bg-sev-warn-soft text-sev-warn";
  return "bg-sev-crit-soft text-sev-crit";
}

function worstTone(rows: Finding[]): Severity {
  let worst: Severity = "ok";
  for (const row of rows) {
    const tone = TONE[row.severity];
    if (TONE_RANK[tone] < TONE_RANK[worst]) worst = tone;
  }
  return worst;
}

/**
 * The finding text with its node name (when present) rendered as a
 * distinct, hoverable token — today a visual anchor, tomorrow (step 1.4)
 * the click-through to the node inspector.
 */
function FindingText({ finding }: { finding: Finding }) {
  const node = finding.node;
  if (!node) return <>{finding.text}</>;
  const idx = finding.text.indexOf(node);
  if (idx === -1) return <>{finding.text}</>;
  return (
    <>
      {finding.text.slice(0, idx)}
      <span
        title="Node inspector — step 1.4"
        className="cursor-pointer rounded-sm bg-accent-soft px-1 font-semibold text-ink decoration-accent decoration-[1.5px] underline underline-offset-[3px] transition-colors hover:bg-accent-soft/70"
      >
        {node}
      </span>
      {finding.text.slice(idx + node.length)}
    </>
  );
}

export function VerdictPanel({
  findings,
  context,
}: {
  findings?: Finding[] | null;
  /** e.g. "under db failover" or "clean run" — the incident in context. */
  context?: string;
}) {
  const rows = findings ?? [];
  const tone: Severity | "neutral" = rows.length ? worstTone(rows) : "neutral";

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface-1 shadow-card">
      {/* Overall-tone accent: the whole verdict reads as one color story. */}
      <div className={`h-1 w-full ${tone === "neutral" ? "bg-surface-2" : SEVERITY_BG[tone]}`} />

      <div className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          <div className="flex items-center gap-3">
            <span
              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${chipCls(tone)}`}
            >
              <SeverityIcon severity={tone === "neutral" ? "ok" : tone} className="h-3.5 w-3.5" />
            </span>
            <h2 className="text-[15px] font-semibold text-ink">Verdict</h2>
            {tone !== "neutral" ? (
              <span
                className={`text-[11px] font-semibold tracking-[0.08em] ${SEVERITY_TEXT[tone]}`}
              >
                {SEVERITY_WORD[tone]}
              </span>
            ) : null}
          </div>
          <span className="text-xs text-ink-dim">
            {context ? `why this score · ${context}` : "why this score"}
          </span>
        </div>

        <ul role="list" className="mt-4 flex flex-col gap-1">
          {rows.map((row, index) => {
            const rowTone = TONE[row.severity];
            return (
              <li
                key={`${row.id}-${index}`}
                className="flex items-start gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-surface-2/60"
              >
                <span
                  className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${chipCls(rowTone)}`}
                >
                  <SeverityIcon severity={rowTone} />
                </span>
                <p className="min-w-0 text-sm leading-6 text-ink">
                  <FindingText finding={row} />
                </p>
              </li>
            );
          })}
          {rows.length === 0 ? (
            <li className="flex items-start gap-3 rounded-lg px-2 py-2">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-ink-dim">
                <SeverityIcon severity="ok" />
              </span>
              <p className="text-sm leading-6 text-ink-dim">
                Run an incident to get a plain-English verdict for this architecture
              </p>
            </li>
          ) : null}
        </ul>
      </div>
    </div>
  );
}
