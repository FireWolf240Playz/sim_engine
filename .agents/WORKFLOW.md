# The pipeline: how Claude and Qwen actually work together

## The one thing to understand

**They never talk to each other.** There is no socket, no API, no agent
protocol between them. Neither model ever sees the other's conversation.

They communicate the way two developers in different timezones do: through
**files in this repo**, asynchronously, with **Alexander as the clock**. Nothing
moves unless he moves it.

That is deliberate. At 30B, an autonomous loop with no supervisor produces
confident garbage faster than you can read it. Batched and human-gated is both
cheaper and more correct.

```
                    docs/ROADMAP.md
                          │
                          ▼
                    [ YOU pick the next item ]
                          │
                          ▼
   ┌──────────────────────────────────────────────┐
   │ CLAUDE                                       │
   │  reads: docs/AGENTS.md, the roadmap item     │
   │  writes: .agents/tasks/NNN-name.md           │
   │          + a FAILING TEST (committed)        │
   └──────────────────────────────────────────────┘
                          │
                   the card + the test
                   (this is the whole message)
                          │
                          ▼
   ┌──────────────────────────────────────────────┐
   │ QWEN  (local, free, unlimited retries)       │
   │  reads: docs/AGENTS.md, the card,            │
   │         ONLY the files the card names        │
   │  takes: a lock in .agents/LOCKS.md           │
   │  loops: edit → run verify → edit → ...       │
   │  writes: code + a ## Result on the card      │
   └──────────────────────────────────────────────┘
                          │
                      git diff
                          │
                          ▼
   ┌──────────────────────────────────────────────┐
   │ CLAUDE                                       │
   │  reads: the diff only                        │
   │  checks: hard rules, scope, contract         │
   │  writes: review notes, AGENTS.md fixes       │
   └──────────────────────────────────────────────┘
                          │
                          ▼
                 [ YOU merge, release lock ]
```

---

## The channels

Every arrow above is a file. There are seven, and each has exactly one job.

| File | Direction | Written by | Read by |
|---|---|---|---|
| `docs/AGENTS.md` | shared world model | Claude (rarely) | both, every session |
| `.agents/tasks/NNN-*.md` | Claude → Qwen | Claude | Qwen |
| **the failing test** | Claude → Qwen | Claude | the test runner |
| `.agents/LOCKS.md` | mutual exclusion | whoever claims | both, before editing |
| `## Result` on the card | Qwen → Claude | Qwen | Claude |
| `git diff` | Qwen → Claude | Qwen | Claude |
| `docs/HANDOFF.md` | Qwen → next Qwen | Qwen | Qwen |
| `.agents/HANDOFF-CLAUDE.md` | Claude → next Claude | Claude | Claude |

**The failing test is the most important channel**, because it is the only one
a model cannot misread. Prose is interpreted; an assertion either passes or it
does not. Every other channel is advisory. This one is binding.

That is why the rule is: *no verify command, no task card.*

---

## The task state machine

A task is in exactly one state, and each transition leaves an artifact. **No
artifact means the transition did not happen** — not "probably happened".

| State | Means | Artifact that proves it |
|---|---|---|
| `BACKLOG` | a line in `docs/ROADMAP.md` | the roadmap |
| `SPEC'D` | Claude has defined "correct" | `.agents/tasks/NNN-*.md` + a committed failing test |
| `CLAIMED` | Qwen is working on it | a row in `.agents/LOCKS.md` |
| `GREEN` | the verify command passes | `## Result` section with the pasted output |
| `REVIEWED` | Claude has read the diff | review notes on the card |
| `MERGED` | it is in main | the commit; lock row deleted |

Never skip `SPEC'D`. Handing Qwen a roadmap line directly is how you get
something plausible that violates a hard rule.

---

## The daily loop

It is **batched, not constant**. Roughly:

### Morning — Claude session, ~15 minutes, 2-3 calls

> Read `docs/AGENTS.md`. From `docs/ROADMAP.md` wave 1, write task cards for
> 1.3 and 1.4 using `.agents/tasks/_TEMPLATE.md`. For each, write the failing
> test first and commit it. Name only the files each task may touch.

