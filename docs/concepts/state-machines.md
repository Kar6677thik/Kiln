# State Machines

> **Milestone:** K1 · **Read before:** [enum](../python/enum.md), [dataclasses](../python/dataclasses.md) · **Reading time:** ~12 min
> **Terms introduced:** **state machine**, **state**, **transition**, **transition table**, **terminal state**, **invariant**

## One-sentence summary

A **state machine** describes something that is always in exactly one of a fixed set of **states**, and may only move between states along a fixed list of allowed **transitions**.

## Why you should care

Kiln has two important state machines: the **job** (QUEUED → RUNNING → SUCCEEDED/FAILED/…, [spec/03 §4](../spec/03-jobs-and-tasks.md#4-the-job-state-machine)) and the **worker** (BOOTING → READY → RETIRING → EXITED, [spec/04 §1](../spec/04-supervisor.md#1-worker-states)).

Most serious bugs in job systems are illegal transitions:
- a job that finished gets "finished" again by a late result;
- a cancelled job still runs;
- a lost job is retried more times than allowed.

These are exactly the bugs that would break Kiln's delivery promises ([delivery-semantics](delivery-semantics.md)). Writing the allowed transitions down *as data*, and refusing anything else, turns those silent bugs into loud errors.

## The mental model

**Analogy:** a board game. Your piece is on exactly one square, and the rules say which squares you may move to from each square. You can't jump from "Start" to "Finish", and once you reach "Finish" the game is over.

**Where the analogy fails:** in a game you choose your moves. In Kiln, *events* force them (a result arrives, a timer fires, a worker dies), and the same event may lead to different states depending on conditions, such as "retry if attempts remain, otherwise LOST".

```
        submit                 dispatched               result ok
  (none) ──────▶  QUEUED  ─────────────────▶  RUNNING ───────────────▶  SUCCEEDED  (terminal)
                    ▲  │ cancel                 │ │ worker lost / timeout
                    │  ▼                        │ │   + attempts remain
                 CANCELLED (terminal)           │ └─────────────────────┐
                    ▲                           │                       │ (back to QUEUED)
                    └── retry ──────────────────┼───────────────────────┘
                                                └──▶ FAILED / TIMED_OUT / LOST  (terminal)
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/state-machines_1.py
```

```text
OFF -> ON  (switch on)
ON -> OFF  (switch off)
OFF -> ON  (switch on)
ON -> BROKEN  (bulb burns out)
terminal? True
refused: illegal transition BROKEN -> ON
```

The snippet models a light bulb with three states. The important parts:
- `ALLOWED` is a **transition table**: a dict whose keys are `(from, to)` pairs and whose values say *why* that move happens. The whole design fits on one screen.
- `transition()` is the **only** function that changes the state, and it checks the table first.
- Once the bulb is `BROKEN`, a **terminal state**, trying to switch it on is *refused*, loudly, instead of silently "fixing" a broken bulb.

## How it really works

There's no magic here. It's a discipline with three rules:
1. **States are an enum**, never loose strings. A typo becomes an `AttributeError`, not a silently wrong state ([enum](../python/enum.md)).
2. **One transition function.** Every state change in the program goes through it. Then the table is guaranteed to be the whole truth. A `grep` for `.state =` should find exactly one place.
3. **Invariants are checked.** An **invariant** is a statement that must *always* be true, for example:
   - "a terminal job never changes state again";
   - "the queue contains exactly the QUEUED jobs";
   - "a RUNNING job has exactly one open attempt".

   Kiln writes these as `assert` statements with `# INVARIANT:` comments, and tests them heavily.

The best test for a state machine is **property-based**. Hypothesis's `RuleBasedStateMachine` fires *random sequences* of events at the real `JobTable` (submit, dispatch, result, timeout, worker lost, cancel, a stale result...) and checks every invariant after every step. Random sequences reach orderings no human would think to write, such as "a timeout arrives, then the late result for the *same* attempt". That's where bugs hide (K1 spec §6.1).

## How Kiln uses it

- [`master/jobs.py` → `JobState`, `_ALLOWED`, `_transition`](../../src/kiln/master/jobs.py): `_ALLOWED` is a literal copy of the spec/03 §4.1 table, and `_transition(job, to, why)` asserts the pair is allowed before changing anything. `JobTable` methods (`start`, `succeed`, `fail_attempt`, `cancel`...) are the *events*. Each decides the target state and calls `_transition`.
- [`master/jobs.py` → `JobTable.succeed`](../../src/kiln/master/jobs.py) returns `None` for a stale result: an old attempt number, or a job that is already terminal. This is the "late result" rule of spec/03 §4.1.
- The worker state machine lives in `master/workers.py` (spec/04 §1).

## Common misconceptions

- ❌ **Myth:** "a state machine needs a special library."
  ✅ **Reality:** an enum, a dict and one function are enough. Libraries add features (diagrams, hooks), but the discipline is what matters.
- ❌ **Myth:** "if every handler sets the right state, a table is redundant."
  ✅ **Reality:** the table catches the cases nobody thought about, such as two events for the same job arriving in an unexpected order. Without it, the second event silently overwrites the first.
- ❌ **Myth:** "refusing an illegal transition with an error makes the system fragile."
  ✅ **Reality:** the alternative is *silent corruption*, like a SUCCEEDED job flipping to LOST. A loud error in a test is far cheaper than a wrong answer in production. For *expected* odd events, such as a late result, the rule is written down and handled (ignored and logged), not crashed on.

## Check yourself

1. A job is SUCCEEDED, and then a `result` for its first attempt arrives again. What should happen?
   <details><summary>Answer</summary>

   Nothing changes. SUCCEEDED is terminal, so the late result is ignored and logged at WARNING ([spec/03 §4.1](../spec/03-jobs-and-tasks.md#41-transition-table-every-allowed-transition-anything-else-is-a-bug-and-raises-assertionerror)).

   </details>
2. Why keep the transition table as *data* (a dict) rather than as `if` statements spread over several methods?
   <details><summary>Answer</summary>

   One table can be read in one glance, compared line by line with the spec, and checked in a single place. Scattered `if`s can disagree with each other, and nobody can see the whole design at once.

   </details>
3. Predict: in the snippet, what does `transition(Light.OFF, Light.BROKEN)` do?
   <details><summary>Answer</summary>

   It raises `AssertionError: illegal transition OFF -> BROKEN`. In this model a bulb only burns out while it's ON, so `(OFF, BROKEN)` isn't in the table.

   </details>
4. Name one invariant of Kiln's job table.
   <details><summary>Answer</summary>

   Any of these:
   - a terminal job never changes state;
   - the queue holds exactly the QUEUED jobs, in FIFO order;
   - `len(attempts) ≤ max_attempts`;
   - each RUNNING job has exactly one open attempt;
   - the stats counts match the jobs.

   </details>

## Go deeper

- [Hypothesis: stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html): the `RuleBasedStateMachine` used in K1.
- [spec/03 §4](../spec/03-jobs-and-tasks.md#4-the-job-state-machine): Kiln's full job table.
- "Finite-state machine" on Wikipedia: the formal background, if you're curious.
