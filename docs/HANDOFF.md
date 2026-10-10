# Handoff — 2026-10-10 1.3h verified sizing labels

## Task
Make `tests/test_verified_sizing.py` and `web/tests/verifiedSizing.test.ts` pass: every sizing word and size shows what "Fix it" does.

## Open these files, in this order
- `.agents/tasks/1.3h-verified-labels.md` — the card: eight numbered steps, prototyped (143 of 143 vitest, pytest green)
- `sim_core/rightsize.py` — append `verified_sizing`
- `sim_core/engine.py` `run()` — attach `verified_status` / `verified_capacity` in the `if suggest:` block
- `web/src/core/lib/format.ts` — `sizingLabel`; then `topologyView.ts`, `SizingTable.tsx`

## Done
- 1.3e–1.3g render streamlining shipped and committed
- Measured: 9 of 25 demo labels disagree with the verified fix; this card adds no simulation runs

## Next
1. Take lock rows for the card's files, then do steps 1–8 in order
2. Run Verify; append `## Result`; release locks. No browser or Playwright checks: the reviewer does those
3. Stop for review before 1.4

## Do not touch
- `metrics.py`, `score.py`, `findings.py`, `suggestions.py`, `demo.ts` — the old `status` and every score stay (pinned by a test)
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- git: no commits, branches or `git add`

## Constraints discovered
- The right-sizer searches with the utilisation `status`, so the verified answer goes in new fields, never into `status`
- `ruff check .` has ~525 old findings: compare the changed files' count (69) before and after
- PowerShell 5.1: chain with `;`, never `&&`

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # all green, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # green, no skips
```
