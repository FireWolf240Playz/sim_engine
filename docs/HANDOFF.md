# Handoff — 2026-10-10 Wave 1 done; no active task

## Task
None. Wait until a 2.1 export-center card exists in `.agents/tasks/` and this file points at it.

## Open these files, in this order
- `docs/ROADMAP.md` §2.1 — the export center (CSV, Excel, JSON; first new engine dependency `openpyxl`)

## Done
- 1.3h verified sizing labels: `verified_status` / `verified_capacity`, `sizingLabel`; scores unchanged
- 1.4 node inspector: `inspectNode` + `NodeInspector`; click / Enter / Space opens, Esc closes
- Both checked in Chrome, light + dark

## Next
1. Card author: measure and prototype 2.1, then write its card + failing tests
2. Executor: take lock rows, follow the card's steps in order, append `## Result`

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- No browser, Playwright or dev server for the executor: checks by eye happen at review
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- Per-node failures, retries and p95 contribution are not in `summary()`; an engine follow-up
- PowerShell 5.1 drops pytest's final "N passed" line through a pipe: judge by exit code and the dots
- `web/tests/fixtures/suggestions_applied.yaml` is written by vitest and read by pytest: run web tests first

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 160/160, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # green, no skips
```
