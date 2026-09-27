"use client";

import { useQuery } from "@tanstack/react-query";
import { Panel } from "@/components/Panel";
import { IncidentPicker } from "@/components/IncidentPicker";
import { SizingTable } from "@/core/components/SizingTable";
import { StatCards } from "@/core/components/StatCards";
import { TimelineChart } from "@/core/components/TimelineChart";
import { TopologyDiagram } from "@/core/components/TopologyDiagram";
import { api } from "@/core/api/client";
import { DEMO_CONFIG, DEMO_META, playbookTargets } from "@/core/lib/demo";
import { chaosWindows } from "@/core/lib/chaos";
import { useRunState } from "@/core/state/RunStateContext";

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
    <div className="rounded-xl border border-sev-crit/40 bg-sev-crit-soft px-5 py-4">
      <p className="text-sm leading-6 text-sev-crit">{message}</p>
      <p className="mt-2 text-xs text-ink-dim">
        start the engine from the repo root: uvicorn api.main:app --port 8000
      </p>
    </div>
  );
}

export default function HomePage() {
  const { playbook, setPlaybook, requestRun, result, error, isPending } = useRunState();
  const { data: playbooks } = useQuery({ queryKey: ["playbooks"], queryFn: api.playbooks });

  const sizing = result?.summary.component_sizing;
  const { names: targetNames, wholePath } = playbookTargets(
    playbook,
    DEMO_CONFIG.topology.nodes,
  );
  const context = playbook ? `under ${playbook.replace(/_/g, " ")}` : "on the clean run";

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Stress test
          </h1>
          <p className="mt-1.5 text-sm leading-6 text-ink-dim">
            Pick an incident, run it against the demo topology, and see
            exactly where the architecture breaks — and what keeping it alive
            costs — before it&apos;s real infrastructure.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-[11px] text-ink-dim lg:inline">
            seed 42 · 60s run
          </span>
          <button
            type="button"
            onClick={() => requestRun()}
            disabled={isPending}
            className="h-10 rounded-lg bg-accent px-6 text-sm font-semibold text-white shadow-card transition-colors outline-none hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
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

      <StatCards summary={result?.summary ?? null} context={context} />

      {result && sizing ? (
        <>
          <Panel
            title="Topology"
            aside={playbook ? `incident targets: ${wholePath ? "whole path" : targetNames.join(", ")}` : "clean run"}
          >
            <TopologyDiagram
              nodes={DEMO_CONFIG.topology.nodes}
              edges={DEMO_CONFIG.topology.edges ?? []}
              sizing={sizing}
              targetNames={targetNames}
              wholePath={wholePath}
            />
          </Panel>

          <Panel title="Timeline" aside="latency vs chaos, one clock">
            <TimelineChart
              ticks={result.timeseries ?? []}
              slaTarget={DEMO_CONFIG.sla_target}
              windows={chaosWindows(result.chaos, DEMO_CONFIG.traffic.duration)}
              horizon={DEMO_CONFIG.traffic.duration}
            />
          </Panel>

          <Panel title="Sizing" aside="where the money sits">
            <SizingTable nodes={DEMO_CONFIG.topology.nodes} sizing={sizing} />
          </Panel>
        </>
      ) : (
        !error && <EmptyState />
      )}
    </div>
  );
}
