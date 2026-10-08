/**
 * Roadmap 1.3 — "fix it" frontend logic, pure and dependency-free.
 *
 * Mirrors the backend's `sim_core/suggestions.py` contract (the spec lives
 * in `web/tests/suggestions.test.ts` and `tests/test_suggestions.py`):
 *
 * - `applySuggestions` — immutably patches the suggested nodes' capacity
 *   so "apply & re-run" can hit the existing run path with the changed
 *   config. Never mutates the input; unknown nodes are ignored.
 * - `buildFixDiff` — the before → after rows + total estimated monthly
 *   delta behind the Fix-diff panel.
 * - `fixYamlSnippet` / `terraformSnippet` — the copyable handoff
 *   artifacts. YAML is primary; Terraform is a clearly-labeled
 *   illustrative mapping (Eleven never mutates production).
 * - `configToYaml` — deterministic full-config YAML emitter. The last web
 *   test snapshots it to `tests/fixtures/suggestions_applied.yaml`, and
 *   `tests/test_suggestions.py` loads that file through Pydantic — so the
 *   engine itself proves the YAML the UI hands out is valid.
 */
import type { SimulationConfig, Suggestion, TopologyNode } from "../types";

const INDENT = "  ";

export interface FixDiffRow {
  node: string;
  param: string;
  before: number;
  after: number;
  monthlyDelta: number | null;
}

