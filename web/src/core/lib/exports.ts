import type {
  ExportFormatInfo,
  ExportFormatName,
  ExportRequest,
  MeasuredRun,
  SimulateResponse,
} from "../types";

/**
 * Export center (roadmap 2.1c) — what the web asks `POST /export` for, and
 * what it calls the file. Pure: no React, no DOM, no fetch.
 *
 * The API keeps no run, so an export simulates the config it is given again.
 * The request therefore has to describe the run *on screen*, which is the
 * measured run, not the controls: the incident picker and "Apply & re-run"
 * both move the controls before anything new is measured.
 */

/**
 * The request for one format of the run on screen.
 *
 * In the 5-seed mode the headline is the typical seed's run, not the run of
 * the config's own seed, so that seed replaces it. The measured config is
 * copied, never changed.
 */
export function exportRequest(
  run: MeasuredRun,
  result: Pick<SimulateResponse, "typical_seed">,
  format: ExportFormatName,
): ExportRequest {
  return {
    config: { ...run.config, seed: result.typical_seed ?? run.config.seed },
    playbook: run.playbook,
    format,
    // 3.3: the failures the run on screen carried; omitted when there were none.
    ...(run.customChaos?.length ? { custom_chaos: run.customChaos } : {}),
  };
}

/** The name the API gives the file, for when its header cannot be read. */
export function exportFilename(request: ExportRequest, extension: string): string {
  return `eleven-${request.playbook ?? "run"}-seed${request.config.seed}.${extension}`;
}

/**
 * The file name in a `Content-Disposition` header, quoted or not, or `null`
 * when there is none. Only the part after the last slash is kept: the name
 * goes into a download, and a header is not trusted to choose a directory.
 */
export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const match = /filename\s*=\s*(?:"([^"]*)"|([^;]*))/i.exec(header);
  if (!match) return null;
  const raw = (match[1] ?? match[2] ?? "").trim();
  const name = raw.split(/[\\/]/).pop() ?? "";
  return name === "" ? null : name;
}

/** One button of the export menu. */
export interface ExportChoice {
  name: ExportFormatName;
  label: string;
  extension: string;
  /** "Pro" on a paid format, else `null`. */
  badge: string | null;
  /** Always true today: the API gates a format once it knows the user (roadmap 2.2). */
  available: boolean;
}

/** The menu's buttons, in the API's order. */
export function exportChoices(formats: ExportFormatInfo[]): ExportChoice[] {
  return formats.map((format) => ({
    name: format.name,
    label: format.label,
    extension: format.extension,
    badge: format.tier === "paid" ? "Pro" : null,
    available: true,
  }));
}
