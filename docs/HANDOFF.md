# Handoff — 2026-11-14 dashboard layout built on `feat/dashboard-layout`; owner review is left

## Task
Get the owner's verdict on the dashboard layout, apply what they ask for, then merge the branch.

## Open these files, in this order
- `web/src/app/page.tsx` — the layout: toolbar, incident bar, tiles, topology, two columns
- `web/src/core/components/StatCards.tsx` — the six-tile strip
- `web/src/core/components/ExportMenu.tsx` — Export as a toolbar dropdown
- `web/src/components/Shell.tsx` — sticky sidebar, 1600px canvas

## Done
- 2.1 export center shipped and marked done (`docs/ROADMAP.md`)
- Simulator page as a dashboard: sticky toolbar, two independent columns (`web/src/app/page.tsx`)
- Six tiles: SLA compliance, P95 latency and failed requests added (`StatCards.tsx`)
- Export moved from its own panel into the toolbar (`ExportMenu.tsx`)
- Sticky sidebar, wider canvas (`Shell.tsx`); incident description beside its control (`IncidentPicker.tsx`)
- Topology capped at 1.3× its natural size and centred (`TopologyDiagram.tsx`)
- Chaos-window labels on alternate rows (`TimelineChart.tsx`); `shadow-pop` token (`globals.css`)

## Next
1. Owner: look at `/` at your own screen size, light and dark, and say what to change
2. Owner: commit and merge `feat/dashboard-layout`
3. Card author: collapse finding cards to rows that expand (the verdict column is the tallest thing on the page)
4. Card author: measure and prototype Wave 3 (replay from `timeseries` first, streaming after)

## Do not touch
- Every `tests/test_*.py` and `web/tests/*.test.ts` — locked contracts
- `web/src/core/lib/demo.ts` — calibrated demo numbers
- git: the executor makes no commits, branches or `git add`

## Constraints discovered
- The topology needs 832px for the demo graph: beside another panel it scrolls sideways, so it keeps a full row
- A full-page headless screenshot can leave the Recharts chart blank; a viewport capture scrolled to it shows it drawn
- The Fix-it table needs about 530px: below that its Apply buttons scroll out of view (phones)
- `.eleven-results` dims its direct children during a run; the topology wrapper is `.eleven-live` and must stay a direct child
- Tile colours come from findings (`sla_breach`, `p95_headroom`): do not add thresholds in the frontend

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..   # 179/179, no errors
& .\venv\Scripts\python.exe -m pytest -q -rs   # exit 0, no skips
```
