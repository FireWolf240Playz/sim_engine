"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { exportChoices, exportRequest } from "../lib/exports";
import type { ExportFormatName, MeasuredRun, SimulateResponse } from "../types";

interface ExportMenuProps {
  /** The config and incident the result on screen was measured with (null before a run). */
  run: MeasuredRun | null;
  result: SimulateResponse | null;
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

function DownloadIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true" focusable="false">
      <path
        d="M8 2.5v7.2M5.4 7.2 8 9.8l2.6-2.6M3 11.5v1A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5v-1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Export center (roadmap 2.1c): the page's Export action. One button in the
 * header opens the list of formats; choosing one downloads the run on screen.
 * Every file comes from the API, so the web and the CLI give the same bytes.
 * A paid format wears a badge and is otherwise the same choice: the API
 * decides who may have it, once it knows the user.
 *
 * The list is a disclosure, not an ARIA menu: its choices are plain buttons
 * in tab order, Escape closes it and returns focus to the trigger.
 */
export function ExportMenu({ run, result, disabled }: ExportMenuProps) {
  const formats = useQuery({
    queryKey: ["export-formats"],
    queryFn: api.exportFormats,
    staleTime: Infinity,
  });
  const [open, setOpen] = useState(false);
  const [preparing, setPreparing] = useState<ExportFormatName | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const ready = run !== null && result !== null;

  async function download(name: ExportFormatName): Promise<void> {
    if (run === null || result === null) return;
    setPreparing(name);
    setFailure(null);
    try {
      const file = await api.exportRun(exportRequest(run, result, name));
      save(file.blob, file.filename);
      setOpen(false);
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "The export failed — try again.");
    } finally {
      setPreparing(null);
    }
  }

  const choices = exportChoices(formats.data?.formats ?? []);

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((was) => !was)}
        disabled={!ready || disabled}
        aria-expanded={open}
        aria-controls={listId}
        title={ready ? undefined : "Run first: an export is the data behind a run"}
        className="inline-flex h-8 items-center gap-2 rounded-lg border border-line bg-surface-1 px-3 text-[13px] font-semibold text-ink transition-colors outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-0 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <DownloadIcon />
        {preparing !== null ? "Preparing…" : "Export"}
        <svg
          viewBox="0 0 16 16"
          className={`h-3 w-3 text-ink-dim transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
          focusable="false"
        >
          <path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open ? (
        <div
          id={listId}
          role="group"
          aria-label="Export formats"
          className="absolute right-0 top-full z-30 mt-1.5 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-surface-1 p-1.5 shadow-pop"
        >
          {choices.map((choice) => (
            <button
              key={choice.name}
              type="button"
              onClick={() => void download(choice.name)}
              disabled={disabled || preparing !== null || !choice.available}
              className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left text-[13px] font-medium text-ink outline-none transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span>{preparing === choice.name ? "Preparing…" : choice.label}</span>
              {choice.badge !== null ? (
                <span className="rounded border border-line px-1 text-[10.5px] font-medium uppercase tracking-[0.08em] text-ink-dim">
                  {choice.badge}
                </span>
              ) : null}
            </button>
          ))}
          {formats.isError ? (
            <p role="alert" className="px-2.5 py-2 text-[13px] text-sev-crit">
              I can&apos;t load the export formats — is the engine running?
            </p>
          ) : null}
          {failure !== null ? (
            <p role="alert" className="px-2.5 py-2 text-[13px] text-sev-crit">
              {failure}
            </p>
          ) : null}
          <p className="mt-1 border-t border-line px-2.5 pb-1 pt-2 text-xs leading-5 text-ink-dim">
            The file is this exact run, simulated again from its seed.
          </p>
        </div>
      ) : null}
    </div>
  );
}
