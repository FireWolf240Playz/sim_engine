# Handoff — 2026-11-14 dashboard balance built on `feat/dashboard-balance`; owner review is left

## Task
Get the owner's verdict on the balanced dashboard, then merge `feat/dashboard-layout` and `feat/dashboard-balance` into `main`.

## Open these files, in this order
- `web/src/app/page.tsx` — the rows: verdict | Fix it, timeline (+ confidence), sizing
- `web/src/core/components/FindingCard.tsx` — collapsed row mode (`onToggle`)
- `web/src/core/components/FixLog.tsx` — `FixHistoryList`'s `limit`

## Done
- Rows instead of independent columns: no hole between cards (`web/src/app/page.tsx`)
- Findings collapse to one-line rows; the modal and inspector keep full cards (`FindingCard.tsx`, `VerdictPanel.tsx`)
- Change log shows the latest two, with "show N earlier changes" (`FixLog.tsx`)
- `Panel` takes `className` so a card can fill its row (`web/src/components/Panel.tsx`)

## Next
1. Owner: look at `/` after a few incident runs and applies; say what to change, then commit and merge
2. Card author: verdict at 1280 is 407px wide; consider a container query that stacks the gauge over the headline
3. Card author: measure and prototype Wave 3 (replay from `timeseries` first, streaming after)

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- `web/src/core/lib/demo.ts` — calibrated demo numbers
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- Most blank space inside any card, measured: 38px (was a 356px hole); opening a finding grows the verdict, and its neighbour stretches to match
- `.eleven-results` dims its direct children during a run; the topology wrapper is `.eleven-live` and must stay a direct child
- A full-page headless screenshot can leave the Recharts chart blank; a viewport capture scrolled to it shows it drawn
- Tile colours come from findings (`sla_breach`, `p95_headroom`): do not add thresholds in the frontend

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 179/179, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # exit 0, no skips
```
