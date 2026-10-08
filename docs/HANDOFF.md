# Handoff — 2026-10-09 1.3 shipped; waiting for the 1.3e card

## Task
No active task. Wait for `.agents/tasks/1.3e-*.md` (verified sizing labels) before editing anything.

## Open these files, in this order
- `docs/ROADMAP.md` §1.3 — "Follow-up, 1.3e" is the next item's spec
- `docs/AGENTS.md` — the map was re-synced for 1.3 (rightsize.py, fix log, timeline band)

## Done
- 1.3 shipped: verified repair-and-trim "Fix it" (`sim_core/rightsize.py`), outcome preview + change log (`web/src/core/components/FixLog.tsx`)
- 1.3c review fixes merged (pendingSuggestions, per-row Apply, Terraform name, YAML empty mapping)
- Multi-seed timeline renders the typical run plus a P95 band

## Next
1. Read the 1.3e card when it lands; take locks first
2. Make its failing tests pass; append `## Result`

## Do not touch
- `tests/test_suggestions.py`, `web/tests/suggestions.test.ts` — locked spec
- `RunStateContext.tsx` run-token guard, `web/src/core/lib/demo.ts`
- git: no commits, branches or `git add`

## Constraints discovered
- `summary["suggestions"]` is no longer the formula: `CloudSimulator.run()` attaches `plan_fix` output; the formula is `build_suggestions`, used only for flagging
- Verification re-runs call `run(suggest=False)`; never call `run()` from inside `rightsize.py`
- Vitest writes `tests/fixtures/suggestions_applied.yaml`; run web tests before pytest
- PowerShell 5.1: no `&&`, chain with `;`

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..
& .\venv\Scripts\python.exe -m pytest -q -rs   # green, no skips
```
