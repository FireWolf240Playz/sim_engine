"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Panel } from "@/components/Panel";
import { IncidentPicker } from "@/components/IncidentPicker";
import { ConfidencePanel } from "@/core/components/ConfidencePanel";
import { FixDiffPanel } from "@/core/components/FixDiffPanel";
import { FixHistoryList, FixOutcomePreview } from "@/core/components/FixLog";
import { SizingTable } from "@/core/components/SizingTable";
import { StatCards } from "@/core/components/StatCards";
import { VerdictPanel } from "@/core/components/VerdictPanel";
import { TimelineChart } from "@/core/components/TimelineChart";
import { TopologyDiagram } from "@/core/components/TopologyDiagram";
import { NodeInspector } from "@/core/components/NodeInspector";
import { api } from "@/core/api/client";
import { DEMO_META, playbookTargets } from "@/core/lib/demo";
import { chaosWindows } from "@/core/lib/chaos";
import { pendingSuggestions } from "@/core/lib/suggestions";
import { fixPanelView, staleNodes, type FixPanelNote } from "@/core/lib/rerun";
import { inspectNode } from "@/core/lib/nodeInspector";
import { CONFIDENCE_SEEDS, useRunState, type RunMode } from "@/core/state/RunStateContext";
import type { TopologyEdge } from "@/core/types";

const RUN_MODES: Array<{ mode: RunMode; label: string }> = [
  { mode: "single", label: "Single run" },
  { mode: "confidence", label: `${CONFIDENCE_SEEDS}-seed confidence` },
];

/** Stable identity for "no edges", so memo props never churn on `?? []`. */
const NO_EDGES: TopologyEdge[] = [];

/** 1.3g — the Fix-it panel's note, per fixPanelView's verdict. */
const FIX_NOTES: Record<Exclude<FixPanelNote, null>, string> = {
  rerunning: "Re-running with your change…",
  applied: "Applied, not yet measured: run again to see the result.",
  nothing: "Nothing to change: every node is the right size for this load.",
};

function EmptyState() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface-1/60 px-6 py-14 text-center">
      <p className="mx-auto max-w-lg text-[15px] leading-7 text-ink">
        No run yet. Pick an incident if you&apos;re feeling brave, or run it
        clean first — then I&apos;ll show you exactly where this architecture
        breaks, and what keeping it alive would cost.
      </p>
      <p className="mx-auto mt-3 text-xs text-ink-dim">{DEMO_META}</p>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-sev-crit/40 bg-sev-crit-soft px-5 py-4"
    >
      <p className="text-sm leading-6 text-sev-crit">{message}</p>
      <p className="mt-2 text-xs text-ink-dim">
        start the engine from the repo root: uvicorn api.main:app --port 8000
      </p>
    </div>
  );
}

