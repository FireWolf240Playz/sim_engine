import type { SizingStatus } from "../types";

export type Severity = "ok" | "warn" | "crit";

/**
 * Mirror of `sim_core/score.py::score_color` — the badge must never
 * disagree with the report PNG.
 */
export function scoreSeverity(score: number | null): Severity {
  if (score === null) return "warn";
  if (score >= 75) return "ok";
  if (score >= 50) return "warn";
  return "crit";
}

const SEVERITY_RANK: Record<Severity, number> = { crit: 0, warn: 1, ok: 2 };

/** The more severe of two severities. */
export function worseSeverity(a: Severity, b: Severity): Severity {
  return SEVERITY_RANK[a] <= SEVERITY_RANK[b] ? a : b;
}

/**
 * The verdict's one colour: the worse of the score's band and the worst
 * finding. The header bar, icon, band word and gauge ring all use it, so a
 * high score can never sit green next to a critical finding (the engine
 * caps the band word the same way, `score.py::capped_band`).
 */
export function verdictSeverity(score: number | null, worstFinding: Severity | null): Severity {
  const fromScore = scoreSeverity(score);
  return worstFinding === null ? fromScore : worseSeverity(fromScore, worstFinding);
}

export function gradeSeverity(grade: string | null): Severity {
  if (grade === "A" || grade === "B") return "ok";
  if (grade === "C") return "warn";
  return "crit"; // D, F, or n/a
}

export function sizingSeverity(status: SizingStatus): Severity {
  if (status === "right_sized") return "ok";
  if (status === "oversized") return "warn";
  return "crit";
}

export const SEVERITY_TEXT: Record<Severity, string> = {
  ok: "text-sev-ok",
  warn: "text-sev-warn",
  crit: "text-sev-crit",
};

export const SEVERITY_BORDER: Record<Severity, string> = {
  ok: "border-sev-ok",
  warn: "border-sev-warn",
  crit: "border-sev-crit",
};

export const SEVERITY_BG: Record<Severity, string> = {
  ok: "bg-sev-ok",
  warn: "bg-sev-warn",
  crit: "bg-sev-crit",
};

/**
 * SVG equivalents of the three maps above. Inside an `<svg>` the colour
 * has to land on `fill`/`stroke` rather than `color`/`background`, and
 * these utilities resolve to the very same CSS variables — so an SVG
 * surface follows a theme flip without the component holding a single
 * hex value of its own.
 */
export const SEVERITY_FILL: Record<Severity, string> = {
  ok: "fill-sev-ok",
  warn: "fill-sev-warn",
  crit: "fill-sev-crit",
};

export const SEVERITY_FILL_SOFT: Record<Severity, string> = {
  ok: "fill-sev-ok-soft",
  warn: "fill-sev-warn-soft",
  crit: "fill-sev-crit-soft",
};

export const SEVERITY_STROKE: Record<Severity, string> = {
  ok: "stroke-sev-ok",
  warn: "stroke-sev-warn",
  crit: "stroke-sev-crit",
};

/** Colorblind-safe word per severity — color is never the sole signal. */
export const SEVERITY_WORD: Record<Severity, string> = {
  ok: "STABLE",
  warn: "STRESSED",
  crit: "FAILING",
};

export function fmtSeconds(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "n/a";
  return `${value.toFixed(digits)}s`;
}

export function fmtPct(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined) return "n/a";
  return `${(fraction * 100).toFixed(digits)}%`;
}

/**
 * Compact money: `31536000 -> "$31.5M"`, `1517 -> "$1.5k"`,
 * `0.42 -> "$0.42"`, `0.004 -> "<$0.01"`.
 *
 * The sub-dollar branches matter: a simulation run is seconds long, so
 * per-run costs and their deltas are usually fractions of a dollar.
 * Rounding those to whole dollars made every such figure read "$0".
 */
export function fmtMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return "$0";
  const magnitude = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (magnitude >= 1_000_000) return `${sign}$${(magnitude / 1_000_000).toFixed(1)}M`;
  if (magnitude >= 1_000) return `${sign}$${(magnitude / 1_000).toFixed(1)}k`;
  if (magnitude >= 1) return `${sign}$${magnitude.toFixed(0)}`;
  if (magnitude === 0) return "$0";
  if (magnitude < 0.01) return `${sign}<$0.01`;
  return `${sign}$${magnitude.toFixed(2)}`;
}

export function fmtMoneyFull(value: number | null | undefined): string {
  if (value === null || value === undefined) return "$0";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
