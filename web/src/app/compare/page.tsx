"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Panel } from "@/components/Panel";
import { ApiError, api } from "@/core/api/client";
import { useRunState } from "@/core/state/RunStateContext";
import {
  SEVERITY_TEXT,
  fmtMoney,
  fmtPct,
  fmtSeconds,
  sizingSeverity,
} from "@/core/lib/format";
import type {
  CompareMode,
  CompareRequest,
  CompareResponse,
  CompareRunDiff,
  SizingStatus,
} from "@/core/types";

type MetricKind = "int" | "frac" | "sec" | "usd";

/** Metric rows (mirrors `sim_core.compare.COMPARE_METRICS`). */
interface MetricRow {
  key: string;
  label: string;
  kind: MetricKind;
  /** true = higher is better; false = lower is better; null = no good/bad. */
  goodUp: boolean | null;
}

/**
 * The cost row's label carries the run length, which comes from the
 * active config rather than a literal — the baseline is whatever
 * architecture is loaded, and an imported one need not be 60 s long.
 */
function metricRows(durationSeconds: number): MetricRow[] {
  return [
    { key: "requests", label: "Requests", kind: "int", goodUp: null },
    { key: "completion_rate", label: "Completion", kind: "frac", goodUp: true },
    { key: "sla_compliance", label: "SLA compliance", kind: "frac", goodUp: true },
    { key: "p50_latency", label: "P50 latency", kind: "sec", goodUp: false },
    { key: "p95_latency", label: "P95 latency", kind: "sec", goodUp: false },
    { key: "p99_latency", label: "P99 latency", kind: "sec", goodUp: false },
    { key: "total_retries", label: "Retries", kind: "int", goodUp: false },
    {
      key: "total_cost",
      label: `Total cost (${Math.round(durationSeconds)} s)`,
      kind: "usd",
      goodUp: false,
    },
  ];
}

function fmtValue(kind: MetricKind, value: number | null): string {
  if (value === null) return "n/a";
  switch (kind) {
    case "frac":
      return fmtPct(value, 1);
    case "sec":
      return fmtSeconds(value, 2);
    case "usd":
      return fmtMoney(value);
    default:
      return `${Math.round(value)}`;
  }
}

const MODES: { key: CompareMode; label: string; hint: string }[] = [
  {
    key: "multi-cloud",
    label: "multi-cloud",
    hint: "same architecture, AWS / Azure / GCP small-tier presets",
  },
  {
    key: "what-if",
    label: "what-if",
    hint: "patch the baseline: one path=value per line (e.g. db.max_capacity=2)",
  },
  {
    key: "sweep",
    label: "sweep",
    hint: "sweep one parameter and find the knee where improvement flattens",
  },
];

