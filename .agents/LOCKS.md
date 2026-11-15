# Path ownership — read this before editing anything

Both models read this file first and **refuse to edit a path they do not own**.
Update it when you take or release a path. Stale locks are worse than none, so
clear yours when you finish.

Format: one row per active claim. `Since` is a date, not a guess.

| Path | Owner | Since | Task |
|---|---|---|---|
| `tests/test_suggestions.py` | claude | 2026-10-08 | 1.3 acceptance contract — qwen makes it pass, never edits it |
| `web/tests/suggestions.test.ts` | claude | 2026-10-08 | 1.3 FE acceptance contract — qwen makes it pass, never edits it |
| `.agents/tasks/1.3-right-sizing.md`, `1.3b-fix-diff-frontend.md`, `1.3c-review-fixes.md` | claude | 2026-10-08 | 1.3 task cards (qwen appends `## Result` only) |
| `web/tests/topologyView.test.ts`, `graphLayout.test.ts`, `runResult.test.ts`, `verdictView.test.ts`, `rerun.test.ts` | claude | 2026-10-09 | 1.3e–1.3g acceptance contracts — qwen makes them pass, never edits them |
| `.agents/tasks/1.3e-topology-memo.md`, `1.3f-simplify-render.md`, `1.3g-rerun-feedback.md` | claude | 2026-10-09 | render task cards (qwen appends `## Result` only) |
| `web/tests/nodeInspector.test.ts`, `.agents/tasks/1.4-node-inspector.md` | claude | 2026-10-10 | 1.4 contract + card — after 1.3h; qwen makes it pass, appends `## Result` only |
| `tests/test_verified_sizing.py`, `web/tests/verifiedSizing.test.ts`, `web/tests/sizingTable.test.ts`, `.agents/tasks/1.3h-verified-labels.md` | claude | 2026-10-10 | 1.3h contracts + card — qwen makes them pass, appends `## Result` only |
| `tests/test_exports.py`, `tests/test_api_export.py`, `tests/test_export_cli.py`, `.agents/tasks/2.1-export-engine.md`, `2.1b-export-api-cli.md` | claude | 2026-11-14 | 2.1 / 2.1b contracts + cards — done, both carry `## Result`; locked contracts from here on |
| `web/tests/exports.test.ts`, `web/tests/exportClient.test.ts`, `.agents/tasks/2.1c-export-menu.md`, `web/src/core/types.ts` | claude | 2026-11-14 | 2.1c contracts + card — done, the card carries `## Result`; locked contracts from here on |
| `tests/test_event_sink.py`, `tests/test_feed.py`, `tests/test_feed_outage.py`, `tests/test_api_runs.py`, `tests/test_custom_chaos.py`, `tests/test_traffic_shapes.py`, `web/tests/runStream.test.ts`, `web/tests/runClient.test.ts`, `web/tests/playback.test.ts`, `web/tests/liveView.test.ts`, `web/tests/replay.test.ts`, `web/tests/clickToFail.test.ts`, `web/tests/trafficShape.test.ts`, `web/tests/fixtures/runStream.ts`, `.agents/tasks/3.*.md` | claude | 2026-11-15 | Wave 3 contracts + cards — all eight built, each card carries `## Result`; locked contracts from here on |

> **Claude, 2026-10-08:** the `api/` row is superseded. A top-level key
> breaks `tests/test_profile.py`. `suggestions` goes inside `summary()` (via
> `sim_core/metrics.py`, authorized). 1.3c reverts `api/routes/simulate.py`
> + `api/schemas.py` to HEAD (comment-only edits dropped) — no lock needed.

## Unowned but shared — ask before touching

| Path | Why |
|---|---|
| `web/src/core/types.ts` | Hand-written mirror of `summary()`. Changing it is a contract decision: route through Claude with the engine change in the same task card. |
| `sim_core/metrics.py` | `summary()` and `timeseries()` are the contract every consumer reads. A field change ripples to `score.py`, `findings.py`, `profile.py`, `compare.py`, `viz.py`, `cli/sim.py`, the API and the frontend. |
| `web/src/core/lib/demo.ts` | Grid-searched demo topology. Its numbers are calibrated so the clean run scores 98 and each incident visibly degrades it. Changing them invalidates every documented score. |
| `docs/AGENTS.md` | The map. Fix a line when you find it wrong; do not restructure it mid-task. |
| `.gitignore` | Run output is ignored only at the repo root (`/*.json`, `/*.png`); keep the leading slash or `web/package.json` disappears again. |

## Never

| Path | Why |
|---|---|
| `.env` | GCP pricing credentials. Never read, echo, or commit. |
| `venv/`, `node_modules/`, `web/.next/`, `__pycache__/` | Not source. |
| Root `*.json`, `*.png` | Generated run output. |

---

**Releasing a lock:** delete the row. **Taking one:** add a row before your
first edit, not after. If a path you need is held, say so and stop — do not
edit around the lock.
