"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import { fmtPct } from "@/core/lib/format";
import type { Preset } from "@/core/types";

const PROVIDERS = [
  { key: "aws", label: "AWS (us-east-1 class)" },
  { key: "azure", label: "Azure (eastus class)" },
  { key: "gcp", label: "GCP (us-central1 class)" },
] as const;

function providerOf(name: string): string {
  return name.split("-")[0] ?? "other";
}

export default function PresetsPage() {
  const { data, isError } = useQuery({ queryKey: ["presets"], queryFn: api.presets });

  const entries: Preset[] = data ? Object.values(data.presets) : [];

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Presets</h1>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-ink-dim">
          Provider-calibrated starting points — small on-demand tiers for
          load balancer, worker, cache, and database. They seed the
          multi-cloud compare and are directional estimates, not benchmarks.
        </p>
      </div>

      {isError ? (
        <div className="rounded-xl border border-sev-crit/40 bg-sev-crit-soft px-5 py-4">
          <p className="text-sm leading-6 text-sev-crit">
            Could not load presets — is the engine running?
          </p>
        </div>
      ) : (
        PROVIDERS.map((provider) => {
          const rows = entries.filter((p) => providerOf(p.name) === provider.key);
          if (!rows.length) return null;
          return (
            <section key={provider.key} className="rounded-xl border border-line bg-surface-1 p-5 shadow-card">
              <h2 className="mb-4 text-[15px] font-semibold text-ink">
                {provider.label}
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-ink-dim">
                      <th className="py-2 pr-4 font-medium">Preset</th>
                      <th className="py-2 pr-4 font-medium">Role</th>
                      <th className="py-2 pr-4 font-medium">Capacity</th>
                      <th className="py-2 pr-4 font-medium">Service time</th>
                      <th className="py-2 pr-4 font-medium">Hit rate</th>
                      <th className="py-2 font-medium">$/hr</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => (
                      <tr key={p.name} className="border-b border-line/60 last:border-b-0">
                        <td className="py-2.5 pr-4 font-mono text-sm text-ink">{p.name}</td>
                        <td className="py-2.5 pr-4 text-sm text-ink-dim">{p.role.replace("_", " ")}</td>
                        <td className="py-2.5 pr-4 font-mono text-sm text-ink">×{p.max_capacity}</td>
                        <td className="py-2.5 pr-4 font-mono text-sm text-ink">{p.service_time.toFixed(2)}s</td>
                        <td className="py-2.5 pr-4 font-mono text-sm text-ink">
                          {p.hit_rate != null ? fmtPct(p.hit_rate, 0) : "—"}
                        </td>
                        <td className="py-2.5 font-mono text-sm text-ink">
                          {p.cost_per_hour != null ? `$${p.cost_per_hour.toFixed(3)}` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })
      )}

      <p className="max-w-3xl text-[13px] leading-6 text-ink-dim">
        These are the numbers behind the multi-cloud compare: each provider&apos;s
        small tier has different concurrency and per-request latency, which is
        exactly the difference <span className="font-mono text-ink">compare multi-cloud</span> measures.
        For exact rates use the CLI price catalogs instead of these estimates.
      </p>
    </div>
  );
}
