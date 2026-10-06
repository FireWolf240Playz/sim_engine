import { describe, expect, it } from "vitest";
import {
  SEVERITY_FILL,
  SEVERITY_TEXT,
  SEVERITY_WORD,
  fmtMoney,
  fmtMoneyFull,
  fmtPct,
  fmtSeconds,
  gradeSeverity,
  scoreSeverity,
  sizingSeverity,
  type Severity,
} from "@/core/lib/format";

const SEVERITIES: Severity[] = ["ok", "warn", "crit"];

describe("scoreSeverity", () => {
  // These thresholds mirror sim_core/score.py::score_color. If the engine
  // moves a boundary, this test is what catches the UI disagreeing with
  // the report PNG.
  it("maps the engine's score bands", () => {
    expect(scoreSeverity(100)).toBe("ok");
    expect(scoreSeverity(75)).toBe("ok");
    expect(scoreSeverity(74.9)).toBe("warn");
    expect(scoreSeverity(50)).toBe("warn");
    expect(scoreSeverity(49.9)).toBe("crit");
    expect(scoreSeverity(0)).toBe("crit");
  });

  it("treats a missing score as stressed rather than healthy", () => {
    expect(scoreSeverity(null)).toBe("warn");
  });
});

describe("gradeSeverity", () => {
  it("maps cost grades", () => {
    expect(gradeSeverity("A")).toBe("ok");
    expect(gradeSeverity("B")).toBe("ok");
    expect(gradeSeverity("C")).toBe("warn");
    expect(gradeSeverity("D")).toBe("crit");
    expect(gradeSeverity("F")).toBe("crit");
    expect(gradeSeverity(null)).toBe("crit");
  });
});

describe("sizingSeverity", () => {
  it("treats undersized as critical and oversized as a warning", () => {
    expect(sizingSeverity("right_sized")).toBe("ok");
    expect(sizingSeverity("oversized")).toBe("warn");
    expect(sizingSeverity("undersized")).toBe("crit");
  });
});

describe("severity lookup tables", () => {
  it("covers every severity, so no lookup can return undefined", () => {
    for (const sev of SEVERITIES) {
      expect(SEVERITY_TEXT[sev]).toBeTruthy();
      expect(SEVERITY_FILL[sev]).toBeTruthy();
      expect(SEVERITY_WORD[sev]).toBeTruthy();
    }
  });

  it("names a token rather than a literal colour", () => {
    // The whole point of the token refactor: no component may carry hex.
    for (const sev of SEVERITIES) {
      expect(SEVERITY_FILL[sev]).toMatch(/^fill-sev-/);
      expect(SEVERITY_TEXT[sev]).toMatch(/^text-sev-/);
    }
  });
});

describe("fmtMoney", () => {
  it("compacts large figures", () => {
    expect(fmtMoney(31_536_000)).toBe("$31.5M");
    expect(fmtMoney(1517)).toBe("$1.5k");
    expect(fmtMoney(42)).toBe("$42");
  });

  // Regression: a simulation run is seconds long, so per-run costs and
  // their deltas are fractions of a dollar. Rounding to whole dollars
  // made the entire Compare cost row — values AND deltas — read "$0".
  it("keeps sub-dollar figures readable", () => {
    expect(fmtMoney(0.42)).toBe("$0.42");
    expect(fmtMoney(0.07)).toBe("$0.07");
    expect(fmtMoney(0.999)).toBe("$1.00");
  });

  it("does not round a non-zero cost down to zero", () => {
    expect(fmtMoney(0.004)).toBe("<$0.01");
    expect(fmtMoney(0.0000001)).toBe("<$0.01");
  });

  it("distinguishes an actual zero", () => {
    expect(fmtMoney(0)).toBe("$0");
    expect(fmtMoney(null)).toBe("$0");
    expect(fmtMoney(undefined)).toBe("$0");
  });

  it("keeps the sign on negative deltas", () => {
    expect(fmtMoney(-0.42)).toBe("-$0.42");
    expect(fmtMoney(-1500)).toBe("-$1.5k");
  });
});

describe("fmtMoneyFull", () => {
  it("groups thousands and drops cents", () => {
    expect(fmtMoneyFull(1234.56)).toBe("$1,235");
    expect(fmtMoneyFull(0)).toBe("$0");
    expect(fmtMoneyFull(null)).toBe("$0");
  });
});

describe("fmtPct / fmtSeconds", () => {
  it("formats fractions as percentages", () => {
    expect(fmtPct(0.9876, 1)).toBe("98.8%");
    expect(fmtPct(1)).toBe("100%");
    expect(fmtPct(0)).toBe("0%");
  });

  it("distinguishes a zero value from a missing one", () => {
    expect(fmtPct(0)).toBe("0%");
    expect(fmtPct(null)).toBe("n/a");
    expect(fmtSeconds(0)).toBe("0.0s");
    expect(fmtSeconds(null)).toBe("n/a");
    expect(fmtSeconds(undefined)).toBe("n/a");
  });

  it("honours the digit count", () => {
    expect(fmtSeconds(1.2345, 2)).toBe("1.23s");
  });
});
