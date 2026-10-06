import type { Summary } from "../types";

/**
 * Plain-text verdict builder — the "Copy verdict" payload and the single
 * place that knows how to flatten a rich summary into screenshot-able
 * text. Mirrors the CLI's Verdict block so what you copy is what the
 * terminal prints.
 */
export function buildVerdictText(
  summary: Summary,
  opts?: { architectureLabel?: string; context?: string; seeds?: number[] | null },
): string {
  const lines: string[] = [];

  const where = [opts?.architectureLabel, opts?.context]
    .filter((part): part is string => Boolean(part && part.trim()))
    .join(" · ");
  lines.push(where ? `Verdict — ${where}` : "Verdict");

  if (summary.verdict_headline) {
    lines.push(summary.verdict_headline, "");
  }

  const score = summary.resilience_score;
  if (score !== null && score !== undefined) {
    const band = summary.score_explanation?.band;
    lines.push(`Score ${score}/100${band ? ` (${band})` : ""}`);
  }

  const findings = summary.findings ?? [];
  if (findings.length) {
    lines.push("");
    for (const finding of findings) {
      lines.push(`[${finding.severity.toUpperCase()}] ${finding.text}`);
      if (finding.why) lines.push(`    why: ${finding.why}`);
      if (finding.impact) lines.push(`    impact: ${finding.impact}`);
      if (finding.recommendation) lines.push(`    fix: ${finding.recommendation}`);
    }
  }

  if (opts?.seeds && opts.seeds.length > 1) {
    lines.push("", `confidence: ${opts.seeds.length} seeds (${opts.seeds.join(", ")})`);
  }

  return lines.join("\n");
}

/** `1.5 -> "+1.5"`, `-1.5 -> "−1.5"` (true minus sign), `0 -> "±0"`. */
export function fmtPoints(points: number): string {
  const value = Math.round(points * 10) / 10;
  if (value > 0) return `+${trimTrailingZero(value)}`;
  if (value < 0) return `−${trimTrailingZero(Math.abs(value))}`;
  return "±0";
}

function trimTrailingZero(value: number): string {
  const text = value.toFixed(1);
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

/** Severity class for a signed points value: credit / penalty / neutral. */
export function pointsToneClass(points: number): string {
  if (points > 0) return "text-sev-ok";
  if (points < 0) return "text-sev-crit";
  return "text-ink-dim";
}
