# The Cycle Garbage Collector

> **Milestone:** K3 · **Read before:** [refcounting](refcounting.md), [copy-on-write](copy-on-write.md) · **Reading time:** ~15 min
> **Terms introduced:** **cycle collector**, **tracked object**, **generation**, **threshold**, **automatic collection**, **`gc_refs`**

## One-sentence summary

CPython's **cycle collector** periodically scans the container objects it tracks to find groups that only reference each other, and frees them; it runs automatically when allocations cross a **threshold**, and every scan *writes* into the header of every object it looks at.

## Why you should care

The GC is the second half of Instagram's copy-on-read problem (research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)). One full collection in a forked worker touches every tracked object in the preloaded app, and copies every page it touches. Instagram's first fix was to turn the GC off, and they learned that "off" has three different meanings: `gc.disable()`, `gc.set_threshold(0)`, and an explicit `gc.collect()` that runs anyway. Kiln's `worker.gc_mode` setting (`default`, `disabled`, `threshold0`, `freeze`) is built from exactly these.

## The mental model

**Analogy:** a cleaner who doesn't trust the "on loan" tally ([refcounting](refcounting.md)). From time to time they take every book on a cart (the tracked objects) and, for each one, subtract the loans that come *from other books on the same cart*. Any book still showing a loan is held by someone outside, so it and everything it holds stay. The rest are cycles nobody can reach, and they're thrown out. Young carts are checked often, and old carts rarely.

**Where the analogy fails:** to do the subtraction, the cleaner writes a temporary number (`gc_refs`) on every book's cover, so every inspected book gets marked even if it stays. In a forked worker, those marks are writes, and the pages get copied.

```
   allocations of tracked containers ──▶ young generation (gen 0)
                                          │ count > threshold[0] (2000) → collect gen 0
                                          ▼ survivors
                                        gen 1 ── every threshold[1] (10) gen-0 runs → collect 0+1
                                          ▼ survivors
                                        gen 2 (old) ── every threshold[2] (10) gen-1 runs → FULL collection
   gc.freeze() moves everything to a     │                                             (scans everything,
   "permanent generation" never scanned ◀┘                                              writes every header)

   one collection, per scanned object:  copy refcount into gc_refs → subtract internal refs
                                        → anything with gc_refs > 0 is reachable → mark what it reaches
                                        → the rest is garbage
```

## See it for yourself

