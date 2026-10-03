"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Panel } from "@/components/Panel";
import { api, ApiError } from "@/core/api/client";
import { useRunState } from "@/core/state/RunStateContext";
import type { ImportResponse, ImportNodeSummary } from "@/core/types";

const ROLE_LABEL: Record<string, string> = {
  load_balancer: "load balancer",
  worker: "worker",
  cache: "cache",
  database: "database",
  external_api: "external api",
  generic: "service",
};

function RoleBadge({ role }: { role: string }) {
  return (
    <span className="inline-flex items-center rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-[10.5px] font-medium uppercase tracking-[0.06em] text-ink-dim">
      {ROLE_LABEL[role] ?? role}
    </span>
  );
}

function NodeRow({ node }: { node: ImportNodeSummary }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line/70 px-4 py-2.5 last:border-b-0">
      <span className="min-w-28 font-mono text-[13px] text-ink">{node.name}</span>
      <RoleBadge role={node.role} />
      <span className="text-[12.5px] text-ink-dim">
        cap {node.max_capacity} · {node.service_time}s/hop
      </span>
    </div>
  );
}

export default function ImportPage() {
  const router = useRouter();
  const { architectureLabel, isDemoArchitecture, setArchitecture, resetArchitecture } =
    useRunState();

  const [text, setText] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [preview, setPreview] = useState<ImportResponse | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const importMutation = useMutation({
    mutationFn: () => api.importArchitecture(text, filename ?? undefined),
    onSuccess: (data) => {
      setPreview(data);
      setFailure(null);
    },
    onError: (err) => {
      setPreview(null);
      setFailure(err instanceof ApiError ? err.message : "the engine could not read that file");
    },
  });

  const readFile = useCallback((file: File) => {
    setFilename(file.name);
    setPreview(null);
    setFailure(null);
    file
      .text()
      .then((content) => setText(content))
      .catch(() => setFailure(`could not read ${file.name}`));
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragActive(false);
      const file = event.dataTransfer.files?.[0];
      if (file) readFile(file);
    },
    [readFile],
  );

  const useArchitecture = () => {
    if (!preview) return;
    const label =
      `${preview.report.format.replace(/_/g, " ")} · ${preview.report.node_count} nodes` +
      (preview.report.source ? ` · ${preview.report.source}` : "");
    setArchitecture(preview.config, label);
    router.push("/incidents");
  };

  const canImport = text.trim().length > 0 && !importMutation.isPending;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Import</h1>
          <p className="mt-1.5 text-sm leading-6 text-ink-dim">
            Bring a real architecture: Eleven YAML/JSON or the output of
            <span className="mx-1 font-mono text-[12.5px]">terraform show -json</span>
            (AWS, GCP, Azure, k8s). I map it to a topology, label every
            estimate, and it becomes the thing every incident runs against.
          </p>
        </div>
        {!isDemoArchitecture ? (
          <div className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent-soft px-3 py-2">
            <span className="text-[12.5px] font-medium text-accent">{architectureLabel}</span>
            <button
              type="button"
              onClick={resetArchitecture}
              className="text-[12px] font-medium text-ink-dim outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
            >
              reset to demo
            </button>
          </div>
        ) : null}
      </div>

      <Panel
        title="Architecture file"
        aside="yaml · json · terraform show -json — auto-detected"
      >
        <div className="grid gap-4 lg:grid-cols-[minmax(220px,1fr)_2fr]">
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileInput.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") fileInput.current?.click();
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={onDrop}
            className={`flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-6 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent ${
              dragActive
                ? "border-accent bg-accent-soft"
                : "border-line bg-surface-1/60 hover:border-accent/60"
            }`}
          >
            <svg viewBox="0 0 20 20" className="h-6 w-6 text-ink-dim" aria-hidden="true">
              <path
                d="M10 13V4.5M6.8 7.7 10 4.5l3.2 3.2M4 12.5v2.5a1.5 1.5 0 0 0 1.5 1.5h9a1.5 1.5 0 0 0 1.5-1.5v-2.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <p className="text-[13px] font-medium text-ink">
              Drop a file here, or click to browse
            </p>
            <p className="text-[11.5px] text-ink-dim">.yaml · .yml · .json</p>
            <input
              ref={fileInput}
              type="file"
              accept=".yaml,.yml,.json,application/json,text/yaml,text/plain"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) readFile(file);
                e.target.value = "";
              }}
            />
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor="import-paste" className="text-[12.5px] font-medium text-ink-dim">
              …or paste the content
            </label>
            <textarea
              id="import-paste"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setPreview(null);
                setFailure(null);
              }}
              spellCheck={false}
              placeholder={'seed: 42\ntopology:\n  nodes: [...]\n# or the JSON from `terraform show -json`'}
              className="h-32 w-full resize-y rounded-lg border border-line bg-surface-1 p-3 font-mono text-[12px] leading-5 text-ink outline-none placeholder:text-ink-dim/60 focus-visible:ring-2 focus-visible:ring-accent"
            />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!canImport}
            onClick={() => importMutation.mutate()}
            className="h-10 rounded-lg bg-accent px-6 text-sm font-semibold text-white shadow-card transition-colors outline-none hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {importMutation.isPending ? "Reading…" : "Import"}
          </button>
          {filename ? (
            <span className="font-mono text-[12px] text-ink-dim">{filename}</span>
          ) : null}
        </div>

        {failure ? (
          <div className="mt-4 rounded-lg border border-sev-crit/40 bg-sev-crit-soft px-4 py-3">
            <p className="text-[13px] leading-6 text-sev-crit">{failure}</p>
          </div>
        ) : null}
      </Panel>

      {preview ? (
        <Panel
          title={`Understood: ${preview.report.node_count} nodes · ${preview.report.edge_count} inferred links`}
          aside={`${preview.format.replace(/_/g, " ")}${preview.report.source ? ` · ${preview.report.source}` : ""}`}
        >
          <div className="overflow-hidden rounded-lg border border-line">
            {preview.report.nodes.map((node) => (
              <NodeRow key={node.name} node={node} />
            ))}
          </div>

          {preview.report.edges.length > 0 ? (
            <p className="mt-3 font-mono text-[11.5px] leading-5 text-ink-dim">
              {preview.report.edges
                .map((e) => `${e.source} → ${e.target}${(e.probability ?? 1) < 1 ? ` (${e.probability})` : ""}`)
                .join("   ·   ")}
            </p>
          ) : null}

          {preview.report.assumptions.length > 0 ? (
            <div className="mt-4">
              <p className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-dim">
                Assumptions — every one of these is an estimate, not a measurement
              </p>
              <ul className="flex flex-col gap-1">
                {preview.report.assumptions.map((line) => (
                  <li key={line} className="text-[12.5px] leading-5 text-ink-dim">
                    · {line}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {preview.report.warnings.length > 0 ? (
            <div className="mt-3 flex flex-col gap-1">
              {preview.report.warnings.map((line) => (
                <p key={line} className="text-[12.5px] leading-5 text-ink-dim">
                  ! {line}
                </p>
              ))}
            </div>
          ) : null}

          {preview.report.unmapped_resources.length > 0 ? (
            <p className="mt-3 text-[12.5px] leading-5 text-ink-dim">
              skipped: {preview.report.unmapped_resources.join(", ")}
            </p>
          ) : null}

          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
            <button
              type="button"
              onClick={useArchitecture}
              className="h-10 rounded-lg bg-accent px-6 text-sm font-semibold text-white shadow-card transition-colors outline-none hover:bg-accent-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0"
            >
              Use this architecture
            </button>
            <span className="text-[12.5px] text-ink-dim">
              every incident on the next page will target it — clean runs too
            </span>
          </div>
        </Panel>
      ) : null}

      {isDemoArchitecture ? (
        <p className="max-w-3xl text-[13px] leading-6 text-ink-dim">
          No import yet — the lab is running the{" "}
          <span className="font-medium text-ink">{architectureLabel}</span>. Upload your
          own architecture and it takes over: the simulator, the incidents, and the
          cost numbers all switch to it.
        </p>
      ) : null}
    </div>
  );
}