export interface FixDiff {
  rows: FixDiffRow[];
  /** Sum of the known deltas; `null` when no row carries a price. */
  totalMonthlyDelta: number | null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function nodeByName(config: SimulationConfig, name: string): TopologyNode | undefined {
  return config.topology.nodes.find((node) => node.name === name);
}

/**
 * Return a new config with every known suggestion's `param` set to its
 * `proposed` value. Immutably — the input is never mutated — and
 * idempotent: applying the same set twice changes nothing more (the
 * second pass sees `current === proposed` and writes the same value).
 * Unknown nodes are ignored; an empty list returns the config as-is.
 */
export function applySuggestions(
  config: SimulationConfig,
  suggestions: Suggestion[],
): SimulationConfig {
  if (suggestions.length === 0) return config;
  const byNode = new Map<string, Suggestion>();
  for (const suggestion of suggestions) byNode.set(suggestion.node, suggestion);

  const nodes = config.topology.nodes.map((node) => {
    const suggestion = byNode.get(node.name);
    if (!suggestion) return node;
    return { ...node, [suggestion.param]: suggestion.proposed };
  });
  return { ...config, topology: { ...config.topology, nodes } };
}

/**
 * The suggestions that still have an effect on `config`: the node exists and
 * its current value for the param differs from the proposed one. After
 * "Apply & re-run" the config is patched immediately, but the panel still
 * holds the previous run's suggestions until the re-run lands (and forever if
 * it fails); those stale rows read "×3 → ×3" and Apply re-applies a no-op.
 * Filtering them out is the guard — `buildFixDiff` and the panel both use it.
 */
export function pendingSuggestions(
  config: SimulationConfig,
  suggestions: Suggestion[],
): Suggestion[] {
  return suggestions.filter((s) => {
    const node = nodeByName(config, s.node);
    if (!node) return false;
    return (node as unknown as Record<string, unknown>)[s.param] !== s.proposed;
  });
}

/**
 * The Fix-diff rows, in suggestion order, for the pending suggestions only.
 * `before` is the config's current value for the node (falling back to the
 * suggestion's own `current`), `monthlyDelta` is the suggestion's estimate —
 * `null` when unpriced. `totalMonthlyDelta` sums only the known deltas, `null`
 * when none is known. Rows already applied to `config` drop out.
 */
export function buildFixDiff(
  config: SimulationConfig,
  suggestions: Suggestion[],
): FixDiff {
  const rows: FixDiffRow[] = pendingSuggestions(config, suggestions).map((s) => {
    const node = nodeByName(config, s.node);
    const before = node && isFiniteNumber(node.max_capacity) ? node.max_capacity : s.current;
    return {
      node: s.node,
      param: s.param,
      before,
      after: s.proposed,
      monthlyDelta: isFiniteNumber(s.est_monthly_delta) ? s.est_monthly_delta : null,
    };
  });
  const known = rows
    .map((row) => row.monthlyDelta)
    .filter((value): value is number => isFiniteNumber(value));
  return {
    rows,
    totalMonthlyDelta: known.length ? known.reduce((sum, value) => sum + value, 0) : null,
  };
}

// ---------------------------------------------------------------------------
// Snippets — the copyable handoff artifacts
// ---------------------------------------------------------------------------

/**
 * The changed lines as a YAML fragment: only the touched nodes, in
 * suggestion order. Empty string when there is nothing to fix.
 */
export function fixYamlSnippet(
  config: SimulationConfig,
  suggestions: Suggestion[],
): string {
  const lines: string[] = [];
  for (const s of suggestions) {
    if (!nodeByName(config, s.node)) continue;
    lines.push(`    - name: ${yamlString(s.node)}`);
    lines.push(`      ${s.param}: ${yamlScalar(s.proposed)}`);
  }
  if (lines.length === 0) return "";
  return [
    "# Eleven fix — apply these capacity changes, then re-run to confirm",
    "topology:",
    "  nodes:",
    ...lines,
  ].join("\n") + "\n";
}

/** role → one representative provider resource (illustrative only). */
const ROLE_TO_TF_RESOURCE: Record<string, string> = {
  load_balancer: "aws_lb",
  worker: "aws_instance",
  database: "aws_db_instance",
  cache: "aws_elasticache_cluster",
  generic: "null_resource",
};

/**
 * A clearly-labeled illustrative Terraform sketch of the changes. The
 * role → resource mapping is approximate on purpose — the YAML is the
 * source of truth, and Eleven never emits production config.
 */
export function terraformSnippet(
  config: SimulationConfig,
  suggestions: Suggestion[],
): string {
  const blocks: string[] = [];
  for (const s of suggestions) {
    const node = nodeByName(config, s.node);
    if (!node) continue;
    const resource = ROLE_TO_TF_RESOURCE[node.role] ?? ROLE_TO_TF_RESOURCE.generic;
    const label = node.name.replace(/[^a-zA-Z0-9_-]/g, "_");
    blocks.push(
      `resource "${resource}" "${label}" {\n  # ${s.param}: ${s.current} → ${s.proposed}\n}`,
    );
  }
  if (blocks.length === 0) return "";
  return [
    "# ILLUSTRATIVE TERRAFORM — the provider mapping is approximate; adapt",
    "# it to your real stack. The YAML above is the source of truth.",
    ...blocks,
  ].join("\n") + "\n";
}

// ---------------------------------------------------------------------------
// Deterministic YAML emitter (no dependency; PyYAML-compatible)
// ---------------------------------------------------------------------------

/**
 * Serialize any config value to a single-line YAML scalar. Strings are
 * always double-quoted (escaped), so any identifier — or anything else the
 * user types — round-trips byte-exactly through PyYAML.
 */
function yamlScalar(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "null";
  if (typeof value === "string") return yamlString(value);
  return "null";
}

function yamlString(value: string): string {
  const escaped = value
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n")
    .replace(/\t/g, "\\t");
  return `"${escaped}"`;
}

/**
 * Emit `key: value` lines for an object at the given indent level.
 * `undefined` values are skipped (the field is absent), `null` is written
 * as `null` (the field is present and empty) — Pydantic's Optional fields
 * accept both, and this keeps the emitted YAML honest.
 */
function emitObject(obj: Record<string, unknown>, indent: number, lines: string[]): void {
  const pad = INDENT.repeat(indent);
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined) continue;
    if (value === null) {
      lines.push(`${pad}${key}: null`);
    } else if (Array.isArray(value)) {
      if (value.length === 0) {
        lines.push(`${pad}${key}: []`);
      } else {
        lines.push(`${pad}${key}:`);
        emitSequence(value, indent + 1, lines);
      }
    } else if (typeof value === "object") {
      const entries = Object.entries(value).filter(([, v]) => v !== undefined);
      if (entries.length === 0) {
        lines.push(`${pad}${key}: {}`);
      } else {
        lines.push(`${pad}${key}:`);
        emitObject(value as Record<string, unknown>, indent + 1, lines);
      }
    } else {
      lines.push(`${pad}${key}: ${yamlScalar(value)}`);
    }
  }
}

/** Emit a block sequence of scalars or mappings at the given indent. */
function emitSequence(items: unknown[], indent: number, lines: string[]): void {
  for (const item of items) {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      lines.push(`${INDENT.repeat(indent)}- ${yamlScalar(item)}`);
      continue;
    }
    const obj = item as Record<string, unknown>;
    const entries = Object.entries(obj).filter(([, v]) => v !== undefined);
    if (entries.length === 0) {
      lines.push(`${INDENT.repeat(indent)}- {}`);
      continue;
    }
    const inner: string[] = [];
    emitObject(obj, indent + 1, inner);
    const [first, ...rest] = inner;
    lines.push(
      `${INDENT.repeat(indent)}- ${first.slice(INDENT.repeat(indent + 1).length)}`,
    );
    lines.push(...rest);
  }
}

/**
 * Deterministic full-config YAML (insertion key order, stable formatting).
 * The snapshot in `web/tests/suggestions.test.ts` is loaded by
 * `SimulationConfig.from_yaml` in `tests/test_suggestions.py`, which is the
 * cross-language proof that the UI's YAML is engine-valid.
 */
export function configToYaml(config: SimulationConfig): string {
  const lines: string[] = [];
  emitObject(config as unknown as Record<string, unknown>, 0, lines);
  return lines.join("\n") + "\n";
}