[`snippets/garbage-collector_1.py`](snippets/garbage-collector_1.py) creates 100,000 small cycles under four settings and counts how many collections *start*, using a `gc.callbacks` hook:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/garbage-collector_1.py
```

```text
threshold: (2000, 10, 10)  is a list tracked by the GC? True  an int? False
default                                        automatic collections: 96
gc.disable()                                   automatic collections: 0
gc.disable() then a library calls gc.enable()  automatic collections: 97
gc.set_threshold(0), then gc.enable()          automatic collections: 0
explicit gc.collect() while disabled: ran 1 collection, found 200226 objects
```

(Python 3.15.1. Python 3.13.17 printed the same five numbers; 3.15.1t printed 82 instead of 96 and 97.)

- **Thresholds `(2000, 10, 10)`:** a young collection after 2,000 net new tracked objects, a middle one after 10 young ones, a full one after 10 middle ones.
- **Lists are tracked; ints aren't.** Only containers that can be part of a cycle are tracked.
- **default:** 96 automatic collections while making 200,000 lists.
- **`gc.disable()`:** none, until a library calls `gc.enable()` and they come back (97). That's what Instagram hit with msgpack.
- **`gc.set_threshold(0)`:** none, *even after* `gc.enable()`. Threshold 0 means "never trigger automatically", whoever enables the GC.
- **Explicit `gc.collect()`** ran while the GC was disabled, and found 200,226 objects: the 100,000 two-list cycles left behind by the `set_threshold(0)` phase (the earlier `gc.disable()` leftovers were already collected once `gc.enable()` brought automatic collections back). "Disabled" only stops *automatic* collections.

## How it really works

- **Tracked objects.** Containers (lists, dicts, sets, class instances, frames…) are allocated with an extra **GC header**, `PyGC_Head`, just before the object, holding two pointers that link it into its generation's list. Atomic objects (ints, floats, strings) have no header and are never scanned. CPython may *untrack* a container that can't be in a cycle: on 3.13.17 a dict like `{"id": 1, "name": "user1"}` reported `gc.is_tracked(...) == False`, while 3.14.9 and 3.15.1 reported `True` (measured in K3; this changes how much a full collection touches, see [gc-freeze](gc-freeze.md)).
- **Automatic collections.** Each allocation of a tracked object bumps a counter. When it passes `threshold[0]`, the collector runs, from inside whatever allocation pushed it over. That's why a collection can happen in the middle of a request, in any code. `gc.get_count()` shows the counters.
- **One collection** (`Python/gc.c`, `gc_collect_main` and helpers): for every object in the generations being collected it copies the refcount into `gc_refs` (stored in the GC header's bits), visits each object's references to subtract internal ones, then treats objects with `gc_refs > 0` as roots and marks everything reachable from them. Unreachable objects get their finalizers called and are freed. Survivors move to the next generation by relinking their headers. **Every step writes the header**, and the header sits on the same page as the object, so in a forked worker every scanned object's page gets copied.
- **Three ways to "turn it off":**
  - `gc.disable()` sets a flag. Anyone can call `gc.enable()` again, including C extensions through `PyGC_Enable()`.
  - `gc.set_threshold(0)` makes the trigger never fire. `gc.enable()` doesn't change the threshold, so this survives a rogue library (gate G3.2: 0 automatic collections on 3.13, 3.14, 3.15 and 3.15t).
  - `gc.collect()` always runs, disabled or not (gate G3.4). The interpreter also runs a final collection during shutdown ([finalization-and-atexit](finalization-and-atexit.md)).
- **`gc.freeze()`** moves every tracked object into a permanent generation that collections skip ([gc-freeze](gc-freeze.md)).
- **3.14's incremental GC.** 3.14.0–3.14.4 shipped a collector that worked in small increments. It was reverted to the generational design in 3.14.5 (research §2.2). Kiln's 3.14 venv is 3.14.9, the generational one. K7 replays that regression.
- **Free-threaded 3.15t** has no `PyGC_Head` and no generation lists: `Python/gc_free_threading.c` finds objects by walking the memory allocator's heaps and keeps its marks in per-object bits. It still writes into scanned objects, and the K3 freeze snippet copied 22.2 MiB with one full collection on 3.15.1t.

## How Kiln uses it

- `src/kiln/memory/gcmodes.py` implements `worker.gc_mode`: `disabled` → `gc.disable()`, `threshold0` → `gc.set_threshold(0)`, `freeze` → the freeze recipe. Hooks run in the master or forkserver before the preload, right before each fork, and early in each worker.
- Lab tasks: `labs.churn(n)` creates cycles to trigger automatic collections, `labs.gc_collect(generation)` runs an explicit one, and `labs.gc_stats` reports `isenabled`, thresholds, counts, per-generation stats and the freeze count.
- `src/kiln/memory/gcwatch.py` detects a library re-enabling the GC ([global-interpreter-state](global-interpreter-state.md)).
- Code tag: `CONCEPT(garbage-collector)`.

## Common misconceptions

- ❌ **Myth:** "`gc.disable()` turns garbage collection off."
  ✅ **Reality:** it turns *automatic cycle collection* off, until someone calls `gc.enable()`. Refcounting still frees almost everything, and `gc.collect()` still runs.
- ❌ **Myth:** "the GC only touches garbage."
  ✅ **Reality:** it reads and *writes* every tracked object in the generations it collects, live ones included. That's why it destroys copy-on-write sharing.
- ❌ **Myth:** "a full collection only happens when I call `gc.collect()`."
  ✅ **Reality:** with default thresholds, every tenth middle collection is a full one, triggered by an ordinary allocation anywhere.

## Check yourself

1. Predict: after `gc.set_threshold(0)`, a library calls `gc.enable()`. How many automatic collections happen while creating 100,000 cycles? And if your code then calls `gc.collect()`?
   <details><summary>Answer</summary>

   Zero automatic collections: the threshold, not the enabled flag, controls the trigger. `gc.collect()` still runs one collection and frees the cycles.

   </details>
2. Why does one full collection in a forked worker copy pages holding objects that are still alive?
   <details><summary>Answer</summary>

   The collector writes `gc_refs` and relinks the GC header of every object it scans, and the header sits on the same page as the object. Live objects are scanned too, so their pages are written and copied.

   </details>
3. Why are ints and strings never scanned?
   <details><summary>Answer</summary>

   They can't refer to other objects, so they can't be part of a cycle. They're allocated without a GC header and aren't tracked.

   </details>
4. Instagram switched from `gc.disable()` to `gc.set_threshold(0)`. What exactly did that protect them from?
   <details><summary>Answer</summary>

   From any library (msgpack, in their case) calling `gc.enable()`. With threshold 0, enabling the GC doesn't make automatic collections happen.

   </details>

## Go deeper

- [Python docs: `gc` module](https://docs.python.org/3.15/library/gc.html), including the `freeze` recipe.
- [CPython developer guide: Garbage collector design](https://github.com/python/cpython/blob/main/InternalDocs/garbage_collector.md): generations, `gc_refs`, untracking, the free-threaded collector.
- CPython source: `Python/gc.c` (`gc_collect_main`, `update_refs`, `subtract_refs`), `Python/gc_free_threading.c`.
- Next: [gc-freeze](gc-freeze.md).
