# How Eleven gets built: one human, two models

There is no orchestrator process. **Alexander is the orchestrator.** The two
models are asymmetric, and the split is by *kind of thinking*, not by code area.

| | Claude | Qwen3-30B (local) |
|---|---|---|
| Context | Wide — holds the whole repo | 32k per slot |
| Cost | Metered. Every call is real money | Free. Run it a hundred times |
| Speed | Slower round trip | ~25 tok/s, always on |
| Strong at | Cross-file reasoning, architecture, review, finding the bug you can't see | Executing a well-specified, single-file change |
| Weak at | Being cheap | Holding a contract in its head across files |

The rule that follows: **Claude decides and reviews, Qwen executes.** Context
is Claude's scarce-but-large resource and Qwen's hard limit, so Claude's job is
to compress the repo into a task card small enough for Qwen to act on.

---

## Routing

**Send to Claude**

- Anything touching more than ~3 files
- A contract change — `MetricsCollector.summary()` ↔ `web/src/core/types.ts`
- "Something is wrong and I don't know why"
- Reviewing a day of Qwen's diffs
- Writing the failing test that *specifies* the next task
- Refactors with a theme (de-duplicate tokens, extract a module)
- Anything needing current external facts (library APIs, versions, docs)
- Re-generating `docs/AGENTS.md` when it has drifted

**Send to Qwen**

- A task card that names its files and its verify command
- Making a known-failing test pass
- Mechanical work: renames, moves, formatting, adding type hints
- Writing more tests for behaviour that already exists
- Building a component from a spec that already exists
- Anything you would happily re-run five times until it is right

**Send to neither — decide yourself**

- What to build next. `docs/ROADMAP.md` is yours.
- Whether a trade-off is acceptable.

---

## The loop

1. **You** pick the next item from `docs/ROADMAP.md`.
2. **Claude** writes `.agents/tasks/NNN-name.md` from the template. Where the
   task has a testable outcome, Claude also writes the *failing test* and
   commits it. This is the highest-value thing Claude does: it turns
   "understand the architecture" into "make this assertion true", which is a
   job a 30B model is genuinely good at — and it is verifiable without Claude.
3. **Qwen** reads `docs/AGENTS.md`, the task card, and only the files the card
   names. It works on its own branch or worktree.
4. **Qwen** runs the card's verify command until it passes. No verify command,
   no task card.
5. **Claude** reviews `git diff`. A diff is cheap to read and is where the hard
   rules get caught — a Pydantic model with `.capacity` called on it, a chaos
   event that edits a metric instead of live state, a hex colour in a component.
6. **You** merge. Update `.agents/LOCKS.md` and `docs/HANDOFF.md`.

Steps 2 and 5 are where the money goes, and they are worth it. Step 3 is free,
so let Qwen fail and retry rather than reaching for Claude on the first error.

---

## Ownership

`.agents/LOCKS.md` is the live record of who owns which paths. Both models read
it before editing and refuse to touch a path they do not own. Use
`git worktree` for genuinely concurrent work so the two cannot share a working
directory:

```bash
git worktree add ../eleven-qwen -b qwen/verdict-card
```

The one file that belongs to **neither** model by default is
`web/src/core/types.ts`. It mirrors the engine's `summary()` dict by hand, so
changing it is a contract decision. Route it through Claude, with the engine
change in the same task card. Both sides silently disagreeing about that file
is how `findings.py` ended up five fields ahead of the frontend.

---

## Keeping it honest

- **The map rots.** `findings.py` went from 8 KB to 21 KB in a day. Add to
  every task card: *if `docs/AGENTS.md` is wrong about a file you touched, fix
  that line before you finish.*
- **Qwen's summaries are optimistic.** A 30B model reports work it did not
  finish. Trust the verify command, not the summary. That is why every card has
  one.
- **Don't let Claude write code Qwen could write.** If the task is "add three
  more findings following the existing pattern", that is a Qwen job with a
  Claude-written test. Using Claude there is paying for typing.
