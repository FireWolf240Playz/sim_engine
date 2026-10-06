# Path ownership — read this before editing anything

Both models read this file first and **refuse to edit a path they do not own**.
Update it when you take or release a path. Stale locks are worse than none, so
clear yours when you finish.

Format: one row per active claim. `Since` is a date, not a guess.

| Path | Owner | Since | Task |
|---|---|---|---|
| `sim_core/findings.py` | qwen | 2026-10-06 | richer `Finding` fields |
| `sim_core/score.py` | qwen | 2026-10-06 | `score_band` / `score_explanation` |
| `web/src/core/components/VerdictPanel.tsx` | qwen | 2026-10-06 | verdict card enrichment |
| `web/src/core/components/StatCards.tsx` | qwen | 2026-10-06 | score display |
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