Output: two or three cards, two or three failing tests, committed.

### Day — Qwen, hours, free

> Read `docs/AGENTS.md` and `.agents/tasks/003-right-sizing.md`. Take your
> lock. Work only the files the card names. Run the verify command until it
> passes. Append a `## Result`.

Qwen can fail twenty times. It costs nothing. Do not reach for Claude on the
first red test — that is what the free model is *for*.

### Evening — Claude session, ~10 minutes, 1-2 calls

> Read `git diff main...qwen/right-sizing`. Check it against the hard rules in
> `docs/AGENTS.md`. Flag anything out of scope. If `docs/AGENTS.md` is now
> wrong about a file in this diff, fix that line.

Then you merge and release the lock.

**Why batched beats constant:** every Claude call costs money, and every
handoff is a lossy summary. Two batches a day with real work between them beats
twenty interruptions.

---

## Routing: who gets what

**Claude owns anything where the hard part is knowing what "correct" means.
Qwen owns anything where "correct" is already written down.**

| Claude | Qwen |
|---|---|
| Contract changes (`summary()` ↔ `types.ts`) | Making a known-failing test pass |
| Anything spanning more than ~3 files | Following a pattern that already exists 3× |
| "Something broke, I don't know why" | Mechanical edits: renames, type hints, formatting |
| Writing the failing test | More tests for behaviour that already exists |
| Reviewing a diff | Building a component from a written spec |
| Designing a first-of-its-kind feature | Adding the 4th, 5th, 6th of something |
| Refreshing `docs/AGENTS.md` | |
| **You:** what to build next, and whether a trade-off is acceptable | |

### The multiplier: exemplar + test → replication

`findings.py` is the model case. Each rule is one `_`-prefixed function plus a
threshold constant at module top.

1. Claude writes **one** new finding with its constant and a pytest case that
   asserts it fires at the boundary and not below it.
2. Qwen writes the next five against that pattern, iterating locally until
   `pytest tests/test_findings.py` is green.

One paid call unlocks hours of free local work. The same shape applies to
presets, chaos injectors, export formats and test coverage — anywhere the
codebase already has three examples of a thing.

It does **not** apply to anything first-of-its-kind. SSE streaming has no
exemplar, so Qwen has nothing to copy and will invent something that breaks a
hard rule.

---

## Failure modes, and what catches each

| Failure | What catches it |
|---|---|
| Qwen reports done but isn't | The verify command. Trust it, never the summary — a 30B writes optimistic prose. |
| Both edit the same file | `.agents/LOCKS.md` + `git worktree` |
| Qwen quietly widens the change | The card's "Files you may edit" list |
| Qwen runs out of context mid-task | `docs/HANDOFF.md`, rewritten before it dies |
| `docs/AGENTS.md` goes stale | "If the map is wrong about a file you touched, fix that line before you finish" |
| Claude's spec was wrong | The failing test is wrong too — caught at review, cheaply |
| Contract drift between engine and FE | `web/src/core/types.ts` belongs to neither model; route it through Claude with the engine change in the same card |

That last row is not hypothetical. `sim_core/findings.py` grew five optional
fields that `types.ts` never learned about, so they arrive in the JSON and are
dropped silently. No error, nothing rendered. That is what two agents agreeing
separately looks like.

---

## Concurrency

Use worktrees so the two cannot share a working directory:

```bash
git worktree add ../eleven-qwen -b qwen/right-sizing
```

Qwen works in `../eleven-qwen`. Claude reviews `git diff main...qwen/...` from
the main checkout. Neither can stomp the other's files even if a lock is
forgotten.

---

## Rules that keep it honest

1. **The repo is the only memory.** Nothing said in a chat reaches the other
   model. If it matters, it goes in a file.
2. **Verify, don't trust.** The command is the judge.
3. **Don't pay Claude to type.** If the task is "add three more of the thing
   that already exists", that is Qwen's, with a Claude-written test.
4. **Don't let Qwen design.** If there is no exemplar, it is Claude's.
5. **One lock per path, released when done.** A stale lock is worse than none.
