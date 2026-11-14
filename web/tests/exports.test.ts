/**
 * Contract for roadmap 2.1c (`.agents/tasks/2.1c-export-menu.md`): what the
 * web sends to `POST /export`, and what it names the file.
 *
 * The API keeps no run: an export re-runs the config it is given. So the
 * request must describe the run *on screen*. Three ways it can go wrong, each
 * pinned below: the user changed the incident picker after the run, applied
 * a fix that has not been measured yet, or ran the 5-seed confidence mode
 * (the headline is the typical seed's run, not the config's own seed).
 * Make these pass. Do not edit them.
 */
import { describe, expect, it } from "vitest";
import {
  exportChoices,
  exportFilename,
  exportRequest,
  filenameFromDisposition,
} from "@/core/lib/exports";
import type { ExportFormatInfo, MeasuredRun, SimulationConfig } from "@/core/types";

const CONFIG: SimulationConfig = {
  seed: 42,
  duration: 60,
  topology: {
    nodes: [{ name: "db", role: "database", max_capacity: 3, service_time: 3 }],
    edges: [],
  },
  traffic: { base_rps: 2, duration: 60 },
  chaos: [],
};

const RUN: MeasuredRun = { config: CONFIG, playbook: "db_failover" };

describe("exportRequest", () => {
  it("names the measured run: its config, its playbook, the format", () => {
    expect(exportRequest(RUN, {}, "csv")).toEqual({
      config: CONFIG,
      playbook: "db_failover",
      format: "csv",
    });
  });

  it("keeps a clean run clean", () => {
    expect(exportRequest({ config: CONFIG, playbook: null }, {}, "json").playbook).toBe(null);
  });

  it("exports the typical seed of a confidence run", () => {
    const request = exportRequest(RUN, { typical_seed: 44 }, "xlsx");
    expect(request.config.seed).toBe(44);
    // everything else about the config is untouched
    expect({ ...request.config, seed: 42 }).toEqual(CONFIG);
  });

  it("uses the config's own seed for a single run", () => {
    expect(exportRequest(RUN, { typical_seed: null }, "csv").config.seed).toBe(42);
    expect(exportRequest(RUN, {}, "csv").config.seed).toBe(42);
  });

  it("does not mutate the measured config", () => {
    const frozen = structuredClone(CONFIG);
    exportRequest(RUN, { typical_seed: 7 }, "csv");
    expect(CONFIG).toEqual(frozen);
  });
});

describe("exportFilename", () => {
  it("matches the name the API sends", () => {
    expect(exportFilename(exportRequest(RUN, {}, "csv"), "csv")).toBe(
      "eleven-db_failover-seed42.csv",
    );
    expect(
      exportFilename(exportRequest({ config: CONFIG, playbook: null }, { typical_seed: 44 }, "xlsx"), "xlsx"),
    ).toBe("eleven-run-seed44.xlsx");
  });
});

describe("filenameFromDisposition", () => {
  it("reads the quoted file name", () => {
    expect(filenameFromDisposition('attachment; filename="eleven-run-seed42.csv"')).toBe(
      "eleven-run-seed42.csv",
    );
  });

  it("reads an unquoted file name", () => {
    expect(filenameFromDisposition("attachment; filename=run.json")).toBe("run.json");
  });

  it("returns null when there is nothing usable", () => {
    expect(filenameFromDisposition(null)).toBe(null);
    expect(filenameFromDisposition("attachment")).toBe(null);
    expect(filenameFromDisposition('attachment; filename=""')).toBe(null);
  });

  it("never returns a path", () => {
    expect(filenameFromDisposition('attachment; filename="../../etc/passwd"')).toBe("passwd");
    expect(filenameFromDisposition('attachment; filename="C:\\\\Users\\\\x\\\\run.csv"')).toBe(
      "run.csv",
    );
  });
});

describe("exportChoices", () => {
  const formats: ExportFormatInfo[] = [
    { name: "csv", label: "Per-request log (CSV)", extension: "csv", media_type: "text/csv; charset=utf-8", tier: "free" },
    { name: "json", label: "Full run (JSON)", extension: "json", media_type: "application/json", tier: "free" },
    {
      name: "xlsx",
      label: "Workbook (Excel)",
      extension: "xlsx",
      media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      tier: "paid",
    },
  ];

  it("keeps the API's order and labels", () => {
    expect(exportChoices(formats).map((c) => [c.name, c.label, c.extension])).toEqual([
      ["csv", "Per-request log (CSV)", "csv"],
      ["json", "Full run (JSON)", "json"],
      ["xlsx", "Workbook (Excel)", "xlsx"],
    ]);
  });

  it("badges the paid formats and leaves every format available", () => {
    const choices = exportChoices(formats);
    expect(choices.map((c) => c.badge)).toEqual([null, null, "Pro"]);
    // Nothing is locked in the UI: the API decides, once it knows the user (roadmap 2.2).
    expect(choices.every((c) => c.available)).toBe(true);
  });

  it("is empty for no formats", () => {
    expect(exportChoices([])).toEqual([]);
  });
});
