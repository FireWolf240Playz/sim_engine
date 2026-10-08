# Handoff → Claude Code — updated 2026-10-08

For the next **Claude** session. Qwen's live work order is `docs/HANDOFF.md`, a
different document; do not overwrite it while Qwen holds locks.

**Read order:** this file → `docs/AGENTS.md` (repo map) → `.agents/LOCKS.md`.

---

## State (2026-10-08, 23:55)

`main` is at `f2a0833` (1.2 rich verdict card, committed). Nothing newer is
on `main` yet.

| Where | What | Status |
|---|---|---|
| working tree (uncommitted) | 1.3 right-sizing, Qwen's attempt 1 + Claude's docs re-sync | green; Qwen now doing `.agents/tasks/1.3c-review-fixes.md` |
| branch `qwen/1.3-attempt-1` (`37b2d54`) | snapshot of that attempt before review | reference only, never merge |
| branch `fix/multi-seed-timeline` (`ea0ce01`) | Claude: 5-seed runs rendered an empty timeline; now typical run + P95 band across seeds | done, approved by Alexander in preview; **merge after 1.3 commits** |
| preview servers | `fix/multi-seed-timeline` on API :8001 / UI :3001, worktree `scratchpad/preview` | stop them and remove the worktree after the merge (remove the `web/node_modules` junction first with `cmd /c rmdir`) |

## The loop (agreed with Alexander, repeat for every feature)

1. Claude: task card in `.agents/tasks/` + failing tests, both Claude-locked in `LOCKS.md`.
2. Qwen: makes them pass, appends `## Result`. Never commits, never marks ✅.
3. Claude: snapshots the attempt to `qwen/<item>-attempt-N` (temp `GIT_INDEX_FILE`
   → `write-tree` → `commit-tree`, so the working tree is untouched), runs every
   suite, reviews the diff; then either a follow-up card with new failing
   tests, or approval.
4. Alexander commits; Claude marks the roadmap ✅ and re-syncs `docs/AGENTS.md`.

## Next, in order

1. When 1.3c has a `## Result`: review the diff (the 5 tests, per-row Apply,
   panel hiding, `api/` reverted). Browser-check on the demo topology.
2. Commit 1.3, with explicit paths. Then merge `fix/multi-seed-timeline`:
   `page.tsx`, `types.ts` and `TimelineChart.tsx` overlap; hand-merge if git stops.
   After the merge, add `timeseries_band` / `typical_seed` to `docs/AGENTS.md`.
3. Mark 1.3 ✅ in `docs/ROADMAP.md`.
4. Ask Alexander: should the 1.2 `undersized` finding quote the suggestion's
   number instead of `recommended_capacity` ("size-to 138")? Its text is
   pinned in `tests/test_findings.py:251`.
5. Next feature: 1.4 node inspector. Card + failing tests first.

## Open items, lower priority

- `ruff --fix` pass (~410 auto-fixable); only when no locks are held.
- Install mypy (`pip install -e .[dev]`) and measure `--strict`.
- Old run output still tracked at the repo root (`summary.json`, `rt.json`,
  `rt.yaml`, `step2.json`, `out.png`, `eleven_report.png`): `git rm --cached`.
- Stray `C:\Users\ayord\Desktop\package-lock.json` (outside the repo) makes
  `next build` warn about the workspace root.

## How to work here

Stage explicit paths only, never `git add -A`: Qwen's work sits uncommitted in
the same tree. Check file mtimes before assuming Qwen has stopped. Build
isolated fixes in a `git worktree` under the scratchpad. For a dev server
there, use `next dev --webpack`: Turbopack rejects a junctioned
`node_modules`. On PowerShell 5.1, pass commit messages with
`git commit -F <file>`.