export default function HomePage() {
  const {
    config,
    architectureLabel,
    isDemoArchitecture,
    resetArchitecture,
    playbook,
    setPlaybook,
    runMode,
    setRunMode,
    requestRun,
    applyAndRerun,
    fixHistory,
    result,
    measuredNodes,
    error,
    isPending,
  } = useRunState();
  const { data: playbooks } = useQuery({ queryKey: ["playbooks"], queryFn: api.playbooks });

  const sizing = result?.summary.component_sizing;
  const targets = useMemo(
    () => playbookTargets(playbook, config.topology.nodes),
    [playbook, config.topology.nodes],
  );
  const { names: targetNames, wholePath } = targets;
  const context = playbook ? `under ${playbook.replace(/_/g, " ")}` : "on the clean run";
  // Suggestions not yet applied to the active config. The verified outcome
  // describes the whole set, so it shows only while that set is still
  // pending, or when there is nothing to apply but findings capacity can't fix.
  const suggestions = result?.summary.suggestions ?? [];
  const pending = pendingSuggestions(config, suggestions);

  // 1.3g — which nodes' on-screen numbers belong to the OLD size, and what
  // the Fix-it panel is allowed to say while they are re-measured.
  const staleNames = useMemo(
    () => staleNodes(measuredNodes, config.topology.nodes),
    [measuredNodes, config.topology.nodes],
  );
  const stale = useMemo(
    () => ({ names: staleNames, running: isPending }),
    [staleNames, isPending],
  );
  const fixView = fixPanelView({
    pending: pending.length,
    suggestions: suggestions.length,
    hasOutcome: Boolean(result?.summary.fix_outcome),
    isPending,
  });

  // 1.4 — which node the inspector has open. `setInspected` is stable, so
  // passing it to the memo'd diagram never churns its props.
  const [inspected, setInspected] = useState<string | null>(null);
  const inspection = inspected && result ? inspectNode(inspected, config, result, stale) : null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Stress test
          </h1>
          <p className="mt-1.5 text-sm leading-6 text-ink-dim">
            Pick an incident, run it against the{" "}
            <span className="font-medium text-ink">{architectureLabel}</span>, and see
            exactly where the architecture breaks — and what keeping it alive
            costs — before it&apos;s real infrastructure.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {!isDemoArchitecture ? (
            <span className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent-soft px-2.5 py-1.5">
              <span className="text-[12px] font-medium text-accent">{architectureLabel}</span>
              <button
                type="button"
                onClick={resetArchitecture}
                className="rounded-sm text-[11.5px] text-ink-dim outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
              >
                reset
              </button>
            </span>
          ) : (
            <span className="hidden font-mono text-[11px] text-ink-dim lg:inline">
              seed 42 · 60s run
            </span>
          )}
          <div
            role="group"
            aria-label="Run mode"
            className="flex h-7 items-center rounded-lg border border-line bg-surface-1 p-0.5"
          >
            {RUN_MODES.map(({ mode, label }) => (
              <button
                key={mode}
                type="button"
                onClick={() => setRunMode(mode)}
                disabled={isPending}
                aria-pressed={runMode === mode}
                className={`h-6 rounded-md px-3 text-[13px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60 ${
                  runMode === mode
                    ? "bg-accent text-white shadow-card"
                    : "text-ink-dim hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => requestRun()}
            disabled={isPending}
            className="h-8 rounded-lg bg-accent px-6 text-sm font-semibold text-white shadow-card transition-colors outline-none hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isPending ? "Running…" : "Run"}
          </button>
        </div>
      </div>

      <Panel title="Incident" aside="each one mutates live simulation state">
        <IncidentPicker
          value={playbook}
          onChange={setPlaybook}
          options={playbooks?.playbooks ?? []}
          disabled={isPending}
        />
      </Panel>

      {error ? <ErrorState message={error} /> : null}

      {/* Results stay mounted across runs: React patches only what changed,
          and a run in flight dims them instead of blanking the page. */}
      <div className="eleven-results flex flex-col gap-5" aria-busy={isPending}>
      <StatCards summary={result?.summary ?? null} context={context} />

      <VerdictPanel
        summary={result?.summary ?? null}
        context={context}
        architectureLabel={architectureLabel}
        seeds={result?.seeds ?? null}
        fixHistory={fixHistory}
      />

      {result ? (
        <Panel title="Fix it" aside="sizes verified by simulation on the same seed">
          {fixView.outcome && result.summary.fix_outcome ? (
            <FixOutcomePreview outcome={result.summary.fix_outcome} />
          ) : null}
          {fixView.diff ? (
            <FixDiffPanel
              config={config}
              suggestions={suggestions}
              onApply={applyAndRerun}
              isPending={isPending}
            />
          ) : fixView.note !== null ? (
            // Kept mounted when empty, so applying a fix never makes the
            // panels below jump up the page.
            <p className="text-[13px] text-ink-dim">{FIX_NOTES[fixView.note]}</p>
          ) : null}
        </Panel>
      ) : null}

      {fixHistory.length ? (
        <Panel title="Changes applied" aside="each one re-run and measured">
          <FixHistoryList history={fixHistory} />
        </Panel>
      ) : null}

      {result?.profile ? (
        <Panel
          title="Confidence across seeds"
          aside={
            result.seeds && result.seeds.length > 0
              ? `seeds ${result.seeds.join(" · ")}`
              : undefined
          }
        >
          <ConfidencePanel profile={result.profile} />
        </Panel>
      ) : null}

      {result && sizing ? (
        <>
          {/* eleven-live: stays at full strength while a re-run runs — only
              the re-measured nodes and the other panels dim. */}
          <div className="eleven-live">
            <Panel
              title="Topology"
              aside={playbook ? `incident targets: ${wholePath ? "whole path" : targetNames.join(", ")}` : "clean run"}
            >
              <TopologyDiagram
                nodes={config.topology.nodes}
                edges={config.topology.edges ?? NO_EDGES}
                sizing={sizing}
                targetNames={targetNames}
                wholePath={wholePath}
                stale={stale}
                onOpen={setInspected}
              />
              {inspection ? (
                <NodeInspector
                  inspection={inspection}
                  onClose={() => setInspected(null)}
                  onApply={applyAndRerun}
                  isPending={isPending}
                />
              ) : null}
            </Panel>
          </div>

          <Panel
            title="Timeline"
            aside={
              result.typical_seed != null
                ? `typical run · seed ${result.typical_seed}`
                : "latency vs chaos, one clock"
            }
          >
            <TimelineChart
              ticks={result.timeseries ?? []}
              slaTarget={config.sla_target}
              windows={chaosWindows(result.chaos, config.traffic.duration)}
              horizon={config.traffic.duration}
              band={result.timeseries_band}
              seedCount={result.seeds?.length}
            />
          </Panel>

          <Panel title="Sizing" aside="where the money sits">
            <SizingTable nodes={config.topology.nodes} sizing={sizing} stale={stale} />
          </Panel>
        </>
      ) : (
        !error && <EmptyState />
      )}
      </div>
    </div>
  );
}
