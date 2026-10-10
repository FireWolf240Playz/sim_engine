# Handoff — 2026-10-10 1.3h verified sizing labels (card in preparation)

## Task
Wait. The 1.3h card and its failing tests are being written; start only when `.agents/tasks/1.3h-*.md` exists and this file points at it.

## Open these files, in this order
- `docs/ROADMAP.md` §1.3, "Follow-up, 1.3h" — what the sizing label must become
- `sim_core/suggestions.py` and `sim_core/rightsize.py` — where the label and the verified fix come from today

## Done
- 1.3e–1.3g render streamlining shipped: `shareResult`, two plain `memo`s (`TopologyDiagram`, `NodeBody`), `findingKeys`, stale-node marking, no whole-page dim
- Attempt snapshots are local branches only, never pushed

## Next
1. Card author: measure the label disagreement on the demo, prototype the fix, write the 1.3h card + failing tests
2. Executor: take lock rows, follow the card's steps in order, append `## Result`
3. Still owed from 1.3g: the by-eye checks (MEASURING…, NOT MEASURED, light and dark) on the next dev-server session

## Do not touch
- Every `web/tests/*.test.ts` and `tests/test_*.py` — locked contracts
- No new `memo`, no comparators without a measured number on the card
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- `_suggestions` only proposes a size that differs from the current one, so pending == suggestions right after a successful run
- `tsc` type-checks `web/tests/`; a card's Verify names which errors are allowed
- PowerShell 5.1: chain with `;`, never `&&`

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 136/136, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # green, no skips
```
