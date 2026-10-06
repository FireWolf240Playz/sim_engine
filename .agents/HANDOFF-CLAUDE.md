# Handoff → Claude Code — updated 2026-10-07

For the next **Claude** session. Qwen's live work order is `docs/HANDOFF.md`, a
different document; do not overwrite it while Qwen holds locks.

**Read order:** this file → `docs/AGENTS.md` (repo map) → `.agents/LOCKS.md`.

---

## State (verified 2026-10-07 on the real machine)

The 2026-10-06 Cowork handoff was executed. Its web correctness pass is
`333bafe`; follow-ups are `d39e83c` (repo hygiene, Python 3.14) and `bb3e1ec`
(CLI fix).

| Check | Result |
|---|---|
| `pytest` | 180 passed |
| `npm run test:unit` / `typecheck` / `build` | 62 passed / clean / clean (real `next/font/google`) |
| `npm run lint` | 2 errors, both in Qwen's uncommitted verdict files (below) |
| `ruff check .` | ~525 pre-existing findings; see `docs/AGENTS.md` → Known drift |
| `mypy sim_core` | not installed in `venv/` (it is in `[dev]` extras) |

## Qwen's in-flight work (uncommitted, locked)

Roadmap 1.2 rich verdict card. The engine ↔ `types.ts` contract drift is
**closed**: `Finding` has all five optional fields, and `Summary` carries
`score_explanation` and `verdict_headline`. Before it can be committed:

1. Fix `react-hooks/set-state-in-effect` in `ScoreGauge.tsx:32` and
   `VerdictReportModal.tsx:74`.
2. `npm run lint` clean, plus the `docs/HANDOFF.md` verify command.
3. Claude reviews the diff (engine + `types.ts` + components together, since
   it is a contract change), then commit and clear Qwen's rows in `LOCKS.md`.

## Open items, highest value first

1. **Roadmap 1.3: right-sizing suggestions.** This is next after 1.2. The
   engine already computes `component_sizing[node].recommended_capacity`. Write
   the task card plus a failing `tests/test_suggestions.py` for Qwen.
2. **Roadmap 1.4: node inspector.** Pure frontend. `FindingCard` already
   reserves the link slot for it.
3. **`ruff --fix` pass.** ~410 auto-fixable findings. Mechanical work for Qwen,
   but it touches every engine file, so only do it when no locks are held.
4. **Install mypy** (`pip install -e .[dev]`) and measure `--strict`.
5. **Unused data already fetched:** `per_component` per tick, `runs[]`,
   `report_png_b64`, `traffic.spike_rps`. These feed roadmap 1.4 and 3.4.
6. Everything is `"use client"`; `tsconfig` targets ES2017. Low priority.
7. A stray `C:\Users\ayord\Desktop\package-lock.json`, outside the repo,
   makes `next build` warn about the workspace root. Delete it, or set
   `turbopack.root`.

## How to work here

`.agents/WORKFLOW.md`: Claude decides and reviews, Qwen executes. The most
useful thing to produce is a task card plus a failing test. Stage explicit
paths only, never `git add -A`: Qwen's work sits uncommitted in the same tree.
On Windows PowerShell 5.1, pass commit messages with `git commit -F <file>`;
here-strings piped to `-F -` arrive empty.
