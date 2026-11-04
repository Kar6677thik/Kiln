# gc.freeze() and the Permanent Generation

> **Milestone:** K3 · **Read before:** [garbage-collector](garbage-collector.md), [copy-on-write](copy-on-write.md), [refcounting](refcounting.md) · **Reading time:** ~11 min
> **Terms introduced:** **`gc.freeze()`**, **permanent generation**, **freeze recipe**, **memory hole**

## One-sentence summary

`gc.freeze()` moves every object the collector currently tracks into a **permanent generation** that future collections skip, so a forked worker's collections no longer write to the parent's objects; but it does nothing about reference count writes, so *reading* those objects still copies their pages (errata E-15).

## Why you should care

`gc.freeze()` (Python 3.7) was CPython's official answer to Instagram's GC problem (research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)), and it's often described as "the fix for copy-on-write". It fixes exactly one of the two sources of writes. K3's lab measures how much it helps in Kiln (`worker.gc_mode = freeze`) and makes the remaining cost visible.

## The mental model

**Analogy:** the cleaner from [garbage-collector](garbage-collector.md) gets a sealed archive room. `freeze()` moves every book currently on the carts into it, and the cleaner never inspects the archive again, so no more marks get written on those covers.

**Where the analogy fails:** readers still borrow archive books, and every loan still adds a tally mark on the cover ([refcounting](refcounting.md)). The archive stops the *cleaner's* writes, not the *readers'*.

```
 parent (gc.disable() early ... build the app ... gc.freeze() ... fork())
   young ─┐
   gen 1 ─┼──freeze()──▶ permanent generation   (not scanned by any later collection)
   old   ─┘
 child (gc.enable() early)
   new objects ──▶ young / gen 1 / old ── collections scan ONLY these
   reading a frozen object ──▶ ob_refcnt++ / ob_refcnt-- ──▶ page copied anyway (E-15)
```

## See it for yourself

