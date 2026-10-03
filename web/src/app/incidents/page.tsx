"use client";

import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import { playbookTargets } from "@/core/lib/demo";
import { useRunState } from "@/core/state/RunStateContext";
import {
  SEVERITY_TEXT,
  scoreSeverity,
} from "@/core/lib/format";
import type { TopologyNode } from "@/core/types";
import { SeverityIcon } from "@/core/components/SeverityIcon";

/** Human target scope for each incident on the ACTIVE topology. */
function targetScope(playbook: string, nodes: TopologyNode[]): string {
  const { names, wholePath } = playbookTargets(playbook, nodes);
  if (wholePath) return "whole path";
  const roles = nodes
    .filter((n) => names.includes(n.name))
    .map((n) => n.role.replace("_", " "));
  return roles.length ? roles.join(" + ") : "—";
}

function RunButton({
  onClick,
  disabled,
  label = "run this",
}: {
  onClick: () => void;
  disabled: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="h-9 rounded-lg border border-line bg-surface-1 px-4 text-[13px] font-medium text-ink outline-none transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
    >
      {disabled ? "running…" : label}
    </button>
  );
}

export default function IncidentsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const { config, architectureLabel, requestRun, result, playbook, isPending } = useRunState();
  const { data: playbooks } = useQuery({ queryKey: ["playbooks"], queryFn: api.playbooks });

  const runIncident = (name: string | null) => {
    requestRun(name);
    if (pathname !== "/") router.push("/");
  };

  const lastScore = result?.summary.resilience_score ?? null;
  const rows = playbooks?.playbooks ?? [];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Incidents</h1>
          <p className="mt-1.5 text-sm leading-6 text-ink-dim">
            Named, realistic failure shapes — each one mutates the live
            simulation (real capacity drops, real service-time inflation),
            not a metrics log. Run one against the{" "}
            <span className="font-medium text-ink">{architectureLabel}</span> and
            watch the score fall.
          </p>
        </div>
        {lastScore !== null ? (
          <span
            className={`flex items-center gap-2 text-[13px] font-medium ${SEVERITY_TEXT[scoreSeverity(lastScore)]}`}
          >
            <SeverityIcon severity={scoreSeverity(lastScore)} className="h-3.5 w-3.5" />
            last run {playbook ? playbook.replace(/_/g, " ") : "clean"} — {Math.round(lastScore)}/100
          </span>
        ) : null}
      </div>

      <div className="overflow-hidden rounded-xl border border-line bg-surface-1 shadow-card">
        <div className="hidden grid-cols-[minmax(170px,1.1fr)_110px_minmax(250px,2fr)_auto] gap-4 border-b border-line px-5 py-3 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-dim sm:grid">
          <span>Incident</span>
          <span>Targets</span>
          <span>What it does</span>
          <span />
        </div>
        {rows.map((p) => (
          <div
            key={p.name}
            className="grid grid-cols-1 gap-3 border-b border-line/70 px-5 py-4 last:border-b-0 sm:grid-cols-[minmax(170px,1.1fr)_110px_minmax(250px,2fr)_auto] sm:items-center sm:gap-4"
          >
            <span className="text-[14px] font-medium text-ink">
              {p.name.replace(/_/g, " ")}
            </span>
            <span className="text-[13px] text-ink-dim">{targetScope(p.name, config.topology.nodes)}</span>
            <span className="text-[13px] leading-6 text-ink-dim">{p.description}</span>
            <div className="sm:justify-self-end">
              <RunButton onClick={() => runIncident(p.name)} disabled={isPending} />
            </div>
          </div>
        ))}
        <div className="grid grid-cols-1 gap-3 px-5 py-4 sm:grid-cols-[minmax(170px,1.1fr)_110px_minmax(250px,2fr)_auto] sm:items-center sm:gap-4">
          <span className="text-[14px] font-medium text-ink-dim">clean baseline</span>
          <span className="text-[13px] text-ink-dim">—</span>
          <span className="text-[13px] leading-6 text-ink-dim">
            No incident. Your reference line — the score every incident is judged against.
          </span>
          <div className="sm:justify-self-end">
            <RunButton onClick={() => runIncident(null)} disabled={isPending} />
          </div>
        </div>
      </div>

      <p className="max-w-3xl text-[13px] leading-6 text-ink-dim">
        Why the incidents hit different scores: a db failover removes slots, so
        requests queue and the P95 breaches the 10 s SLA; a cache storm keeps
        every node alive but pushes lookups through the database; the
        dependency cascade is the worst — the dependency is down, then its
        retry tail slows the whole path. The same 5-node architecture, four
        different ways to fail.
      </p>
    </div>
  );
}
