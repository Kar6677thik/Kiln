# Locks (threading.Lock, RLock, `with lock:`)

> **First used in:** see the [ledger](README.md) (milestone K4) · **Needs:** [threading](threading.md), [context-managers](context-managers.md) · **Reading time:** ~7 min
> **Terms introduced:** **`threading.Lock`**, **`threading.RLock`**, **`acquire` / `release`**, **deadlock**

## What it looks like

```python
import threading

lock = threading.Lock()
total = 0

def add_one():
    global total
    with lock:            # only one thread at a time runs this block
        total += 1
```

## What it means

- A **`threading.Lock`** (a mutex) is either unlocked or held by one thread. **`acquire()`** takes it, waiting if another thread holds it; **`release()`** gives it back.
- **`with lock:`** calls `acquire()` on entry and `release()` on exit, even if the block raises an exception ([context-managers](context-managers.md)). Always prefer it to calling the two by hand.
- **`acquire(timeout=0.1)`** gives up after 0.1 s and returns `False` instead of waiting forever.
- A plain `Lock` is **not re-entrant**: if the thread holding it calls `acquire()` again, it waits for itself forever. A **`threading.RLock`** (re-entrant lock) counts how many times its owner acquired it and can be taken again by the same thread.
- **Deadlock:** two threads each hold one lock and wait for the other's. Neither ever continues. The usual cure is to always take locks in the same order, or to hold only one at a time.

## Why people use it

To turn a multi-step operation on shared data into one indivisible step: read-modify-write on a counter, check-then-set on a dict ([concepts/data-races](../concepts/data-races.md)). On the free-threaded build, it's the difference between 800,000 and about 175,000 in K4's racy counter demo.

## How Kiln uses it

- `worker/channel.py`: one lock around `sendall`, so frames from the heartbeat thread and job threads never interleave on the socket.
- `worker/main.py` `WorkerCounters`: `jobs_done`, the in-flight count and the "retiring already announced" flag change together under one lock.
- `labs/k4/race_demo.py`: `LockedStats.record` wraps the racy `record` in `with self._lock:`.

## Gotchas

- **A lock protects nothing by itself.** *Every* access to the shared data must go through the same lock; one unlocked reader or writer breaks it.
- **Locks and `fork()` don't mix.** A lock held by another thread when a process forks stays locked forever in the child ([concepts/fork-and-threads](../concepts/fork-and-threads.md)). Kiln's master has no threads for this reason (ADR-006).
- **Thread-unsafe code deadlocks in production, not in tests.** SquadStack's Celery workers deadlocked in production because some code wasn't thread-safe (research [§2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources)).
- **Holding a lock while waiting** (on the network, on another lock) makes every other thread wait too. Keep locked blocks short.

## Try it

```text
>>> import threading
>>> lock = threading.Lock()
>>> lock.acquire()
True
>>> lock.locked()
True
>>> lock.acquire(timeout=0.1)
False
>>> lock.release()
>>> with lock:
...     print("inside:", lock.locked())
...
inside: True
>>> lock.locked()
False
>>> rlock = threading.RLock()
>>> with rlock:
...     with rlock:
...         print("an RLock can be taken twice by the same thread")
...
an RLock can be taken twice by the same thread
>>> total = 0
>>> def add_many():
...     global total
...     for _ in range(100_000):
...         with lock:
...             total += 1
...
>>> ts = [threading.Thread(target=add_many) for _ in range(4)]
>>> for t in ts: t.start()
...
>>> for t in ts: t.join()
...
>>> total
400000
```

(Generated with `scripts/repl_transcript.py docs/python/snippets/locks.repl` on 3.15.1.)

## Check yourself

1. Why did `lock.acquire(timeout=0.1)` return `False` in the session above?
   <details><summary>Answer</summary>

   The same thread already held the lock (the first `acquire()`), and a plain `Lock` isn't re-entrant, so the second acquire waited 0.1 s and gave up.

   </details>
2. Thread A takes lock X then lock Y; thread B takes Y then X. What can happen, and what's the fix?
   <details><summary>Answer</summary>

   A deadlock: A holds X and waits for Y while B holds Y and waits for X. Fix it by having every thread take the locks in the same order (always X before Y).

   </details>
3. Predict: the counter session above without `with lock:`, on 3.15t. Is `total` 400,000?
   <details><summary>Answer</summary>

   Almost certainly not: the free-threaded build loses updates on unprotected `+=` heavily (K4's data-race snippet lost about 78% at 8 threads). With the lock it's exactly 400,000 on every build.

   </details>