[`snippets/gc-freeze_1.py`](snippets/gc-freeze_1.py) builds 300,000 small dicts with the GC disabled, then forks two children: one without `freeze()`, one after it. Each child re-enables the GC, runs a full collection, then reads every dict, measuring how many MiB of shared pages it had to copy (`Private_Dirty` growth):

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/gc-freeze_1.py
```

```text
without freeze   frozen=      0  gc copied  38.0 MiB, then reading copied   0.0 MiB more
with freeze      frozen= 305518  gc copied   0.2 MiB, then reading copied  36.7 MiB more
```

(Python 3.15.1.)

- **Without freeze,** one full collection copied **38.0 MiB**: it wrote into every tracked dict. Reading afterwards copied nothing more, because those pages were already private.
- **With freeze,** 305,518 objects were in the permanent generation, and the full collection copied only **0.2 MiB**. Freeze worked.
- **But reading** the frozen dicts still copied **36.7 MiB**: each read increments and decrements a refcount. Freeze can't prevent that. In total the worker copied about as much either way. Freeze only helps if the worker never touches most of the frozen objects.

The same snippet on other builds:

| interpreter | without freeze: gc / read | with freeze: frozen count, gc / read |
|---|---|---|
| 3.13.17 | 1.2 / 36.7 MiB | 5,198 objects, 0.2 / 36.7 MiB |
| 3.15.1 | 38.0 / 0.0 MiB | 305,518 objects, 0.2 / 36.7 MiB |
| 3.15.1t | 22.2 / 0.0 MiB | 306,404 objects, 0.6 / 18.4 MiB |

On 3.13 these dicts (holding only an int and a str) were **untracked** by the GC, so even without freeze the collection didn't touch them, and freeze had only 5,198 objects to move. 3.14 and 3.15 track them ([garbage-collector](garbage-collector.md)), so the collection's writes, and freeze's benefit, are much larger.

## How it really works

- **What freeze does.** On the GIL build, `gc_freeze_impl` (`Python/gc.c`) splices the young and old generation lists onto the permanent generation's list. That's a few pointer updates, not a walk over every object, so it's fast even for millions of objects. `gc.get_freeze_count()` reports how many objects are in it. `gc.unfreeze()` moves them back into the oldest generation.
- **What collections then skip.** A collection only walks the lists of the generations it collects. The permanent generation is never one of them, so frozen objects' GC headers are never written again.
- **What freeze doesn't change.** Refcounts. Every reference to a frozen object still writes `ob_refcnt` (or, on 3.15t, `ob_ref_local`/`ob_ref_shared`) inside it.
- **The recipe** (from the `gc` module docs): *`gc.disable()` early in the parent, `gc.freeze()` right before `fork()`, `gc.enable()` early in the child.* Each step has a reason:
  1. **Disable early in the parent.** A collection in the parent frees garbage and leaves **memory holes**: free slots inside pages that also hold live objects. After fork the child allocates new objects into those holes, which writes the shared pages and copies them. With the GC off while the app loads, no holes appear.
  2. **Freeze right before fork,** so *everything* the app created is in the permanent generation, including objects created late in startup.
  3. **Enable early in the child,** so the worker's own new garbage, which isn't shared, is collected normally.
- **Free-threaded 3.15t.** There are no generation lists. `Python/gc_free_threading.c` marks each object with a per-object bit, `_PyGC_BITS_FROZEN` (defined in `Include/internal/pycore_gc.h`, which ships with the 3.15t install), and collections skip marked objects. Gate G3.3 confirmed `gc.freeze()` and `gc.get_freeze_count()` exist and behave the same way on 3.15.1 and 3.15.1t, and the table above shows the same pattern.

## How Kiln uses it

- `src/kiln/memory/gcmodes.py`, `gc_mode = "freeze"`: `apply_parent_early()` calls `gc.disable()` in the master (or forkserver) before the app and the preload graph are loaded, `before_fork()` calls `gc.freeze()` right before every `os.fork()`, and `apply_child_early()` calls `gc.enable()` in each worker.
- Under spawn nothing is shared, so the master logs a WARNING once and the worker behaves like `default`.
- K3 experiment (a) compares `freeze` with `default`, `disabled` and `threshold0` phase by phase. Experiment (d) measures what a final collection would cost under `default` vs `freeze`.
- Code tag: `CONCEPT(gc-freeze)`.

## Common misconceptions

- ❌ **Myth:** "`gc.freeze()` makes the objects read-only / immutable."
  ✅ **Reality:** frozen objects are ordinary objects. You can modify them, and every reference still changes their refcount. Only the cycle collector ignores them.
- ❌ **Myth:** "after `gc.freeze()`, forked workers keep sharing the preloaded data."
  ✅ **Reality:** they keep sharing what they *don't touch*. The snippet copied 36.7 MiB just by reading frozen dicts (errata E-15).
- ❌ **Myth:** "freeze is expensive on a big heap because it visits every object."
  ✅ **Reality:** on the GIL build it relinks lists, so its cost doesn't grow with the number of objects. (Not timed in K3.)

## Check yourself

1. Predict: a worker forked after `gc.freeze()` never reads the preloaded graph, but serves requests that create lots of new cycles. Does the graph stay shared?
   <details><summary>Answer</summary>

   Yes, mostly. The automatic collections only scan the worker's new objects; the frozen graph is skipped, and nothing references it. That's the case freeze was designed for.

   </details>
2. Why does the recipe call `gc.disable()` *early* in the parent, instead of just freezing before fork?
   <details><summary>Answer</summary>

   A collection in the parent would free garbage and leave holes in pages holding live objects. The child would then allocate into those holes, writing the shared pages. Disabling early avoids creating the holes.

   </details>
3. On 3.13.17, freeze reduced the full-collection copy from 1.2 to 0.2 MiB; on 3.15.1, from 38.0 to 0.2 MiB. Why such a different benefit for the same code?
   <details><summary>Answer</summary>

   3.13 didn't track those simple dicts, so its collector never touched them in the first place. 3.15 tracks them, so without freeze the collector wrote into all 300,000.

   </details>
4. Is the 36.7 MiB copied "by reading" a bug in freeze?
   <details><summary>Answer</summary>

   No. Freeze only affects the cycle collector. Reading changes reference counts, which freeze was never meant to stop. Only immortal objects avoid refcount writes.

   </details>

## Go deeper

- [Python docs: `gc.freeze()`](https://docs.python.org/3.15/library/gc.html#gc.freeze), with the recipe.
- CPython source: `Python/gc.c` (`gc_freeze_impl`, `gc_unfreeze_impl`), `Python/gc_free_threading.c`.
- [bpo-31558](https://bugs.python.org/issue31558): the issue that added `gc.freeze()` (Python 3.7).
- Next: [immortal-objects](immortal-objects.md).
