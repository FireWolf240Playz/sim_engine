# Handoff — 2026-11-14 engine review fixes; 2.1 still waits for its card

## Task
Review and push `fix/request-accounting`; 2.1 export center stays blocked until its card exists in `.agents/tasks/`.

## Open these files, in this order
- `tests/test_accounting.py` — the accounting rules the engine now keeps
- `tests/test_live_state.py` — capacity restore and per-cache decisions
- `web/src/core/lib/demo.ts` — re-measured demo scores; db_failover is now a mild incident

## Done
- Requests overdue at the horizon are recorded as failed; `unfinished_requests`, `overdue_requests` in `summary()` (`metrics.py`, `engine.py`)
- A failed request never meets the SLA (`engine.py`)
- Capacity restore serves the waiting queue (`topology.py`)
- Each cache decides from its own hit rate; `cache_outage` honours `target` (`engine.py`, `chaos.py`, `config.py`)
- Pricing errors mask the API key (`price_source/base.py`)
- Fix it settles to a fixed point, one run per distinct sizing (`rightsize.py`)

## Next
1. Decide whether to recalibrate the demo: db_failover now moves the score 97 → 94 only
2. Cost grade from `verified_status`, not the utilisation guess (`score.py`, `engine.py`)
3. Preset `cost_per_hour` is per instance but billed per slot (`presets.py`, `metrics.py`)
4. Card author: measure and prototype 2.1, then write its card + failing tests

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- No browser, Playwright or dev server for the executor: checks by eye happen at review
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- Demo scores (seed 42): clean 97, db_failover 94, latency spike 86, cache storm 59, dependency cascade 57
- Every cache visit draws from the RNG now, so multi-cache topologies changed stream order
- SimPy `Resource._do_put` returns `None`: one `_trigger_put` admits at most one request
- PowerShell 5.1 drops pytest's final "N passed" line through a pipe: judge by exit code and the dots
- `web/tests/fixtures/suggestions_applied.yaml` is written by vitest and read by pytest: run web tests first

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 160/160, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # exit 0, no skips
```
