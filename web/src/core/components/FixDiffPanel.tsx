"use client";

import { useEffect, useRef, useState } from "react";
import type { SimulationConfig, Suggestion } from "../types";
import { buildFixDiff, fixYamlSnippet, pendingSuggestions, terraformSnippet } from "../lib/suggestions";

interface Props {
  config: SimulationConfig;
  suggestions: Suggestion[];
  /** "Apply & re-run" — the shared run path (roadmap 1.3). */
  onApply: (suggestions: Suggestion[]) => void;
  isPending: boolean;
}

function fmtDelta(value: number | null): { text: string; tone: string } {
  if (value === null) return { text: "n/a", tone: "text-ink-dim" };
  const sign = value > 0 ? "+" : value < 0 ? "−" : "±";
  const text = `${sign}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  // Money saved reads as good; money added reads as a cost to justify.
  const tone = value < 0 ? "text-sev-ok" : value > 0 ? "text-sev-warn" : "text-ink-dim";
  return { text, tone };
}

/**
 * Roadmap 1.3 — the "fix it" moment: one click from "it's broken" to
 * "it's fixed". Renders the run's suggestions as a before → after table
 * with the total estimated monthly delta, an Apply & re-run button (the
 * shared, token-guarded run path), and copyable handoff artifacts — YAML
 * primary, Terraform clearly labeled illustrative. Eleven never mutates
 * production; the snippet is the handoff to the real environment.
 */
export function FixDiffPanel({ config, suggestions, onApply, isPending }: Props) {
  // Rows that still have an effect on the active config: after Apply, the
  // config is patched immediately but the previous run's suggestions linger
  // until the re-run lands (forever if it fails). `pendingSuggestions` drops
  // those no-op rows so the table, the snippets and Apply all agree.
  const pending = pendingSuggestions(config, suggestions);
  const diff = buildFixDiff(config, pending);
  const [copied, setCopied] = useState<"yaml" | "tf" | null>(null);
  const timerRef = useRef<number | null>(null);

  // Clear any pending "Copied" reset timer on unmount.
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  async function copy(kind: "yaml" | "tf") {
    const text =
      kind === "yaml"
        ? fixYamlSnippet(config, pending)
        : terraformSnippet(config, pending);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return; // clipboard unavailable (permissions / insecure context)
    }
    setCopied(kind);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(null), 2000);
  }

  const copyBtn = (kind: "yaml" | "tf", label: string) => (
    <button
      type="button"
      onClick={() => copy(kind)}
      className={`h-8 rounded-lg border px-4 text-[13px] font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60 ${
        copied === kind
          ? "border-sev-ok/40 bg-sev-ok-soft text-sev-ok"
          : "border-line bg-surface-1 text-ink hover:bg-surface-2"
      }`}
    >
      {copied === kind ? "Copied" : label}
    </button>
  );

  const total = fmtDelta(diff.totalMonthlyDelta);

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-ink-dim">
              <th className="py-2 pr-4 font-medium">Node</th>
              <th className="py-2 pr-4 font-medium">Change</th>
              <th className="py-2 pr-4 font-medium">Why</th>
              <th className="py-2 pr-4 font-medium text-right">Est. monthly</th>
              <th className="py-2 font-medium" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {diff.rows.map((row) => {
              const delta = fmtDelta(row.monthlyDelta);
              const suggestion = pending.find(
                (s) => s.node === row.node && s.param === row.param,
              );
              return (
                <tr key={`${row.node}-${row.param}`} className="border-b border-line/60 last:border-b-0">
                  <td className="py-2.5 pr-4 font-mono text-sm text-ink">{row.node}</td>
                  <td className="py-2.5 pr-4 font-mono text-sm">
                    <span className="text-ink-dim">×{row.before}</span>
                    <span className="mx-1.5 text-ink-dim">→</span>
                    <span className="font-semibold text-ink">×{row.after}</span>
                  </td>
                  <td className="max-w-[34ch] py-2.5 pr-4 text-[13px] leading-snug text-ink-dim">
                    {suggestion?.reason ?? row.param}
                  </td>
                  <td className={`py-2.5 text-right font-mono text-sm ${delta.tone}`}>
                    {delta.text}
                  </td>
                  <td className="py-2.5 text-right">
                    {suggestion ? (
                      <button
                        type="button"
                        onClick={() => onApply([suggestion])}
                        disabled={isPending}
                        className="h-8 rounded-lg border border-line bg-surface-1 px-3 text-[13px] font-semibold text-ink transition-colors outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {isPending ? "Running…" : "Apply"}
                      </button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <p className="text-[13px] text-ink-dim">
          Total est. monthly{" "}
          <span className={`font-mono font-semibold ${total.tone}`}>{total.text}</span>
          <span className="ml-2 hidden sm:inline">
            · apply → re-run → converge (one 25% step at a time)
          </span>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {copyBtn("yaml", "Copy YAML")}
          {copyBtn("tf", "Copy Terraform (illustrative)")}
          <button
            type="button"
            onClick={() => onApply(suggestions)}
            disabled={isPending}
            className="h-8 rounded-lg bg-accent px-5 text-sm font-semibold text-white shadow-card transition-colors outline-none hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isPending ? "Running…" : "Apply & re-run"}
          </button>
        </div>
      </div>
    </div>
  );
}
