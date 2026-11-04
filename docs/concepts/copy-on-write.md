# Copy-on-Write (and "Copy-on-Read" in Python)

> **Milestone:** K3 · **Read before:** [virtual-memory-and-pages](virtual-memory-and-pages.md), [fork](fork.md), [memory-metrics](memory-metrics.md) · **Reading time:** ~14 min
> **Terms introduced:** **copy-on-write (CoW)**, **copy-on-read**, **shared page**, **private page**, **dirty page**

## One-sentence summary

After `fork()`, parent and child share every memory page until one of them **writes** to a page; at that moment the kernel gives the writer its own **private** copy of that 4 KiB page, and in CPython even *reading* an object writes to it, so reads copy pages too.

## Why you should care

A pre-fork server like Kiln, gunicorn or uWSGI loads the application once in the master and forks workers, hoping they share that memory. Instagram's Django workers did share it, for a few seconds. Then each worker's shared memory fell from about 250 MB to 140 MB (research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)). Instagram called it **copy-on-read**: CPython writes to objects when you merely look at them. K3's lab (`docs/labs/K3-cow.md`) reproduces this in Kiln and measures each of Instagram's fixes.

## The mental model

**Analogy:** two students share one printed textbook. As long as they only read, one copy is enough. The moment one wants to write a note in the margin of page 57, the photocopier makes them a private copy of *just page 57*. Everything else stays shared.

**Where the analogy fails:** in CPython, *reading* a page of the textbook also writes in the margin. Every time you look at an object, CPython bumps a counter stored inside it (its reference count, see [refcounting](refcounting.md)). So "just reading" photocopies pages too.

```
 right after fork()                     child writes to page 2                child "reads" a Python object on page 3
 parent table   child table             parent table   child table            (= refcount++ / refcount--: a write)
   p1 ─┐  ┌─ p1                           p1 ─┐  ┌─ p1                           p1 ─┐  ┌─ p1
   p2 ─┼──┼─ p2     all frames             p2 ─┼──┼─┐                             p2 ─┼──┼─┐
   p3 ─┼──┼─ p3     shared, marked         p3 ─┼──┼─ p3                           p3 ─┼──┼─┐│
       ▼  ▼         read-only                  ▼  ▼   ▼                               ▼  ▼ ▼▼
     [F1][F2][F3]                            [F1][F2][F3] [F2'] ← private copy      [F1][F2][F3] [F2'][F3']
     Shared = 3 pages, Private = 0           Shared = 2, child Private_Dirty = 1    Shared = 1, child Private_Dirty = 2
```

## See it for yourself

