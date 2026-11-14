/**
 * Contract for roadmap 2.1c (`.agents/tasks/2.1c-export-menu.md`): the
 * client's two export calls. A download is the one response that is not
 * JSON, and its failures must still come back as the same typed `ApiError`
 * the rest of the UI phrases for a human.
 * Make these pass. Do not edit them.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/core/api/client";
import type { ExportRequest } from "@/core/types";

const REQUEST: ExportRequest = {
  config: {
    seed: 42,
    duration: 60,
    topology: { nodes: [], edges: [] },
    traffic: { base_rps: 2, duration: 60 },
  },
  playbook: "db_failover",
  format: "csv",
};

function stubFetch(impl: typeof fetch): void {
  vi.stubGlobal("fetch", vi.fn(impl));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("exportFormats", () => {
  it("lists what the engine can export", async () => {
    const body = { formats: [{ name: "csv", label: "CSV", extension: "csv", media_type: "text/csv", tier: "free" }], count: 1 };
    let url = "";
    stubFetch(async (input) => {
      url = String(input);
      return new Response(JSON.stringify(body), { status: 200 });
    });
    await expect(api.exportFormats()).resolves.toEqual(body);
    expect(url.endsWith("/export/formats")).toBe(true);
  });
});

describe("exportRun", () => {
  it("posts the request and returns the file with the server's name", async () => {
    let seenUrl = "";
    let seenInit: RequestInit | undefined;
    stubFetch(async (input, init) => {
      seenUrl = String(input);
      seenInit = init;
      return new Response("request_id,t\r\nreq-0,0.1\r\n", {
        status: 200,
        headers: {
          "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": 'attachment; filename="eleven-db_failover-seed42.csv"',
        },
      });
    });
    const file = await api.exportRun(REQUEST);
    expect(seenUrl.endsWith("/export")).toBe(true);
    expect(seenInit?.method).toBe("POST");
    expect(JSON.parse(String(seenInit?.body))).toEqual(REQUEST);
    expect(seenInit?.signal).toBeInstanceOf(AbortSignal);
    expect(file.filename).toBe("eleven-db_failover-seed42.csv");
    expect(file.blob).toBeInstanceOf(Blob);
    expect(await file.blob.text()).toBe("request_id,t\r\nreq-0,0.1\r\n");
  });

  it("keeps binary content intact", async () => {
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00, 0xff, 0xfe]);
    stubFetch(async () => new Response(bytes, { status: 200 }));
    const file = await api.exportRun({ ...REQUEST, format: "xlsx" });
    expect(new Uint8Array(await file.blob.arrayBuffer())).toEqual(bytes);
  });

  it("falls back to a built name when the header is missing", async () => {
    stubFetch(async () => new Response("{}", { status: 200 }));
    const file = await api.exportRun({ ...REQUEST, format: "json" });
    expect(file.filename).toBe("eleven-db_failover-seed42.json");
  });

  it("turns a refusal into an ApiError with the engine's sentence", async () => {
    stubFetch(
      async () =>
        new Response(JSON.stringify({ detail: "this call would simulate about 600,000 requests" }), {
          status: 422,
        }),
    );
    const err = await api.exportRun(REQUEST).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe("this call would simulate about 600,000 requests");
  });

  it("reports an unreachable engine as status 0", async () => {
    stubFetch(async () => {
      throw new TypeError("fetch failed");
    });
    const err = await api.exportRun(REQUEST).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toContain("cannot reach the engine");
  });
});
