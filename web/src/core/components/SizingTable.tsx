import type { ComponentSizing, TopologyNode } from "../types";
import { SEVERITY_TEXT, fmtPct, sizingSeverity } from "../lib/format";
import { SeverityIcon } from "./SeverityIcon";

const STATUS_WORD: Record<string, string> = {
  right_sized: "right-sized",
  oversized: "oversized",
  undersized: "undersized",
};

interface Props {
  nodes: TopologyNode[];
  sizing: Record<string, ComponentSizing>;
}

/** Per-node right-sizing readout — the numbers behind the cost grade. */
export function SizingTable({ nodes, sizing }: Props) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-ink-dim">
            <th className="py-2 pr-4 font-medium">Node</th>
            <th className="py-2 pr-4 font-medium">Sized</th>
            <th className="py-2 pr-4 font-medium">Mean util</th>
            <th className="py-2 pr-4 font-medium">P95 queue</th>
            <th className="py-2 pr-4 font-medium">Rec. capacity</th>
            <th className="py-2 font-medium">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => {
            const info = sizing[node.name];
            const sev = info ? sizingSeverity(info.status) : null;
            return (
              <tr key={node.name} className="border-b border-line/60 last:border-b-0">
                <td className="py-2.5 pr-4 font-mono text-sm text-ink">{node.name}</td>
                <td className="py-2.5 pr-4 font-mono text-sm text-ink-dim">×{node.max_capacity}</td>
                <td className="py-2.5 pr-4 font-mono text-sm text-ink">
                  {info ? fmtPct(info.mean_utilization, 1) : "n/a"}
                </td>
                <td className="py-2.5 pr-4 font-mono text-sm text-ink">
                  {info ? info.p95_queue.toFixed(1) : "n/a"}
                </td>
                <td className="py-2.5 pr-4 font-mono text-sm text-ink">
                  {info ? `×${info.recommended_capacity}` : "n/a"}
                </td>
                <td className="py-2.5">
                  {info && sev ? (
                    <span className={`inline-flex items-center gap-1.5 text-sm ${SEVERITY_TEXT[sev]}`}>
                      <SeverityIcon severity={sev} className="h-3 w-3" />
                      {STATUS_WORD[info.status] ?? info.status}
                    </span>
                  ) : (
                    <span className="text-sm text-ink-dim">no data</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
