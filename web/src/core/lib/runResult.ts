/**
 * 1.3f — fix data identity at the source.
 *
 * `replaceEqualDeep` (already in `@tanstack/react-query`) walks the new
 * result and reuses every part of the previous one whose value is equal,
 * so unchanged subtrees keep their identity. That is what lets the plain
 * `memo` on the diagram and its node cards skip by default — no comparators.
 *
 * It never mutates `prev`, and with a null `prev` it simply returns the
 * new result untouched.
 */
import { replaceEqualDeep } from "@tanstack/react-query";
import type { SimulateResponse } from "../types";

/**
 * Share structure between two results: unchanged parts keep their
 * identity, changed parts come from `next`.
 */
export function shareResult(
  prev: SimulateResponse | null,
  next: SimulateResponse,
): SimulateResponse {
  if (prev === null) return next;
  return replaceEqualDeep(prev, next);
}
