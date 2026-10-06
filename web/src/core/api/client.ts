import type {
  CompareRequest,
  CompareResponse,
  HealthResponse,
  ImportResponse,
  PlaybookListResponse,
  PresetListResponse,
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
  heavy: 180_000, // /compare — several full simulations server-side
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

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = TIMEOUT_MS.quick,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
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

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    const hasDetail = body !== null && typeof body === "object" && "detail" in body;
    if (hasDetail) {
      // FastAPI's own error shape: `detail` is either a sentence or the
      // structured validation payload.
      const detail = (body as { detail: unknown }).detail;
      throw new ApiError(
        response.status,
        typeof detail === "string" ? detail : JSON.stringify(detail),
      );
    }
    // Anything else (a proxy's HTML error page, an empty body) carries no
    // usable sentence, so the status code goes into the message — it is
    // the only diagnosable part, and the UI renders the message verbatim.
    const statusText = response.statusText || "request failed";
    throw new ApiError(response.status, `${response.status} ${statusText}`);
  }

  return body as T;
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
