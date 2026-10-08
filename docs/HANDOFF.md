# Handoff — 2026-10-08 1.3 review fixes

## Task
Make the 5 failing tests in `web/tests/suggestions.test.ts` pass and do the cleanup items in `.agents/tasks/1.3c-review-fixes.md`; nothing else.

## Open these files, in this order
- `.agents/tasks/1.3c-review-fixes.md` — the card: rules, failing tests, allowed files
- `web/tests/suggestions.test.ts` — the "Review round 2" block at the bottom is the spec
- `web/src/core/lib/suggestions.ts` — add `pendingSuggestions`; fix the Terraform map and `emitSequence`
- `web/src/core/components/FixDiffPanel.tsx` — render pending rows, add a per-row Apply
- `web/src/app/page.tsx` — only the panel's show/hide condition

## Done
- 1.3 attempt 1 built and green; snapshot on branch `qwen/1.3-attempt-1` (`37b2d54`)
- Docs re-synced by Claude: `docs/ROADMAP.md` §1.3 is "in review", `docs/AGENTS.md` lists the 1.3 files

## Next
1. In `.agents/LOCKS.md`, delete every `qwen` and `bionic` row; add rows for the files you will edit
2. Make the 5 tests pass
3. Cleanup items 1–2 on the card
4. Run Verify; append `## Result` to the card with the output tail; release locks

## Do not touch
- `tests/test_suggestions.py`, `web/tests/suggestions.test.ts`, `.agents/tasks/*.md` (append `## Result` only)
- `docs/ROADMAP.md`, `docs/AGENTS.md` — Claude marks ✅ after review
- `RunStateContext.tsx` run-token guard, `web/src/core/lib/demo.ts`, every backend file
- git: no commits, branches or `git add`

## Constraints discovered
- `tests/fixtures/suggestions_applied.yaml` is written by Vitest and loaded by pytest: run the web tests before pytest
- Windows console can't encode `→` (cp1252); `cli/__init__.py` forces UTF-8; keep the arrow
- A stale uvicorn silently serves old `summary()` keys; restart after engine changes
- Design tokens only from `globals.css`; no synchronous `setState` in effects
- PowerShell 5.1: no `&&`, chain with `;`
- Servers: :8000/:3000 are yours; :8001/:3001 are Claude's timeline preview, leave them

## Verify with
```
cd web; npm run test:unit; npm run typecheck; npm run lint; cd ..
& .\venv\Scripts\python.exe -m pytest -q -rs   # green, no skips
```
