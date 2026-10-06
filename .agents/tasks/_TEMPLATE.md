# Task NNN — <short name>

> Written by Claude, executed by Qwen. Copy this file to
> `.agents/tasks/NNN-name.md` and fill it in. A card without a **Verify**
> command is not a task card — do not start it.

## Goal
<One sentence. What is true when this is done that is not true now.>

## Files you may edit
- `path/to/file.py` — <what changes here>
- `path/to/other.tsx` — <what changes here>

Nothing else. If the work needs a file not on this list, stop and say so
instead of widening the change.

## Read first (and only these)
- `docs/AGENTS.md`
- `path/to/contract.py` lines N-M — <why>

## The failing test
```bash
<command that fails right now>
```
```
<the assertion that fails, pasted>
```
Your job is to make exactly this pass without weakening the assertion.
<If there is no test: say "none — this change is not testable" and explain how
it will be checked by eye instead. Prefer writing a test.>

## Constraints
- <the hard rule that is easy to break here — see docs/AGENTS.md>
- No new dependencies.
- Do not change public signatures unless this card says to.

## Out of scope
- <the adjacent thing you will be tempted to fix. Leave it. Note it instead.>

## Verify
```bash
<the exact command sequence that proves it works>
```
All of it must pass. Do not report done on a partial pass.

## When finished
1. Append a `## Result` section below: what changed, what the verify output was,
   anything you could not do.
2. If `docs/AGENTS.md` is now wrong about a file you touched, fix that line.
3. Release your rows in `.agents/LOCKS.md`.
4. If you are out of context, rewrite `docs/HANDOFF.md` first.
