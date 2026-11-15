# Handoff — 2026-11-15 1.3i Fix it converges; owner review and commit

## Task
Get the owner's browser check and commit of 1.3i (one commit for this card), then pick the next card.

## Open these files, in this order
- `.agents/tasks/1.3i-fix-converges.md` — measured bug, design, `## Result` (incl. the re-pinned 1.1 test)
- `sim_core/rightsize.py` — `_helps`, the rounds in `_plan`, `_merge`, `plan_fix_seeds`
- `tests/test_fix_converges.py` — the contract

## Done
- Wave 3 shipped and pushed (`2a52f12`, `e9bc526`)
- 1.3i built: single and 5-seed Fix it lands where it predicted, next plan empty (`rightsize.py`, `engine.py::attach_fix_plan_seeds`, `/simulate`, `/runs`, `run --seeds`, `FixLog.tsx`)

## Next
1. Owner: restart uvicorn (engine changed), re-try cross-region spike + ramp in 5-seed mode, commit 1.3i alone
2. Card candidate: 5-seed plan cost on 15–20 nodes (89–133 s; was 64–150 s): plan seeds in parallel processes, or cancel
3. Card candidate: cancel an abandoned run's plan; dim the diagram's sizing words while the plan verifies
4. Card candidate: 5-seed export carries the typical seed's own plan, not the run's shared one

## Do not touch
- Every `tests/test_*.py`, `web/tests/*.test.ts`, `web/tests/fixtures/` — locked contracts
- `web/src/core/lib/demo.ts` — calibrated demo numbers
- `.env`; git: no assistant names in commits or trailers

## Constraints discovered
- Commit per card, not per wave: one revertable commit each
- Restart the API after pulling: an older engine refuses `traffic.shape` and `custom_chaos` (422)
- A multi-seed run's seeds equal solo runs except for the plan keys (`suggestions`, `fix_outcome`, `cost_grade`, verified labels)
- The trim passes can hit `MAX_PASSES` without settling on 20 nodes: never loop rounds on "passes changed", only on "repair changed"
- pytest prints no summary line here: trust the exit code
- Headless Chrome reports `prefers-reduced-motion: reduce`: force `no-preference` to see pulses

## Verify with
```
& .\venv\Scripts\python.exe -m pytest -q -rs   # 610 passed, exit 0
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 318/318, clean
```
