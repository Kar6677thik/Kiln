# Thread safety of built-in types

> **Milestone:** K4 · **Read before:** [data-races](data-races.md), [free-threading](free-threading.md) · **Reading time:** ~9 min
> **Terms introduced:** **per-object lock**, **critical section**, **single operation vs compound operation**, **`queue.Queue`**

## One-sentence summary

On both builds, one operation on a `dict` or `list` (`d[k] = v`, `lst.append(x)`) is safe from any thread; **two operations in a row are not**, so `d[k] = d.get(k, 0) + 1` loses updates even though the dict itself never gets corrupted.

## Why you should care

"Is `dict` thread-safe?" has two different answers depending on what you mean, and mixing them up is how the race in `labs/k4/race_demo.py` (`self.per_task[task] = self.per_task.get(task, 0) + 1`) slips into real code. The free-threaded build (research [§2.9](../research/python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver)) keeps the first promise (no corruption) with per-object locks, and makes breaking the second (no lost updates) common. The research also names what's *not* safe at all: sharing one iterator between threads.

## The mental model

**Analogy:** a library's catalogue drawer has a latch; only one person can be *in the drawer* at a time, so no card is ever torn or misfiled. But "look up the count on a card, walk to your desk, add one, come back and write it" spans two visits to the drawer, and someone else can visit in between.

**Where the analogy fails:** on the GIL build there's no latch at all; one global lock (the GIL) plays the same role for every object at once.

```
 d[k] = d.get(k, 0) + 1
        └── op 1: d.get(k) ──┘        └── op 2: d[k] = ... ──┘
        [lock d │ read │ unlock d]  ...gap...  [lock d │ write │ unlock d]
                                       ▲
                     another thread's get+set can happen here → its update is lost

 per-object lock: each single op is a CRITICAL SECTION; the gap between ops is not protected.
```

## See it for yourself

[`snippets/thread-safety-of-builtins_1.py`](snippets/thread-safety-of-builtins_1.py): 8 threads each do `shared[key] = shared.get(key, 0) + 1` 50,000 times over 4 keys:

```text
build: GIL
keys still intact: True; len(shared) = 4
total = 400,000 of 400,000; lost = 0
```

```text
build: free-threaded
keys still intact: True; len(shared) = 4
total = 197,689 of 400,000; lost = 202,311
```

(3.15.1, then 3.15.1t.)

- On the free-threaded build, **half the increments vanished**, but the dict is perfectly healthy: exactly the 4 keys, valid integer values, no crash, no exception. Every single `get` and every single set was correct; their *combination* wasn't.
- On the GIL build nothing was lost in this run. That's luck of timing, not a guarantee: `d.get` is a call, a point where the GIL can change hands ([data-races](data-races.md)).

## How it really works

- **GIL build.** Built-in operations are implemented in C and run while holding the GIL, so each one finishes before any other thread runs Python code. Nothing extra is needed to keep a dict consistent.
- **Free-threaded build: per-object locks.** PEP 703 gives each mutable built-in container a lightweight lock. A single operation (`dict.__setitem__`, `list.append`, `dict.get`) runs as a **critical section** on that one object. Reads often avoid even that, using optimistic lock-free paths. The result: one operation looks atomic, as with the GIL.
- **Compound operations are never protected for you.** Check-then-act (`if k not in d: d[k] = []`), read-modify-write (`d[k] = d.get(k, 0) + 1`, `x += 1`), iterate-while-another-thread-mutates: each is several operations, and other threads can run between them. You need a `threading.Lock` around the whole sequence, or a different design.
- **Iterators aren't shareable.** The research lists "sharing one iterator across threads" as unsafe (§2.9). Measured with 8 threads looping over one *generator*: threads that tried to resume it while another was inside it got `ValueError: generator already executing` and stopped, 1 thread on 3.15.1 and 7 of 8 on 3.15.1t. Give each thread its own iterator, or hand items out through a queue.
- **`queue.Queue` is the safe hand-off.** `put` and `get` are built on a lock and condition variables, so it's correct with any number of producer and consumer threads. "One thread owns the data; others send it requests through a queue" removes the need for locks in the rest of the code.

## How Kiln uses it

- `labs/k4/race_demo.py`'s `RacyStats.per_task` is exactly the snippet's pattern; the lab counts how many per-task updates it loses.
- `worker/main.py` `WorkerCounters` puts `jobs_done += 1` and the retirement check under one lock, because they're compound (check the limit, then set the "retiring" flag once).
- `src/kiln/worker/executors/threads.py` relies on `concurrent.futures`, whose work queue is a thread-safe queue internally: the main thread submits, pool threads take.

## Common misconceptions

- ❌ **Myth:** "`dict` is thread-safe, so my counter dict is fine."
  ✅ **Reality:** single dict operations are safe; your get-then-set is two of them. Above, 202,311 of 400,000 updates were lost with a perfectly healthy dict.
- ❌ **Myth:** "without the GIL, a dict shared between threads will crash or corrupt."
  ✅ **Reality:** per-object locks keep it consistent on 3.15t. The danger is wrong *logic*, not a broken dict.
- ❌ **Myth:** "`list.append` from several threads loses items."
  ✅ **Reality:** a single `append` is one operation and safe on both builds. Losing items needs a compound sequence, like reading `len(lst)` and then writing at that index.

## Check yourself

1. On 3.15t, can `d[k] = d.get(k, 0) + 1` from many threads *corrupt* the dict? Can it *lose updates*? Explain the difference.
   <details><summary>Answer</summary>

   It can't corrupt it: each `get` and each set is a separate critical section on the dict's own lock, so the dict's internal structure stays valid. It can lose updates: between one thread's `get` and its set, another thread can complete its own get-and-set, and the first thread then overwrites that result with a value computed from the old count.

   </details>
2. Two threads each run `for item in shared_generator: process(item)`. What's wrong, and what's the usual fix?
   <details><summary>Answer</summary>

   Sharing one iterator between threads isn't safe. With a generator, a thread that tries to resume it while another thread is inside it gets `ValueError: generator already executing`, which ends its loop (measured on both builds, much more often on 3.15t). Put the items into a `queue.Queue` from one producer and let each thread `get()` from it.

   </details>
3. Predict: 8 threads, each `lst.append(i)` 50,000 times into one list, on 3.15t. `len(lst)` at the end?
   <details><summary>Answer</summary>

   Exactly 400,000. A single `append` is one operation, protected by the list's own lock.

   </details>

## Go deeper

- [PEP 703, "Container thread-safety"](https://peps.python.org/pep-0703/#container-thread-safety): how per-object locking and optimistic reads work.
- [Free-threading HOWTO: thread safety](https://docs.python.org/3.15/howto/free-threading-python.html).
- [`queue` module docs](https://docs.python.org/3.15/library/queue.html).
