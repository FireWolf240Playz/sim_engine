# Handoff — 2026-11-15 Wave 3 shipped; pick the next card

## Task
Choose and card the next work: a Wave 3 follow-up from the review notes, or Wave 4.

## Open these files, in this order
- `docs/ROADMAP.md` — Wave 4 section (accounts, history, share)
- `api/runs.py` — the in-memory run store Wave 4's `runs` table replaces
- `web/src/core/state/RunStateContext.tsx` — the one place a run starts

## Done
- Wave 3, all eight cards, committed and pushed; roadmap marks 3.1–3.5 ✅
- Settings-changed hint: the page says when the incident, traffic or injected failures differ from the run on screen (`web/src/app/page.tsx`)

## Next
1. Card candidate: cancel an abandoned run's plan (run-again doubles the wait; `/runs` has no cancel)
2. Card candidate: dim the diagram's sizing words while the plan verifies
3. Then card Wave 4 from the roadmap

## Do not touch
- Every `tests/test_*.py`, `web/tests/*.test.ts`, `web/tests/fixtures/` — locked contracts
- `web/src/core/lib/demo.ts` — calibrated demo numbers
- `.env`; git: no assistant names in commits or trailers

## Constraints discovered
- Restart the API after pulling: an older engine refuses `traffic.shape` and `custom_chaos` (422)
- Headless Chrome reports `prefers-reduced-motion: reduce`: force `no-preference` to see pulses
- `topologyView.ts` may export only `nodeViews` (locked test); live helpers live in `liveView.ts`
- The feed sits beside the diagram only at `2xl`: the diagram never shrinks
- Recorded `requests` exclude what is still in flight at the horizon; count arrivals as `requests + unfinished − overdue`
- pytest prints no summary line here: trust the exit code (583 dots, exit 0)

## Verify with
```
& .\venv\Scripts\python.exe -m pytest -q -rs   # 583 passed, exit 0
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 314/314, clean
```
