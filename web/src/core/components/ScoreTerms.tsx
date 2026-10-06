import type { ScoreExplanation } from "../types";
import { fmtPoints, pointsToneClass } from "../lib/verdict";

/** "SLA compliance" -> "SLA" — the strip chip needs a short label. */
function shortLabel(label: string): string {
  return label.split(" ")[0];
}

/**
 * "How the score is built" as a compact chip strip — the at-a-glance
 * form, shown under the verdict headline. Credits read green, penalties
 * red, zero-neutral, each in mono so the numbers line up.
 */
export function ScoreTermsStrip({ explanation }: { explanation: ScoreExplanation }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="How the score is built">
      {explanation.terms.map((term) => (
        <span
          key={term.label}
          title={`${term.label}: ${term.detail}`}
          className="inline-flex items-baseline gap-1.5 rounded-md border border-line bg-surface-2/60 px-2 py-1 font-mono text-[11px]"
        >
          <span className="text-ink-dim">{shortLabel(term.label)}</span>
          <span className={`font-semibold ${pointsToneClass(term.points)}`}>{fmtPoints(term.points)}</span>
        </span>
      ))}
      <span className="inline-flex items-baseline gap-1.5 rounded-md border border-line bg-surface-1 px-2 py-1 font-mono text-[11px]">
        <span className="text-ink-dim">score</span>
        <span className="font-semibold text-ink">{explanation.score ?? "—"}/100</span>
      </span>
    </div>
  );
}

/**
 * The same decomposition as a full table — the modal's "how the score is
 * built" section. Every row carries its plain-English `detail`, so the
 * reader sees *what each number means*, not just its sign.
 */
export function ScoreTermsTable({ explanation }: { explanation: ScoreExplanation }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">How the score is built, point by point</caption>
        <tbody>
          {explanation.terms.map((term) => (
            <tr key={term.label} className="border-b border-line last:border-b-0">
              <th
                scope="row"
                className="w-32 px-3 py-2.5 text-left align-top text-[13px] font-medium text-ink"
              >
                {term.label}
              </th>
              <td className="px-3 py-2.5 align-top text-xs leading-5 text-ink-dim">{term.detail}</td>
              <td
                className={`w-20 px-3 py-2.5 text-right align-top font-mono text-[13px] font-semibold ${pointsToneClass(term.points)}`}
              >
                {fmtPoints(term.points)}
              </td>
            </tr>
          ))}
          <tr className="bg-surface-1/70">
            <th scope="row" className="px-3 py-2.5 text-left align-top text-[13px] font-semibold text-ink">
              Score
            </th>
            <td className="px-3 py-2.5 align-top text-xs leading-5 text-ink-dim">
              {explanation.clamped
                ? "sum of the terms above, clamped to the 0–100 rail"
                : "sum of the terms above"}
            </td>
            <td className="px-3 py-2.5 text-right align-top font-mono text-[13px] font-semibold text-ink">
              {explanation.score ?? "—"}/100
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
