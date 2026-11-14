# Handoff — 2026-11-14 engine review fixes, second pass; 2.1 still waits for its card

## Task
Review and push `fix/review-followups`; 2.1 export center stays blocked until its card exists in `.agents/tasks/`.

## Open these files, in this order
- `tests/test_consistency.py` — grade = label, explanation = score, one finding per node
- `tests/test_fix_plan.py` — what the plan may cost, and which run it describes
- `tests/test_edge_groups.py` — alternative edges, and what the importer now infers
- `web/src/core/lib/demo.ts` — re-measured demo scores

## Done
- Cost grade reads the verified label; score and explanation share one formula (`score.py`, `engine.py`)
- Presets bill their list price; an outage no longer lowers the provisioned bill (`presets.py`, `metrics.py`)
- A dead node with a queue reads saturated; sizing demand comes from the samples (`topology.py`, `metrics.py`)
- Plan reuses the shown run, skips runs that cannot differ, keeps an unseeded run's seed (`rightsize.py`, `engine.py`)
- Traffic is exact across spikes and exponential growth (`traffic.py`)
- Strict config, cache typing, run-size limit, `Edge.group` (`config.py`, `topology.py`, `importers.py`)
- API work budget and CORS default (`api/limits.py`, `api/main.py`)

## Next
1. Check `/presets` in the browser: the `$/hr` column now shows the whole instance
2. Show `unfinished_requests` and `waited_requests` in the node inspector (`nodeInspector.ts`)
3. Plan cost on large topologies: 5–13 s per run on the 19-node examples
4. Card author: measure and prototype 2.1, then write its card + failing tests

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- No browser, Playwright or dev server for the executor: checks by eye happen at review
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- Demo scores (seed 42): clean 97, db_failover 73, latency spike 86, cache storm 59, dependency cascade 57
- A constant traffic rate must keep its exact draws: every pinned score rests on it
- A chaos cycle is `duration + interval`
- A node with `waited_requests == 0` replays the same run at any larger size
- PowerShell 5.1 drops pytest's final "N passed" line through a pipe: judge by exit code and the dots
- `web/tests/fixtures/suggestions_applied.yaml` is written by vitest and read by pytest: run web tests first

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 160/160, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # exit 0, no skips
```