export default function ComparePage() {
  // The baseline is the ACTIVE architecture, exactly as the Simulator and
  // Incidents views use it. Comparing a hardcoded demo while an imported
  // architecture was loaded meant this page quietly answered a different
  // question than the one on screen.
  const { config, architectureLabel } = useRunState();

  const [mode, setMode] = useState<CompareMode>("multi-cloud");
  const [setEntries, setSetEntries] = useState("db.max_capacity=2\ntraffic.base_rps=3");
  const [param, setParam] = useState("db.max_capacity");
  const [values, setValues] = useState("2,3,4,6,8");
  const [metric, setMetric] = useState("p95_latency");

  const compare = useMutation<CompareResponse, Error, CompareRequest>({
    mutationFn: (payload) => api.compare(payload),
  });

  const run = () => {
    const payload: CompareRequest = { config, mode };
    if (mode === "what-if") {
      payload.set = setEntries
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    }
    if (mode === "sweep") {
      payload.param = param.trim();
      payload.values = values
        .split(",")
        .map((v) => Number(v.trim()))
        .filter((v) => Number.isFinite(v));
      payload.metric = metric;
    }
    compare.mutate(payload);
  };

  const error = compare.error
    ? compare.error instanceof ApiError
      ? `The engine said no — ${compare.error.message}`
      : "The compare run failed — try again."
    : null;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Compare</h1>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-ink-dim">
          Run the{" "}
          <span className="font-medium text-ink">{architectureLabel}</span> against
          other configurations and read the baseline-relative diff. Every cell
          shows the value and its delta — green is better, red is worse.
        </p>
      </div>

      <Panel
        title="Mode"
        aside={`baseline = ${architectureLabel}, seed ${config.seed}, clean run`}
      >
        <div className="flex flex-col gap-4">
          <div
            role="radiogroup"
            aria-label="Compare mode"
            className="inline-flex max-w-full flex-wrap gap-1 rounded-xl bg-surface-2 p-1"
          >
            {MODES.map((m) => (
              <button
                key={m.key}
                type="button"
                role="radio"
                aria-checked={mode === m.key}
                onClick={() => setMode(m.key)}
                disabled={compare.isPending}
                className={`rounded-lg px-3.5 py-1.5 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50 ${
                  mode === m.key
                    ? "bg-surface-1 font-semibold text-ink shadow-card"
                    : "font-medium text-ink-dim hover:text-ink"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-xs leading-5 text-ink-dim">
            {MODES.find((m) => m.key === mode)?.hint}
          </p>

          {mode === "what-if" ? (
            <label className="block">
              <span className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.1em] text-ink-dim">
                patches (one per line)
              </span>
              <textarea
                value={setEntries}
                onChange={(e) => setSetEntries(e.target.value)}
                rows={3}
                spellCheck={false}
                className="w-full max-w-lg rounded-lg border border-line bg-surface-0 px-3 py-2 font-mono text-[13px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
            </label>
          ) : null}

          {mode === "sweep" ? (
            <div className="flex flex-wrap items-end gap-4">
              <label className="block">
                <span className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.1em] text-ink-dim">
                  parameter
                </span>
                <input
                  value={param}
                  onChange={(e) => setParam(e.target.value)}
                  spellCheck={false}
                  className="h-10 w-56 rounded-lg border border-line bg-surface-0 px-3 font-mono text-[13px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.1em] text-ink-dim">
                  values (comma)
                </span>
                <input
                  value={values}
                  onChange={(e) => setValues(e.target.value)}
                  spellCheck={false}
                  className="h-10 w-44 rounded-lg border border-line bg-surface-0 px-3 font-mono text-[13px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.1em] text-ink-dim">
                  knee metric
                </span>
                <select
                  value={metric}
                  onChange={(e) => setMetric(e.target.value)}
                  className="h-10 rounded-lg border border-line bg-surface-0 px-3 font-mono text-[13px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  <option value="p95_latency">p95_latency</option>
                  <option value="sla_compliance">sla_compliance</option>
                  <option value="total_cost">total_cost</option>
                </select>
              </label>
            </div>
          ) : null}

          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={run}
              disabled={compare.isPending}
              className="h-10 rounded-lg bg-accent px-6 text-sm font-semibold text-white shadow-card transition-colors hover:bg-accent-2 outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-1 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {compare.isPending ? "Running…" : "Run compare"}
            </button>
            {mode === "multi-cloud" ? (
              <span className="font-mono text-[11px] text-ink-dim">
                runs 4 simulations (baseline + 3 clouds)
              </span>
            ) : null}
          </div>
        </div>
      </Panel>

      {error ? (
        <div
          role="alert"
          className="rounded-xl border border-sev-crit/40 bg-sev-crit-soft px-5 py-4"
        >
          <p className="text-sm leading-6 text-sev-crit">{error}</p>
        </div>
      ) : null}

      {compare.data ? (
        <DiffTable response={compare.data} metrics={metricRows(config.traffic.duration)} />
      ) : null}
    </div>
  );
}

function Delta({
  run,
  metricKey,
  kind,
  goodUp,
}: {
  run: CompareRunDiff;
  metricKey: string;
  kind: MetricKind;
  goodUp: boolean | null;
}) {
  const delta = run.deltas[metricKey];
  if (delta === null || delta === undefined || delta === 0) {
    return <span className="text-ink-dim">±0</span>;
  }
  const good = goodUp === null ? null : delta > 0 === goodUp;
  const sign = delta > 0 ? "+" : "−";
  return (
    <span className={good === null ? "text-ink" : good ? SEVERITY_TEXT.ok : SEVERITY_TEXT.crit}>
      {sign}
      {fmtValue(kind, Math.abs(delta))}
    </span>
  );
}

function DiffTable({
  response,
  metrics,
}: {
  response: CompareResponse;
  metrics: MetricRow[];
}) {
  const runs = response.diff.runs;
  const baselineLabel = response.diff.baseline;

  return (
    <Panel
      title={`Diff vs ${baselineLabel}`}
      aside={response.knee !== null ? `knee ≈ ${response.knee}` : undefined}
    >
      {response.knee !== null ? (
        <p className="mb-4 rounded-lg border border-accent/30 bg-accent-soft px-4 py-3 text-[13px] leading-6 text-ink">
          Knee ≈ <span className="font-mono text-accent">{response.knee}</span>: beyond
          this value, each added unit of the swept parameter buys less than the
          knee threshold of improvement on the chosen metric — the
          cost-per-benefit trade stops being worth it.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-ink-dim">
              <th className="py-2 pr-4 font-medium">Metric</th>
              {runs.map((run) => (
                <th
                  key={run.label}
                  className="px-4 py-2 font-mono font-medium normal-case tracking-normal text-ink"
                >
                  {run.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.key} className="border-b border-line/60 last:border-b-0">
                <td className="py-2.5 pr-4 text-[13px] text-ink-dim">{m.label}</td>
                {runs.map((run) => {
                  const isBaseline = run.label === baselineLabel;
                  return (
                    <td key={run.label} className="px-4 py-2.5 font-mono text-[13px]">
                      <span className="text-ink">
                        {fmtValue(m.kind, run.values[m.key] ?? null)}
                      </span>
                      {!isBaseline ? (
                        <span className="ml-2 text-[11px]">
                          <Delta
                            run={run}
                            metricKey={m.key}
                            kind={m.kind}
                            goodUp={m.goodUp}
                          />
                        </span>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td className="py-2.5 pr-4 text-[13px] text-ink-dim">Sizing</td>
              {runs.map((run) => (
                <td key={run.label} className="px-4 py-2.5">
                  <span className="flex flex-wrap gap-1.5">
                    {Object.entries(run.sizing).map(([name, status]) => {
                      const sev = sizingSeverity(status as SizingStatus);
                      return (
                        <span
                          key={name}
                          className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] ${
                            sev === "ok"
                              ? "border-sev-ok/50 text-sev-ok"
                              : sev === "warn"
                                ? "border-sev-warn/50 text-sev-warn"
                                : "border-sev-crit/50 text-sev-crit"
                          }`}
                        >
                          {name} {status.replace("_sized", "")}
                        </span>
                      );
                    })}
                  </span>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
