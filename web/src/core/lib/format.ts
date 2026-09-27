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

export const SEVERITY_HEX: Record<Severity, string> = {
  ok: "#2fbf71",
  warn: "#e8a13a",
  crit: "#e5484d",
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

/** Compact money: `31536000 -> "$31.5M"`, `1517 -> "$1.5k"`. */
export function fmtMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return "$0";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}k`;
  return `$${value.toFixed(0)}`;
}

export function fmtMoneyFull(value: number | null | undefined): string {
  if (value === null || value === undefined) return "$0";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
