import { describe, expect, it } from "vitest";
import { buildVerdictText, fmtPoints, pointsToneClass } from "@/core/lib/verdict";
import type { Finding, Summary } from "@/core/types";

function makeSummary(overrides: Partial<Summary> = {}): Summary {
  const findings: Finding[] = [
    {
      id: "sla_breach",
      severity: "crit",
      text: "SLA compliance 90.0% is below the 95% floor",
      title: "SLA breach",
      why: "At 90%, about 1 in 10 requests were too slow.",
      impact: "~10% of 600 requests would have violated the SLO.",
      evidence: [{ label: "SLA compliance", value: "90.0%" }],
      recommendation: "Fix the critical bottleneck first.",
    },
    {
      id: "retry_storm",
      severity: "warn",
      text: "1.5% of requests needed a retry",
      title: "Retry storm",
      why: "Every retry is a second shot at the same wall.",
      evidence: [{ label: "retries", value: "9" }],
      recommendation: "Size up the struggling node and add backoff.",
    },
  ];
  return {
    requests: 600,
    completion_rate: 0.98,
    failed_requests: 12,
    sla_compliance: 0.9,
    avg_latency: 0.9,
    p50_latency: 0.6,
    p95_latency: 1.4,
    p99_latency: 2.1,
    cache_hit_rate: 0.8,
    total_retries: 9,
    total_cost: 12.5,
    cost_breakdown_by_component: { worker: 10 },
    component_sizing: {},
    sla_target: 0.8,
    cost_per_completed_request: 0.02,
    resilience_score: 71.2,
    cost_grade: "C",
    findings,
    score_explanation: {
      score: 71.2,
      clamped: false,
      band: "Solid",
      terms: [
        { label: "SLA compliance", points: 54, detail: "90.0% of requests met the SLA × 60 pts" },
        { label: "Completion", points: 39.2, detail: "98.0% of requests completed × 40 pts" },
        { label: "Failed requests", points: -0.6, detail: "12 of 600 requests never completed" },
        { label: "Retry pressure", points: 0, detail: "no retries" },
        { label: "P95 headroom", points: -10, detail: "p95 1.4s runs 75% over the 0.8s SLA budget" },
      ],
    },
    verdict_headline:
      "This architecture breaks under this load — 1 critical failure mode found, plus 1 structural risk. The score (71/100) can't be trusted until the criticals are fixed.",
    cost_base: 12.5,
    cost_metered: 0,
    cost_extrapolation: null,
    steady_state_cost_extrapolation: null,
    ...overrides,
  };
}

describe("fmtPoints", () => {
  it("signs credits and penalties, with a true minus", () => {
    expect(fmtPoints(54)).toBe("+54");
    expect(fmtPoints(1.5)).toBe("+1.5");
    expect(fmtPoints(-30)).toBe("−30");
    expect(fmtPoints(-0.6)).toBe("−0.6");
    expect(fmtPoints(0)).toBe("±0");
  });
});

describe("pointsToneClass", () => {
  it("maps sign to the severity text token", () => {
    expect(pointsToneClass(5)).toBe("text-sev-ok");
    expect(pointsToneClass(-5)).toBe("text-sev-crit");
    expect(pointsToneClass(0)).toBe("text-ink-dim");
  });
});

describe("buildVerdictText", () => {
  it("flattens the rich contract into the CLI-shaped block", () => {
    const text = buildVerdictText(makeSummary(), {
      architectureLabel: "demo architecture",
      context: "under db failover",
    });
    expect(text).toContain("Verdict — demo architecture · under db failover");
    expect(text).toContain("This architecture breaks under this load");
    expect(text).toContain("Score 71.2/100 (Solid)");
    expect(text).toContain("[CRIT] SLA compliance 90.0% is below the 95% floor");
    expect(text).toContain("    why: At 90%, about 1 in 10 requests were too slow.");
    expect(text).toContain("    impact: ~10% of 600 requests would have violated the SLO.");
    expect(text).toContain("    fix: Fix the critical bottleneck first.");
    // A finding without `impact` must not print an impact line.
    expect(text).not.toContain("    impact: Every retry");
  });

  it("omits optional context and the confidence line for single runs", () => {
    const text = buildVerdictText(makeSummary());
    expect(text.startsWith("Verdict\n")).toBe(true);
    expect(text).not.toContain("confidence:");
  });

  it("reports multi-seed confidence when present", () => {
    const text = buildVerdictText(makeSummary(), { seeds: [42, 43, 60] });
    expect(text).toContain("confidence: 3 seeds (42, 43, 60)");
  });
});
