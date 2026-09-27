import type {
  CompareRequest,
  CompareResponse,
  HealthResponse,
  PlaybookListResponse,
  PresetListResponse,
  SimulateResponse,
  SimulationConfig,
} from "../types";

const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/** Typed error carrying the HTTP status (0 = engine unreachable). */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `cannot reach the engine at ${API_BASE}`);
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
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : response.statusText;
    const message =
      typeof detail === "string" ? detail : JSON.stringify(detail ?? body ?? response.statusText);
    throw new ApiError(response.status, message);
  }

  return body as T;
}

export const api = {
  base: API_BASE,

  health: () => request<HealthResponse>("/health"),

  playbooks: () => request<PlaybookListResponse>("/playbooks"),

  /** One full run: config in, summary + score + timeseries out. */
  simulate: (
    config: SimulationConfig,
    playbook: string | null = null,
  ) =>
    request<SimulateResponse>("/simulate", {
      method: "POST",
      body: JSON.stringify({
        config,
        playbook,
        include_timeseries: true,
        include_report_png: false,
      }),
    }),

  /** Multi-cloud / what-if / sweep — baseline-relative diff out. */
  compare: (payload: CompareRequest) =>
    request<CompareResponse>("/compare", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  presets: () => request<PresetListResponse>("/presets"),
};
