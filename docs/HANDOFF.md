# Handoff — 2026-10-06 verdict card enrichment

> This file is the live work order. Overwrite it when your context runs out.
> Format and rules are at the bottom of `AGENTS.md`. Read that first.

## Task
Surface the richer `Finding` fields (`title`, `why`, `impact`, `evidence`,
`recommendation`) in the frontend verdict card, so the score explains itself
without the reader opening the CLI.

## Open these files, in this order
- `sim_core/findings.py` — the `Finding` TypedDict is the contract; read the
  field list and one rule function, not the whole 20 KB file
- `web/src/core/types.ts` — the FE mirror, currently stale (see Blocking)
- `web/src/core/components/VerdictPanel.tsx` — the card being changed
- `tests/test_findings.py` — add a case here for any new field

## Done
- Engine side: `Finding` extended with the optional fields; `verdict_headline()`
  added (`sim_core/findings.py`)
- `score.py` gained `score_band()` and `score_explanation()` — a point-by-point
  decomposition of the score, available to the card but not yet rendered

## Next
1. Extend the `Finding` interface in `web/src/core/types.ts` to match
   `findings.py` — all five new fields optional, so single-line findings still
   render unchanged.
2. Render them in `VerdictPanel.tsx`: `title` as the line heading, `text` as
   the body, `evidence` as chips, `recommendation` as the closing line. Keep
   the existing shape when the optional fields are absent.
3. Add a pytest case asserting a crit finding carries `evidence` and
   `recommendation`, so the contract is pinned on the engine side.

## Do not touch
- `web/src/core/lib/*` and `web/tests/*` — recently refactored; the design
  tokens now live only in `globals.css` and components must not hold hex values
- `web/src/core/state/RunStateContext.tsx` — the run-race guard is load-bearing

## Constraints discovered
- Severity tokens differ across the boundary: the engine emits
  `info | warn | crit`, the frontend severity system is `ok | warn | crit`.
  `VerdictPanel` maps `info -> ok`. Keep that mapping in one place.
- `findings.py` thresholds (`_UTILIZATION_CRIT`, `_SLA_FLOOR`,
  `_RETRY_STORM_RATIO`, `_P95_HEADROOM_RATIO`) are module constants. The
  frontend mirrors the *score* bands in `web/src/core/lib/format.ts`
  (`scoreSeverity`) — if a band moves in `score.py`, that file moves too or the
  badge disagrees with the report PNG.
- Findings are pure functions over the `summary()` dict. Do not re-run the
  simulation or reach for live SimPy state to compute one.
- `web/src/core/lib/demo.ts` holds a grid-searched demo topology. Its numbers
  are calibrated so the clean run scores 98 and each incident visibly degrades
  it. Changing them invalidates the documented scores.

## Blocking / needs a decision
- `web/src/core/types.ts` is behind `findings.py`. Until it is updated, the new
  fields are dropped silently by the UI — no error, just nothing rendered.

## Verify with
```bash
pytest tests/test_findings.py -q
cd web && npm run test:unit && npm run typecheck
```
