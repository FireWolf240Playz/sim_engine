/**
 * 1.3f — stable keys for the finding cards.
 *
 * A finding is identified by `id` + `node` (an id like `undersized` can
 * repeat across nodes). Index keys are wrong: resolving the first finding
 * shifts every key below it, and React unmounts and rebuilds each of the
 * other cards. Keying by what the finding is, a repeat of the same pair
 * gets a `#2`, `#3`… suffix in encounter order — distinct, and stable no
 * matter what is added, removed or reordered around them.
 */
import type { Finding } from "../types";

/** One stable key per finding, in input order. */
export function findingKeys(rows: Finding[]): string[] {
  const seen = new Map<string, number>();
  return rows.map((row) => {
    const base = JSON.stringify([row.id, row.node ?? null]);
    const count = (seen.get(base) ?? 0) + 1;
    seen.set(base, count);
    return count === 1 ? base : `${base}#${count}`;
  });
}
