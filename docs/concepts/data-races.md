# Data races

> **Milestone:** K4 · **Read before:** [threads](threads.md), [gil](gil.md) · **Reading time:** ~11 min
> **Terms introduced:** **data race**, **read-modify-write**, **lost update**, **atomic operation**, **lock (mutex)**, **`threading.Barrier`**

## One-sentence summary

A **data race** happens when two threads update the same value without coordinating, so one thread's change silently overwrites the other's (a **lost update**); the GIL makes this rare in Python, the free-threaded build makes it common, and a **lock** fixes it on both.

## Why you should care

Code that was "accidentally safe" for years can start losing data the day you run it with real parallelism (research [§2.9](../research/python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver)). And thread-unsafe code doesn't need free-threading to hurt: SquadStack's Celery workers deadlocked in production because some code wasn't thread-safe (research [§2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources)). K4 plants a race in Kiln-style code (`labs/k4/race_demo.py`), measures how many updates it loses on each build, and shows the lock that fixes it. Kiln's own job counters use exactly that fix.

## The mental model

**Analogy:** two people update a shared whiteboard tally. Each reads "41", walks to the board, and writes "42". Two people did the work; the board says one.

**Where the analogy fails:** people would notice each other. Threads can't, and the window between "read" and "write" can be a few nanoseconds, so the bug appears or disappears with timing, machine load and Python build.

```
 counter.value += 1   is three steps:  READ value ─▶ ADD 1 ─▶ WRITE value

 thread A: READ 41 ─▶ ADD ─▶ 42 ─────────────▶ WRITE 42
 thread B:        READ 41 ─▶ ADD ─▶ 42 ─▶ WRITE 42
                                                     result: 42, not 43 → one LOST UPDATE

 with a lock:  A: [lock │ READ 41 │ ADD │ WRITE 42 │ unlock]
               B:                                         [lock │ READ 42 │ ADD │ WRITE 43 │ unlock]
```

## See it for yourself

[`snippets/data-races_1.py`](snippets/data-races_1.py): 8 threads each add 1 to a shared counter 100,000 times, released together by a `threading.Barrier` so they really overlap. Once without a lock (`racy`), once with one (`locked`), 5 trials each:

```text
build: GIL; expected total per trial: 800,000
racy  : lost updates in 5 trials: [0, 0, 0, 0, 0]
locked: lost updates in 5 trials: [0, 0, 0, 0, 0]
```

```text
build: free-threaded; expected total per trial: 800,000
racy  : lost updates in 5 trials: [631098, 625265, 622755, 621241, 621349]
locked: lost updates in 5 trials: [0, 0, 0, 0, 0]
```

(3.15.1, then 3.15.1t.) On the free-threaded build the racy counter **lost about 78% of its updates**, every time. The locked one is exact on both. On the GIL build, no loss at all. So is the GIL build safe? [`snippets/data-races_2.py`](snippets/data-races_2.py) runs longer (500,000 iterations per thread), and adds a variant with a **function call between the read and the write**:

```text
build: GIL; expected total per trial: 4,000,000
+= 1             : lost in 2 trials: [0, 0]
read, call, write: lost in 2 trials: [2832482, 1249533]
```

```text
build: free-threaded; expected total per trial: 4,000,000
+= 1             : lost in 2 trials: [3273306, 3271657]
read, call, write: lost in 2 trials: [3281141, 3294068]
```

On the GIL build, `+= 1` still lost nothing, but read-call-write lost **millions**. The same pattern lost updates on 3.13.17 and 3.14.9 too. With 100,000 iterations per thread it lost nothing: each thread finished inside one 5 ms time slice, so the threads never overlapped. **Short tests make racy code look safe.**

## How it really works