[`snippets/copy-on-write_1.py`](snippets/copy-on-write_1.py) builds 64 MiB of raw bytes and 300,000 small `str` objects, forks, and lets the child measure its own `Private_Dirty` (the pages it has had to copy, read from `/proc/self/smaps_rollup`) after each step:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/copy-on-write_1.py
```

```text
child: private dirty right after fork        +  0.1 MiB
child: after reading all 64 MiB of bytes     +  0.2 MiB
child: after writing 16 MiB of bytes         + 16.2 MiB
child: after reading 300,000 str objects     + 18.4 MiB more
```

(Python 3.15.1. The same run on 3.13.17 gave identical numbers; 3.15.1t gave 23.0 MiB on the last line.)

- **Right after fork:** almost nothing is private. Everything is shared.
- **Reading raw bytes** (`raw[i]` on a `bytearray`) copies nothing: the bytes are plain data, and reading them doesn't touch any object header on those pages.
- **Writing 16 MiB** of the buffer copies almost exactly 16 MiB: one 4 KiB copy per written page.
- **Reading 300,000 strings** (`len(s)` for each) copied **18.4 MiB**, although the code never assigned anything. Iterating the list gives each string a new reference for a moment: its `ob_refcnt` field goes up and back down. Each string's header lives on the same pages as its text, so nearly every page holding strings gets copied. That's copy-on-read.

## How it really works

1. **At fork,** the kernel copies the parent's page tables and marks every writable page **read-only in both processes**, remembering that the page is really "CoW" (`man 2 fork`).
2. **On the first write** by either process, the CPU raises a protection fault. The kernel sees the CoW marking, allocates a new frame, copies the 4 KiB, points the writer's page table at the copy (now writable) and resumes the write. The other process keeps the original. If only one process still maps a frame, the kernel simply makes it writable again without copying.
3. **Accounting** ([smaps](smaps.md)): before the write the page is counted as `Shared_Clean` in both processes; afterwards the copy is `Private_Dirty` in the writer. A **dirty page** is one that has been modified in memory. **Growing `Private_Dirty` in a worker is the copy-on-write signal** ([spec/10 §7](../spec/10-measurement-methodology.md#7-memory-metrics-definitions)).
4. **Why CPython writes when it reads.** Every object starts with a header containing `ob_refcnt`. Every new reference increments it, and every dropped reference decrements it (`Py_INCREF`/`Py_DECREF` in `Include/refcount.h`). Iterating, passing an argument, or storing a value in a local variable all do this.
5. **The garbage collector writes too.** Container objects (lists, dicts, class instances) carry an extra GC header. A collection writes bookkeeping into the header of every object it scans ([garbage-collector](garbage-collector.md)), so one full collection can copy every page holding tracked objects. [gc-freeze](gc-freeze.md) shows the numbers.
6. **What doesn't get copied:** pages nobody writes. That includes raw buffers (`bytes`, `bytearray`, `array`, NumPy data) that are only read, code pages, and **immortal objects** whose refcount is never changed ([immortal-objects](immortal-objects.md)).

## How Kiln uses it

- The master builds the preload graph (`src/kiln/labs/preload.py`, `labs.preload_mb`) *before* forking, so every worker starts by sharing it.
- The lab task `labs.touch_preloaded(fraction)` reads part of the graph in a worker; `labs.churn` and `labs.gc_collect` make the GC run. A separate sampler process reads `smaps_rollup` for each worker every 200 ms (spec/10 §7) to watch `Private_Dirty` grow.
- `worker.gc_mode` (`src/kiln/memory/gcmodes.py`) tries Instagram's fixes: `disabled`, `threshold0` and `freeze`.
- Code tag: `CONCEPT(copy-on-write)`.

## Common misconceptions

- ❌ **Myth:** "forked workers share memory, so 10 workers cost the same as one."
  ✅ **Reality:** they share only pages nobody has written. In CPython, every object a worker touches makes its page private. Instagram lost about 110 MB of sharing per worker within seconds.
- ❌ **Myth:** "if my worker code only reads the preloaded data, it stays shared."
  ✅ **Reality:** reading Python objects changes their reference counts. The snippet copied 18.4 MiB by "reading" strings. Only raw buffer data and immortal objects can be read without copies.
- ❌ **Myth:** "copy-on-write copies the whole object (or the whole process)."
  ✅ **Reality:** it copies one 4 KiB page at a time, whatever objects happen to be on it.

## Check yourself

1. Predict: in the snippet, what would `Private_Dirty` show if the child read the 300,000 strings a *second* time?
   <details><summary>Answer</summary>

   No further growth: measured on 3.15.1, a second pass added +0.0 MiB. The pages were already copied by the first pass; they're now private, and writing to a private page costs nothing extra.

   </details>
2. Why does reading a `bytearray` not copy pages, while reading a list of strings does?
   <details><summary>Answer</summary>

   `raw[i]` reads plain bytes inside one big buffer; the buffer's object header sits on one page and its refcount changes at most once. Each string is a separate object whose header (with `ob_refcnt`) shares its page with the text, so referencing each string writes to each page.

   </details>
3. After the child copies a page, which process's `smaps_rollup` shows `Private_Dirty`, and what happens to the parent's numbers?
   <details><summary>Answer</summary>

   The child's: the copy belongs to it. If the parent is the last process still mapping the original frame, that page becomes private to the parent too (its `Shared` goes down, its `Private_Clean` goes up).

   </details>
4. Instagram disabled the GC and shared memory improved, but not back to 250 MB. Why couldn't disabling the GC fix everything?
   <details><summary>Answer</summary>

   Disabling the GC removes the GC's writes, but not the reference count writes that every object access makes. Those can only be avoided for immortal objects.

   </details>

## Go deeper

- `man 2 fork` ("copy-on-write pages"), `man 5 proc` (`smaps` fields).
- Instagram Engineering, "Dismissing Python Garbage Collection at Instagram" and "Copy-on-write friendly Python garbage collection" (research §8; read through the research summary).
- [PEP 683: Immortal Objects](https://peps.python.org/pep-0683/), whose motivation section is exactly this problem.
- Next: [smaps](smaps.md), [refcounting](refcounting.md).
