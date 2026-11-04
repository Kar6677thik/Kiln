# fork() and Threads: Why the Master Has Exactly One Thread

> **Milestone:** K1 · **Read before:** [fork](fork.md) · **Reading time:** ~10 min
> **Terms introduced:** **thread**, **lock**, **deadlock**, **fork-safety**

## One-sentence summary

`fork()` copies only the thread that called it, so any lock another thread was holding at that moment is copied in its *locked* state, and nobody in the child will ever unlock it.

## Why you should care

This is the reason Python 3.14 stopped using `fork` as `multiprocessing`'s default on Linux (research §2.9, Kiln K2), and the reason for Kiln's strictest rule: **the master never starts a thread** ([ADR-006](../decisions.md#adr-006)). The bug is nasty because it's random. It only happens if a fork lands at the exact moment another thread holds a lock, for example while that thread is writing a log line. The program hangs once in a thousand restarts, and nobody can reproduce it.

## The mental model

A **thread** is a separate line of execution inside one process: several threads share the same memory and run "at the same time". A **lock** is how threads take turns: whoever holds it may touch a shared thing, and everyone else waits until it's released. A **deadlock** is waiting forever for something that will never happen.

**Analogy:** an office with two workers and one shared notebook with a key. Worker B takes the key and starts writing. At that instant the office is photocopied, but the photocopy contains only Worker A. The copied office has the notebook, locked, and no Worker B to bring back the key. When Worker A needs the notebook, A waits forever.

**Where the analogy fails:** the "office" isn't copied gradually; the kernel copies the whole process in one go, mid-instruction for every other thread. Those threads simply don't exist in the child: they don't get a chance to finish what they were doing.

```
 parent before fork            parent after fork           child after fork
 ┌──────────────────┐          ┌──────────────────┐        ┌──────────────────┐
 │ thread A (main)  │──fork──▶ │ thread A         │        │ thread A         │
 │ thread B: holds  │          │ thread B: holds, │        │  (no thread B!)  │
 │   the log lock   │          │   then releases  │        │ log lock: LOCKED │
 └──────────────────┘          └──────────────────┘        │ forever ────────▶│ A logs → hangs
                                                           └──────────────────┘
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/fork-and-threads_1.py
```

```text
child:  threads in me = 1, lock acquired? False
parent: lock acquired after the holder released it? True
```

A helper thread took a lock and kept it; then the main thread forked. The child has **1** thread (the helper wasn't copied), and its attempt to take the lock gave up after the 0.5 s timeout. Without a timeout, the child would have hung forever. In the parent the helper thread still exists, releases the lock, and the parent gets it.

Python itself warns about this (gate G1.8):

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/fork_with_threads.py
```

```text
threads alive at fork time: 2
DeprecationWarning: This process (pid=N) is multi-threaded, use of fork() may lead to deadlocks in the child.
```

This warning was added in Python 3.12. The snippet shows the same text on 3.13, 3.15 and 3.15t (the pid is replaced by `N` so the output is stable). Kiln's integration tests turn this warning into an error ([code-style §7](../standards/code-style.md#7-fork-safety-rules)), so a stray thread in the master fails the build.

## How it really works

- POSIX (the standard Linux follows) says the child of a multi-threaded process gets a copy of the calling thread only, and may only safely call "async-signal-safe" functions until it calls `exec`. Python code does far more than that.
- The dangerous locks are often invisible: the `logging` module's handler locks, the lock inside `sys.stdout`'s buffered writer, the C library's `malloc` lock, and locks inside imported libraries. You can't audit them all.
- CPython reduces the damage with `os.register_at_fork` hooks. For example it reinitialises some of its own internal locks in the child. But it can't do this for locks owned by arbitrary code, which is why it warns.
- **Fork-safety** is the property "this code works correctly in a child created by `fork()`". The cheapest way to be fork-safe is to have no other threads when you fork.

## How Kiln uses it

- **The master is single-threaded** by design ([ADR-006](../decisions.md#adr-006)): one `selectors` loop, no threads, no thread pools. The arbiter checks `threading.active_count() == 1` right before every fork in [`launch/fork.py`](../../src/kiln/launch/fork.py) and fails loudly otherwise.
- **Workers may start threads**, but only *after* they were forked. The heartbeat thread in [`worker/heartbeat.py`](../../src/kiln/worker/heartbeat.py) is created inside `run_worker` ([`worker/main.py`](../../src/kiln/worker/main.py)). Workers never fork, so their threads can't cause this problem.
- The master never imports third-party libraries ([ADR-005](../decisions.md#adr-005)), because some start threads on import.

## Common misconceptions

- ❌ **Myth:** "If my code doesn't use locks, forking with threads is safe."
  ✅ **Reality:** the locks you don't see are the problem: `logging`, buffered I/O, `malloc`, libraries. You inherit their state too.
- ❌ **Myth:** "The other threads keep running in the child."
  ✅ **Reality:** the child has exactly one thread. The others vanish mid-step, leaving whatever they held locked.
- ❌ **Myth:** "The DeprecationWarning means fork itself is going away."
  ✅ **Reality:** `os.fork()` stays. The warning says that forking *a multi-threaded process* is risky. Forking a single-threaded process, as Kiln's master does, is fine.

## Check yourself

1. Why does the master forbid threads, but workers may start a heartbeat thread?
   <details><summary>Answer</summary>

   The master calls `fork()`, so any lock held by another master thread could be copied locked into a worker and deadlock it. Workers never fork, so their threads can't leave copied locks anywhere.

   </details>
2. A program forks while a background thread is in the middle of `logging.info(...)`. What can happen when the child logs its first line?
   <details><summary>Answer</summary>

   It can hang forever. The handler's lock was copied while held, and the thread that would release it doesn't exist in the child.

   </details>
3. In the snippet, why did the child's `lock.acquire` return `False` instead of hanging?
   <details><summary>Answer</summary>

   It was called with `timeout=0.5`, so it gave up after half a second. A plain `lock.acquire()` with no timeout would have waited forever, which is the deadlock.

   </details>

## Go deeper

- `man 3p fork` (the POSIX text): "a process shall be created with a single thread".
- [Python docs: `os.fork`](https://docs.python.org/3/library/os.html#os.fork), the note about the 3.12 DeprecationWarning.
- [What's New in Python 3.14](https://docs.python.org/3/whatsnew/3.14.html): why the Linux default start method became forkserver.
