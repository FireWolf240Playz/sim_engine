import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/core/api/client";

/**
 * The client's job is to turn every failure mode into one typed error
 * the UI can phrase for a human. These tests pin that contract: the
 * status code, the timeout flag, and where the message comes from.
 */

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function stubFetch(impl: typeof fetch): void {
  vi.stubGlobal("fetch", vi.fn(impl));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("request success", () => {
  it("returns the parsed body", async () => {
    stubFetch(async () => jsonResponse({ status: "ok", version: "0.1.0" }));
    await expect(api.health()).resolves.toEqual({ status: "ok", version: "0.1.0" });
  });

  it("sends JSON content-type and bypasses the cache", async () => {
    let seen: RequestInit | undefined;
    stubFetch(async (_url, init) => {
      seen = init;
      return jsonResponse({ playbooks: [], count: 0 });
    });
    await api.playbooks();
    expect(new Headers(seen?.headers).get("content-type")).toBe("application/json");
    expect(seen?.cache).toBe("no-store");
  });

  it("attaches an abort signal to every call", async () => {
    let seen: RequestInit | undefined;
    stubFetch(async (_url, init) => {
      seen = init;
      return jsonResponse({ presets: {}, count: 0 });
    });
    await api.presets();
    expect(seen?.signal).toBeInstanceOf(AbortSignal);
  });
});

describe("request failure", () => {
  it("reports an unreachable engine as status 0", async () => {
    stubFetch(async () => {
      throw new TypeError("fetch failed");
    });
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.timedOut).toBe(false);
    expect(err.message).toContain("cannot reach the engine");
  });

  // Regression: without a deadline a wedged engine left the UI on
  // "Running…" forever, with nothing to distinguish it from a slow run.
  it("flags a timeout distinctly from an unreachable engine", async () => {
    stubFetch(async () => {
      throw new DOMException("The operation timed out.", "TimeoutError");
    });
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.timedOut).toBe(true);
    expect(err.message).toContain("did not answer within");
  });

  it("surfaces FastAPI's `detail` string as the message", async () => {
    stubFetch(async () =>
      jsonResponse({ detail: "unknown playbook: nope" }, { status: 404 }),
    );
    const err = await api.playbooks().catch((e) => e);
    expect(err.status).toBe(404);
    expect(err.message).toBe("unknown playbook: nope");
  });

  it("serialises a structured validation detail rather than printing [object Object]", async () => {
    const detail = [{ loc: ["body", "config", "seed"], msg: "must be >= 0" }];
    stubFetch(async () => jsonResponse({ detail }, { status: 422 }));
    const err = await api.playbooks().catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.message).toContain("must be >= 0");
    expect(err.message).not.toContain("[object Object]");
  });

  it("falls back to the status text when the body is not JSON", async () => {
    stubFetch(
      async () =>
        new Response("<html>502 Bad Gateway</html>", {
          status: 502,
          statusText: "Bad Gateway",
        }),
    );
    const err = await api.playbooks().catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.message).toContain("502 Bad Gateway");
  });

  it("handles an empty body on an error status", async () => {
    stubFetch(async () => new Response("", { status: 500, statusText: "Internal" }));
    const err = await api.playbooks().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
    expect(err.message).toContain("500");
  });

  it("still names the status when the server sends no status text", async () => {
    stubFetch(async () => new Response("", { status: 503 }));
    const err = await api.playbooks().catch((e) => e);
    expect(err.message).toBe("503 request failed");
  });
});

describe("simulate payload", () => {
  const config = {
    seed: 42,
    duration: 60,
    topology: { nodes: [], edges: [] },
    traffic: { base_rps: 2, duration: 60 },
  };

  async function capturePayload(call: () => Promise<unknown>): Promise<Record<string, unknown>> {
    let body = "";
    stubFetch(async (_url, init) => {
      body = String(init?.body ?? "");
      return jsonResponse({ summary: {}, chaos: [], timeseries: [] });
    });
    await call();
    return JSON.parse(body) as Record<string, unknown>;
  }

  it("always asks for the timeseries and never the PNG", async () => {
    const payload = await capturePayload(() => api.simulate(config, "db_failover"));
    expect(payload.include_timeseries).toBe(true);
    expect(payload.include_report_png).toBe(false);
    expect(payload.playbook).toBe("db_failover");
  });

  it("omits n_seeds for a single run, keeping the pre-1.1 byte shape", async () => {
    const payload = await capturePayload(() => api.simulate(config));
    expect("n_seeds" in payload).toBe(false);
    expect(payload.playbook).toBe(null);
  });

  it("sends n_seeds for a confidence run", async () => {
    const payload = await capturePayload(() => api.simulate(config, null, 5));
    expect(payload.n_seeds).toBe(5);
  });
});
