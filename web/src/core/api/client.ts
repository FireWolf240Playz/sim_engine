import { exportFilename, filenameFromDisposition } from "../lib/exports";
import type {
  CompareRequest,
  CompareResponse,
  ExportFormatsResponse,
  ExportRequest,
  HealthResponse,
  ImportResponse,
  PlaybookListResponse,
  PresetListResponse,
  RunRequest,
  RunStartedResponse,
  RunStatusResponse,
  SimulateResponse,
  SimulationConfig,
} from "../types";

const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/**
 * Per-call deadlines. Without one, a wedged engine leaves the UI stuck
 * on "Running…" with no way back. The values are generous relative to
 * the work: a single 60 s simulated run finishes in well under a second,
 * while `compare multi-cloud` runs four of them and a multi-seed
 * confidence run up to twenty, so those get their own longer budget.
 */
const TIMEOUT_MS = {
  quick: 10_000, // health, playbooks, presets
  run: 60_000, // one /simulate call (single or multi-seed)
  heavy: 180_000, // /compare, /export — several full simulations server-side
} as const;

/** Typed error carrying the HTTP status (0 = engine unreachable or timed out). */
export class ApiError extends Error {
  readonly status: number;
  /** True when the deadline expired rather than the engine refusing. */
  readonly timedOut: boolean;

  constructor(status: number, message: string, timedOut = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.timedOut = timedOut;
  }
}

/** Send one call to the engine; a host that never answers is an `ApiError` 0. */
async function send(path: string, init: RequestInit | undefined, timeoutMs: number): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    // AbortSignal.timeout() rejects with a TimeoutError DOMException;
    // anything else at this stage means the host never answered.
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    throw new ApiError(
      0,
      timedOut
        ? `the engine at ${API_BASE} did not answer within ${Math.round(timeoutMs / 1000)}s`
        : `cannot reach the engine at ${API_BASE}`,
      timedOut,
    );
  }
}

/** A response body as JSON when it is JSON, else as the text it is. */
function parseBody(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** The `ApiError` for a response the engine refused, phrased from its body. */
function refusal(response: Response, body: unknown): ApiError {
  const hasDetail = body !== null && typeof body === "object" && "detail" in body;
  if (hasDetail) {
    // FastAPI's own error shape: `detail` is either a sentence or the
    // structured validation payload.
    const detail = (body as { detail: unknown }).detail;
    return new ApiError(
      response.status,
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  // Anything else (a proxy's HTML error page, an empty body) carries no
  // usable sentence, so the status code goes into the message — it is
  // the only diagnosable part, and the UI renders the message verbatim.
  const statusText = response.statusText || "request failed";
  return new ApiError(response.status, `${response.status} ${statusText}`);
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = TIMEOUT_MS.quick,
): Promise<T> {
  const response = await send(path, init, timeoutMs);
  const body = parseBody(await response.text());
  if (!response.ok) throw refusal(response, body);
  return body as T;
}

/**
 * A file download. The one response that is not JSON: a workbook is binary,
 * and reading it as text (as `request` does) would corrupt it. A refusal is
 * still JSON and becomes the same `ApiError` every other call throws.
 */
async function download(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<{ blob: Blob; disposition: string | null }> {
  const response = await send(path, init, timeoutMs);
  if (!response.ok) throw refusal(response, parseBody(await response.text()));
  return {
    blob: await response.blob(),
    disposition: response.headers.get("Content-Disposition"),
  };
}

export const api = {
  base: API_BASE,

  health: () => request<HealthResponse>("/health"),

  playbooks: () => request<PlaybookListResponse>("/playbooks"),

  /**
   * One full run: config in, summary + score + timeseries out.
   * `nSeeds > 1` asks for the multi-seed confidence profile instead
   * (roadmap 1.1) — the response then carries seeds/runs/profile.
   */
  simulate: (
    config: SimulationConfig,
    playbook: string | null = null,
    nSeeds = 1,
  ) =>
    request<SimulateResponse>(
      "/simulate",
      {
        method: "POST",
        body: JSON.stringify({
          config,
          playbook,
          include_timeseries: true,
          include_report_png: false,
          ...(nSeeds > 1 ? { n_seeds: nSeeds } : {}),
        }),
      },
      TIMEOUT_MS.run,
    ),

  /**
   * 3.1 — start a run and get its id at once; watch it with `watchRun` on
   * `runEventsUrl(run_id)`. The engine checks the request before it starts
   * anything, so a refusal (unknown playbook, too large) is thrown here, as
   * the same `ApiError` `/simulate` would give.
   */
  startRun: (body: RunRequest) =>
    request<RunStartedResponse>(
      "/runs",
      { method: "POST", body: JSON.stringify(body) },
      TIMEOUT_MS.quick,
    ),

  /** The run's event stream, for an `EventSource`. */
  runEventsUrl: (runId: string) => `${API_BASE}/runs/${encodeURIComponent(runId)}/events`,

  /** The run without a stream: its status, and its result once done. */
  runStatus: (runId: string) =>
    request<RunStatusResponse>(`/runs/${encodeURIComponent(runId)}`),

  /** Multi-cloud / what-if / sweep — baseline-relative diff out. */
  compare: (payload: CompareRequest) =>
    request<CompareResponse>(
      "/compare",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      TIMEOUT_MS.heavy,
    ),

  presets: () => request<PresetListResponse>("/presets"),

  /** The formats a run can be downloaded in, in the engine's order. */
  exportFormats: () => request<ExportFormatsResponse>("/export/formats"),

  /**
   * One run as a file. The engine simulates the request's config again, and
   * the Excel and JSON formats carry the Fix-it plan, so this gets the long
   * deadline. The name is the server's; a built one stands in when the
   * header is missing.
   */
  exportRun: async (body: ExportRequest): Promise<{ blob: Blob; filename: string }> => {
    const { blob, disposition } = await download(
      "/export",
      { method: "POST", body: JSON.stringify(body) },
      TIMEOUT_MS.heavy,
    );
    return {
      blob,
      filename: filenameFromDisposition(disposition) ?? exportFilename(body, body.format),
    };
  },

  /** Upload an architecture (native YAML/JSON or terraform show -json) → runnable config. */
  importArchitecture: (content: string, filename?: string) =>
    request<ImportResponse>(
      "/imports",
      {
        method: "POST",
        body: JSON.stringify({ content, filename: filename ?? null }),
      },
      TIMEOUT_MS.run,
    ),
};
