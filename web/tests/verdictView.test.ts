/**
 * Contract for 1.3f (`.agents/tasks/1.3f-simplify-render.md`): finding
 * cards are keyed by what they are (id + node), not by their position.
 * With index keys, resolving the first finding gave every card below it
 * a new key, and React unmounted and rebuilt each of them.
 * Make these pass — do not edit them.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { VerdictPanel } from "@/core/components/VerdictPanel";
import { findingKeys } from "@/core/lib/verdictView";
import type { Finding, Summary } from "@/core/types";

const SLA: Finding = { id: "sla_breach", severity: "crit", text: "SLA breached", title: "SLA breached" };
const DB_UNDER: Finding = { id: "undersized", severity: "warn", text: "db is undersized", node: "db" };
const WORKER_UNDER: Finding = { id: "undersized", severity: "warn", text: "worker is undersized", node: "worker" };
const WEAK_LINK: Finding = { id: "util_weak_link", severity: "warn", text: "db is the weak link", node: "db" };
const FINDINGS = [SLA, DB_UNDER, WORKER_UNDER, WEAK_LINK];

describe("findingKeys", () => {
  it("gives every finding a distinct key, even when ids repeat across nodes", () => {
    expect(new Set(findingKeys(FINDINGS)).size).toBe(FINDINGS.length);
  });

  it("keeps findings that share id and node distinct", () => {
    const keys = findingKeys([SLA, structuredClone(SLA)]);
    expect(keys[0]).not.toBe(keys[1]);
  });

  // The bug: resolving a finding shifted every key below it.
  it("does not shift the other keys when a finding before them is resolved", () => {
    const all = findingKeys(FINDINGS);
    expect(findingKeys(FINDINGS.slice(1))).toEqual(all.slice(1));
    const withoutDb = FINDINGS.filter((f) => f !== DB_UNDER);
    expect(findingKeys(withoutDb)).toEqual(all.filter((_, i) => FINDINGS[i] !== DB_UNDER));
  });

  it("follows the finding, not its position, when the order changes", () => {
    const [a, b] = findingKeys([SLA, WEAK_LINK]);
    expect(findingKeys([WEAK_LINK, SLA])).toEqual([b, a]);
  });

  it("is the same for the same finding from a fresh response", () => {
    expect(findingKeys(structuredClone(FINDINGS))).toEqual(findingKeys(FINDINGS));
  });
});

describe("VerdictPanel markup", () => {
  it("still draws one card per finding", () => {
    const summary = { findings: FINDINGS, resilience_score: 81.2 } as unknown as Summary;
    const html = renderToStaticMarkup(createElement(VerdictPanel, { summary }));
    expect(html.match(/<article/g) ?? []).toHaveLength(FINDINGS.length);
    expect(html).toContain("SLA breached");
  });
});