- **Why `+=` is several steps.** `counter.value += 1` compiles to roughly: load `counter.value`, add 1, store `counter.value`. Each is a separate bytecode instruction (`python -m dis` shows them). Between the load and the store, another thread can do its own load-add-store, and one of the two stores wins.
- **Why the GIL hides it (usually).** With the GIL, only one thread runs bytecode, and a switch happens only at **eval-breaker check points** (function entry, loop back-edges), roughly every 5 ms ([gil](gil.md)). There is no check point inside `+= 1`, so on current CPython it's never split. A function call in the middle *is* a check point. Real code is full of those: logging, metrics, `dict.get`, a property. **The GIL makes races rare, not impossible**, and "rare" is the worst kind of bug: it passes every test and fails in production.
- **Why free-threading exposes it.** On 3.15t the 8 threads really run at the same moment on 8 cores, so loads and stores interleave all the time. The counter object itself stays consistent (no crash); only the *logic* is wrong.
- **What fixes it.** A **lock (mutex)**: `with lock:` lets one thread at a time run the read-modify-write, making the three steps act as one **atomic operation**. Alternatives: give each thread its own counter and add them up at the end (no sharing), or send updates through a `queue.Queue` to one owner thread ([thread-safety-of-builtins](thread-safety-of-builtins.md)).
- **Reproducing a race.** Races depend on timing, so a demo must create overlap on purpose: a **`threading.Barrier`** makes all threads wait until the last one arrives, then releases them together; many iterations keep them overlapping for longer than a time slice; repeats count *how often* it happens.

## How Kiln uses it

- `labs/k4/race_demo.py`: the planted `RacyStats.record` (`self.count += 1` and `d[k] = d.get(k, 0) + 1`), and `LockedStats`, the fix. The K4 lab publishes the lost-update counts for 3.15 and 3.15t.
- `worker/main.py` `WorkerCounters`: the worker's `jobs_done` and in-flight counters are updated by several job threads under the `threads` executor, so they're behind a `threading.Lock`. A test runs 2,000 jobs at concurrency 8 on 3.15t and checks `jobs_done == 2000` exactly.
- `worker/channel.py`: sending a frame is also a multi-step operation (several `send` calls may be needed); the channel's lock keeps two threads' frames from interleaving byte by byte.

## Common misconceptions

- ❌ **Myth:** "Python has a GIL, so I don't need locks."
  ✅ **Reality:** the read-call-write case above lost millions of updates on the GIL build. The GIL protects CPython's internals, not your logic.
- ❌ **Myth:** "my test ran 100 times without losing anything, so it's correct."
  ✅ **Reality:** with 100,000 iterations per thread the GIL build lost nothing; with 500,000 it lost millions. Races are about timing; absence in a test proves little.
- ❌ **Myth:** "a data race crashes the program."
  ✅ **Reality:** in Python it usually just gives wrong numbers, silently. 78% of the updates vanished above and nothing complained.

## Check yourself

1. `counter += 1` from 8 threads, 1,000,000 times each, on 3.15t: what can the final value be?
   <details><summary>Answer</summary>

   Anything up to 8,000,000, and in practice far less: in the snippet's runs (100,000 per thread) only about 22% of the expected total survived. Each lost update is an increment overwritten by another thread's stale write.

   </details>
2. Why did `+= 1` lose nothing on the GIL build, while read-call-write lost millions?
   <details><summary>Answer</summary>

   On the GIL build, threads switch only at check points such as function calls and loop back-edges. `+= 1` has none between its load and its store, so it's never split. The call in read-call-write is a check point: a switch there lets other threads update the counter, and the stale write afterwards erases their work.

   </details>
3. Predict: the same `racy` code with `with lock:` around `counter.value += 1`, on 3.15t. Lost updates?
   <details><summary>Answer</summary>

   Zero, every time: the `locked` rows above. Only one thread at a time can be between the read and the write.

   </details>

## Go deeper

- [`threading.Lock`](https://docs.python.org/3.15/library/threading.html#lock-objects) and [`threading.Barrier`](https://docs.python.org/3.15/library/threading.html#barrier-objects).
- [Free-threading HOWTO: thread safety](https://docs.python.org/3.15/howto/free-threading-python.html).
- `python -m dis` on a function containing `x.value += 1`: see the separate load, add and store instructions.
- [python/locks](../python/locks.md) for the API, [thread-safety-of-builtins](thread-safety-of-builtins.md) for what *is* safe without a lock.
