# Handoff — 2026-11-14 2.1 export center: engine, API and CLI done; 2.1c web menu next

## Task
Build the export menu in the web UI by making the two red Vitest files of card 2.1c pass.

## Open these files, in this order
- `.agents/tasks/2.1c-export-menu.md` — the card: six steps, files you may edit
- `web/tests/exports.test.ts` — contract for the new `web/src/core/lib/exports.ts`
- `web/tests/exportClient.test.ts` — contract for `api.exportFormats` / `api.exportRun`
- `web/src/core/api/client.ts` — where the download path goes
- `web/src/core/state/RunStateContext.tsx` — `measuredConfig`; add `measuredRun`

## Done
- Per-request log: `RequestRecord.retries` / `failed_at` / `overdue`, `MetricsCollector.request_log()` (`sim_core/metrics.py`, `engine.py`)
- CSV, JSON and Excel from one registry, `render_export` (`sim_core/exports.py`)
- `GET /export/formats`, `POST /export` (`api/routes/export.py`, `api/schemas.py`)
- `--export` / `--export-base` on `run` and `demo` (`sim_core/cli/sim.py`)
- Export types for the frontend (`web/src/core/types.ts`)

## Next
1. Owner: commit 2.1 + 2.1b from `feat/2.1-export-center` (everything except the 2.1c card and the two web test files)
2. Executor: take lock rows, follow card 2.1c in order, append `## Result`
3. Reviewer: check the Export panel in the browser, light + dark, single run and 5-seed mode

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- `web/src/core/types.ts` — the export types are final for 2.1c
- No browser, Playwright or dev server for the executor: checks by eye happen at review
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- The API keeps no run: an export re-simulates, so the request must carry the seed of the run on screen (`typical_seed` in 5-seed mode)
- A playbook starts 5 s in at the earliest: a 6 s fixture reads the same with or without one
- A workbook is binary: `request<T>()` in `client.ts` parses text, a download needs its own `fetch`
- Excel and JSON exports include the Fix-it plan: 5–13 s on the 19-node examples, use the `heavy` timeout
- Until 2.1c lands, `npm run test:unit` shows 2 files red (6 tests) and `typecheck` fails on them; nothing else may be red
- PowerShell 5.1 drops pytest's final "N passed" line through a pipe: judge by exit code and the dots

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 166/166, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # exit 0, no skips
```
