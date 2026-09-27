import type { Severity } from "../lib/format";

/**
 * Small shape mark so severity never relies on color alone
 * (colorblind-safe): ok = check, warn = triangle, crit = cross.
 */
export function SeverityIcon({
  severity,
  className = "h-3 w-3",
}: {
  severity: Severity;
  className?: string;
}) {
  if (severity === "ok") {
    return (
      <svg viewBox="0 0 12 12" className={className} aria-hidden="true" focusable="false">
        <path
          d="M2 6.5 4.8 9.3 10 3.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  if (severity === "warn") {
    return (
      <svg viewBox="0 0 12 12" className={className} aria-hidden="true" focusable="false">
        <path
          d="M6 1.6 11 10.4H1Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        <path d="M6 4.6v2.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        <circle cx="6" cy="9.1" r="0.8" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 12 12" className={className} aria-hidden="true" focusable="false">
      <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
