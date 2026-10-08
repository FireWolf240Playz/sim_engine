import { describe, expect, it } from "vitest";
import {
  completeRecord,
  describeRecord,
  historyText,
  recordDelta,
  startRecord,
} from "@/core/lib/fixHistory";
import { buildVerdictText } from "@/core/lib/verdict";
import type { Finding, FixOutcome, Suggestion, Summary } from "@/core/types";

const BREACH: Finding = {
  id: "sla_breach",
  severity: "crit",
  text: "SLA compliance 88% is below the 95% floor",
  title: "SLA breach",
};
const HEALTHY: Finding = { id: "healthy", severity: "info", text: "No weaknesses", title: "Healthy" };

const RAISE: Suggestion = {
  node: "db",
  param: "max_capacity",
  current: 3,
  proposed: 6,
  reason: "db: raise max_capacity 3 → 6 — clears sla breach",
  est_monthly_delta: 129.6,
};
const CUT: Suggestion = { ...RAISE, node: "lb", current: 10, proposed: 4, est_monthly_delta: -172.8 };

const OUTCOME: FixOutcome = {
  seed: 42,
  score_before: 81.2,
  score_after: 99,
  band_before: "At risk",
  band_after: "Resilient",
  resolved: [{ id: "sla_breach", node: null, severity: "crit", title: "SLA breach" }],
  unfixed: [],
  monthly_delta: -43.2,
};

function summary(score: number, band: string, findings: Finding[], extra: Partial<Summary> = {}) {
  return {
    resilience_score: score,
    score_explanation: { score, clamped: false, band, terms: [] },
    findings,
    suggestions: [RAISE, CUT],
    fix_outcome: OUTCOME,
    ...extra,
  } as unknown as Summary;
}

const BEFORE = summary(81.2, "At risk", [BREACH]);
const AFTER = summary(99, "Resilient", [HEALTHY], { suggestions: [], fix_outcome: null });

describe("fix history", () => {
  it("records an apply as pending, then completes it with the re-run", () => {
    const started = startRecord([], BEFORE, [RAISE, CUT], "db_failover");
    expect(started).toHaveLength(1);
    expect(started[0]).toMatchObject({
      step: 1,
      playbook: "db_failover",
      after: null,
      failed: false,
      changes: [
        { node: "db", before: 3, after: 6 },
        { node: "lb", before: 10, after: 4 },
      ],
    });
    expect(started[0].monthlyDelta).toBeCloseTo(-43.2);
    expect(started[0].predicted).toEqual(OUTCOME);

    const done = completeRecord(started, AFTER);
    expect(done[0].after).toEqual({ score: 99, band: "Resilient", serious: [] });
    expect(recordDelta(done[0]).cleared.map((f) => f.id)).toEqual(["sla_breach"]);
    expect(recordDelta(done[0]).introduced).toEqual([]);
  });

  it("drops the prediction for a partial apply (it covers the whole set only)", () => {
    expect(startRecord([], BEFORE, [RAISE], null)[0].predicted).toBeNull();
  });

  it("marks a failed re-run instead of leaving it pending forever", () => {
    const failed = completeRecord(startRecord([], BEFORE, [RAISE, CUT], null), null);
    expect(failed[0].failed).toBe(true);
    expect(describeRecord(failed[0])).toContain("re-run failed");
  });

  it("numbers steps and completes only the newest pending one", () => {
    const one = completeRecord(startRecord([], BEFORE, [RAISE, CUT], null), AFTER);
    const two = startRecord(one, AFTER, [CUT], null);
    expect(two.map((r) => r.step)).toEqual([1, 2]);
    const done = completeRecord(two, AFTER);
    expect(done[0]).toBe(one[0]);
    expect(done[1].after).not.toBeNull();
  });

  it("describes a record in one plain line", () => {
    const done = completeRecord(startRecord([], BEFORE, [RAISE, CUT], "db_failover"), AFTER);
    expect(describeRecord(done[0])).toBe(
      "Fix 1 under db failover: db 3→6, lb 10→4 · score 81.2 → 99 Resilient · cleared SLA breach · −$43/mo",
    );
  });

  it("the copied verdict carries the change log", () => {
    const done = completeRecord(startRecord([], BEFORE, [RAISE, CUT], null), AFTER);
    const text = buildVerdictText(AFTER, { history: done });
    expect(text).toContain("Changes applied (each verified by a re-run):");
    expect(text).toContain("db 3→6");
    expect(historyText([])).toBe("");
  });
});
