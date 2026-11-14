"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { exportChoices, exportRequest } from "../lib/exports";
import type { ExportFormatName, MeasuredRun, SimulateResponse } from "../types";

interface ExportMenuProps {
  /** The config and incident the result on screen was measured with. */
  run: MeasuredRun;
  result: SimulateResponse;
  /** A run is in flight: the result on screen is about to be replaced. */
  disabled: boolean;
}

/** Hand a downloaded file to the browser's own save flow. */
function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * Export center (roadmap 2.1c): one button per format, each downloading the
 * run on screen. Every file comes from the API, so the web and the CLI give
 * the same bytes. A paid format wears a badge and is otherwise the same
 * button: the API decides who may have it, once it knows the user.
 */
export function ExportMenu({ run, result, disabled }: ExportMenuProps) {
  const formats = useQuery({
    queryKey: ["export-formats"],
    queryFn: api.exportFormats,
    staleTime: Infinity,
  });
  const [preparing, setPreparing] = useState<ExportFormatName | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function download(name: ExportFormatName): Promise<void> {
    setPreparing(name);
    setFailure(null);
    try {
      const file = await api.exportRun(exportRequest(run, result, name));
      save(file.blob, file.filename);
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "The export failed — try again.");
    } finally {
      setPreparing(null);
    }
  }

  const choices = exportChoices(formats.data?.formats ?? []);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        {choices.map((choice) => (
          <button
            key={choice.name}
            type="button"
            onClick={() => void download(choice.name)}
            disabled={disabled || preparing !== null || !choice.available}
            className="inline-flex h-8 items-center gap-2 rounded-lg border border-line bg-surface-1 px-4 text-[13px] font-semibold text-ink transition-colors outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {preparing === choice.name ? "Preparing…" : choice.label}
            {choice.badge !== null ? (
              <span className="rounded border border-line px-1 text-[10.5px] font-medium uppercase tracking-[0.08em] text-ink-dim">
                {choice.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      {formats.isError ? (
        <p role="alert" className="text-[13px] text-sev-crit">
          I can&apos;t load the export formats — is the engine running?
        </p>
      ) : null}
      {failure !== null ? (
        <p role="alert" className="mt-2 text-[13px] text-sev-crit">
          {failure}
        </p>
      ) : null}
      <p className="mt-2 text-[13px] text-ink-dim">
        The file is this exact run, simulated again from its seed.
      </p>
    </div>
  );
}
