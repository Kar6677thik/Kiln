# Reference Counting

> **Milestone:** K3 · **Read before:** [classes-and-objects](../python/classes-and-objects.md), [copy-on-write](copy-on-write.md) · **Reading time:** ~11 min
> **Terms introduced:** **reference count (`ob_refcnt`)**, **incref / decref**, **deallocation**, **reference cycle**, **biased reference counting**

## One-sentence summary

Every CPython object stores a counter of how many references point at it; the counter goes up and down on almost every operation, and the object is freed the instant it reaches zero, except that objects that refer to each other in a **cycle** never reach zero on their own.

## Why you should care

Two K3 lessons rest on this:
1. **Copy-on-read.** Changing the counter is a *write* into the object's memory, so "just reading" shared objects in a forked worker copies their pages ([copy-on-write](copy-on-write.md); errata E-15).
2. **Why a garbage collector exists at all.** Counting frees almost everything immediately, but not cycles. That's the job of the cycle collector ([garbage-collector](garbage-collector.md)), the other source of writes Instagram fought.

## The mental model

**Analogy:** a library book with a tally of how many people currently have it on loan. Each new borrower adds a tally mark; each return removes one. When the tally hits zero, the book goes back to the stacks (it's freed).

**Where the analogy fails:** two books can "borrow" each other: book A holds B and B holds A. Even when nobody else has either, each tally stays at 1 forever. Only someone walking the shelves looking for books nobody can reach (the cycle collector) can return them.

```
  a = Thing()        b = a            del b            del a
  ┌───────────┐      ┌───────────┐    ┌───────────┐    refcount 0 → freed at once
  │ ob_refcnt 1│     │ ob_refcnt 2│   │ ob_refcnt 1│    (__del__ / finalizers run now)
  │ ob_type    │     │ ...        │   │ ...        │
  └───────────┘      └───────────┘    └───────────┘

  cycle:  x ──▶ [Thing] ──.self──┐      del x  →  refcount 1 (from itself)  →  never 0
                   ▲─────────────┘                only the cycle collector frees it
```

## See it for yourself

[`snippets/refcounting_1.py`](snippets/refcounting_1.py):

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/refcounting_1.py
```

```text
refcount of a fresh object: 2 (1 for 'a' + 1 for the argument)
after b = a and a list holding it twice: 5
after deleting those references: 2
del a (count reaches 0):
  -> the Thing was freed
... freed immediately, no garbage collector involved
del x (it still refers to itself, so its count stays at 1):
... nothing printed: refcounting alone can't free a cycle
  -> the cyclic Thing was freed
gc.collect() found 2 unreachable objects
```

(Python 3.15.1.)

- `sys.getrefcount(a)` says **2**, not 1: passing `a` as an argument creates one more reference for the duration of the call ([sys-getrefcount](../python/sys-getrefcount.md)).
- `b = a` adds 1, and a list holding `a` twice adds 2: total 5.
- `del a` drops the last reference, and the object is freed **on that line**: its finalizer prints before the next `print`.
- The self-referencing `x` survives `del x`. Only `gc.collect()` frees it (its finalizer prints during the collection). The collector reported 2 unreachable objects: the `Thing` and an internal helper object that belonged to it.

## How it really works

- **The header.** On the GIL build every object begins with `ob_refcnt` and `ob_type` (`Include/object.h`). `Py_INCREF(op)` adds 1, `Py_DECREF(op)` subtracts 1 and calls the type's deallocator when the count reaches 0 (`Include/refcount.h`).
- **Where increfs happen.** Binding a name, storing in a container, passing an argument, returning a value, and pushing an object on the interpreter's stack. Iterating a list increfs each item as it's produced. Some 3.14+ bytecodes "borrow" a reference instead of increfing when it's provably safe, but iteration, containers and calls still write counts.
- **Why it's a memory write.** `ob_refcnt` lives *inside* the object, on the same 4 KiB page as the object's data. A forked worker that references a shared object therefore dirties that page, and the kernel copies it ([copy-on-write](copy-on-write.md)).
- **Deallocation is immediate and deterministic.** Files closed by `__del__`, `weakref.finalize` callbacks and memory release happen exactly when the count reaches 0. That's why most CPython programs barely need the GC.
- **Cycles.** If A references B and B references A (or an object references itself, or a parent and child point at each other), neither count can reach 0. The **cycle collector** finds such groups that nothing outside references ([garbage-collector](garbage-collector.md)).
- **Immortal objects** (PEP 683, Python 3.12+) have a special refcount value that `Py_INCREF`/`Py_DECREF` leave unchanged, so they're never written ([immortal-objects](immortal-objects.md)). `sys.getrefcount(None)` reports `3221225472` on 3.14 and 3.15.
- **Free-threaded builds (3.13t+, here 3.15t).** A single shared counter would need an atomic operation on every incref, which is slow. **Biased reference counting** (PEP 703) splits it: `ob_ref_local`, changed without atomics by the thread that owns the object, and `ob_ref_shared`, changed atomically by other threads. Both are still writes to the object, so copy-on-read still happens: the CoW snippet copied 23.0 MiB on 3.15.1t when reading 300,000 strings, versus 18.4 MiB on 3.15.1.

## How Kiln uses it

- Kiln doesn't manipulate refcounts. K3 *measures* their side effect: `labs.touch_preloaded(fraction)` reads part of the preloaded graph (`src/kiln/labs/preload.py`), and the lab watches `Private_Dirty` grow in each worker.
- The "immortal-heavy" graph composition uses small ints, `None`/`True`/`False` and interned words as leaf values, to separate refcount writes on leaves from those on mortal containers (K3 experiment (b)).
- Code tag: `CONCEPT(refcounting)`.

## Common misconceptions

- ❌ **Myth:** "Python frees memory only when the garbage collector runs."
  ✅ **Reality:** in CPython almost everything is freed by refcounting, immediately, when the last reference goes. The GC only handles cycles.
- ❌ **Myth:** "reading an object doesn't change it."
  ✅ **Reality:** on CPython, referencing an object writes its refcount. That one fact is the whole copy-on-read problem.
- ❌ **Myth:** "`sys.getrefcount(x)` tells you how many variables point at `x`."
  ✅ **Reality:** it's one more than that (the argument), and for immortal objects it's a huge constant that means nothing about usage.

## Check yourself

1. Predict: `x = []; print(sys.getrefcount(x)); y = [x, x, x]; print(sys.getrefcount(x))`.
   <details><summary>Answer</summary>

   `2` then `5`. One reference for `x`, one for the argument, then three more from the list.

   </details>
2. Why does `del a` in the snippet print "the Thing was freed" *immediately*, while `del x` prints nothing?
   <details><summary>Answer</summary>

   `del a` removed the last reference, so the count reached 0 and CPython deallocated it on the spot. `x` refers to itself, so after `del x` its count is still 1; only the cycle collector can free it.

   </details>
3. Why does a forked worker that only *reads* a shared list of 1 million strings end up with lots of `Private_Dirty`?
   <details><summary>Answer</summary>

   Each read increments and decrements the string's `ob_refcnt`, which is a write to the page holding the string. The kernel copies every such page into the worker.

   </details>
4. Free-threaded Python splits the refcount into a local and a shared part. Does that avoid copy-on-read?
   <details><summary>Answer</summary>

   No. Both parts are fields inside the object, so changing either one writes to the object's page. The measurement agrees: 23.0 MiB copied on 3.15.1t.

   </details>

## Go deeper

- CPython source: `Include/object.h` (`PyObject`, `ob_refcnt`), `Include/refcount.h` (`Py_INCREF`, `Py_DECREF`).
- [Python docs: Reference Counting (C API)](https://docs.python.org/3.15/c-api/refcounting.html).
- [PEP 683: Immortal Objects](https://peps.python.org/pep-0683/) and [PEP 703: Making the GIL Optional](https://peps.python.org/pep-0703/) (biased reference counting).
- Next: [garbage-collector](garbage-collector.md).
