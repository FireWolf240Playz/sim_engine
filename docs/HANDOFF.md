# Handoff — 2026-11-14 2.1 export center built; browser check and merge are left

## Task
Check the Export panel in the browser, then merge `feat/2.1-export-center` and mark roadmap 2.1 done.

## Open these files, in this order
- `web/src/core/components/ExportMenu.tsx` — the panel to look at
- `web/src/app/page.tsx` — where it is mounted, after "Fix it"
- `.agents/tasks/2.1c-export-menu.md` — the card and its `## Result`
- `docs/ROADMAP.md` §2.1 — to mark done; §2.2 is the next card

## Done
- Per-request log, three formats, one registry (`sim_core/metrics.py`, `sim_core/exports.py`)
- `GET /export/formats`, `POST /export`, `--export` on `run` and `demo` (`api/routes/export.py`, `sim_core/cli/sim.py`)
- Export request and file-name helpers (`web/src/core/lib/exports.ts`)
- `api.exportFormats`, `api.exportRun`, blob download path (`web/src/core/api/client.ts`)
- `measuredRun` on the run state (`web/src/core/state/RunStateContext.tsx`)
- Export panel (`web/src/core/components/ExportMenu.tsx`, `web/src/app/page.tsx`)

## Next
1. Reviewer: in the browser, download each format after a single run and after a 5-seed run; light + dark
2. Reviewer: change the incident picker without running, export, and confirm the file is the run on screen
3. Owner: commit 2.1c, merge to `main`, push
4. Card author: mark 2.1 done in `docs/ROADMAP.md`; measure and write the 2.2 card (paid gating, 402)

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- No browser, Playwright or dev server for the executor: checks by eye happen at review
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- The API keeps no run: an export re-simulates, so the request carries the measured config, its playbook and, in 5-seed mode, `typical_seed`
- A playbook starts 5 s in at the earliest: a 6 s fixture reads the same with or without one
- Excel and JSON exports include the Fix-it plan: 5–13 s on the 19-node examples
- The `paid` tier is a badge only until 2.2: nothing refuses a format
- PowerShell 5.1 drops pytest's final "N passed" line through a pipe: judge by exit code and the dots

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 179/179, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # exit 0, no skips
```
