# Immortal Objects (PEP 683)

> **Milestone:** K3 · **Read before:** [refcounting](refcounting.md), [copy-on-write](copy-on-write.md) · **Reading time:** ~11 min
> **Terms introduced:** **immortal object**, **PEP 683**, **small int cache**, **interning**

## One-sentence summary

An **immortal object** has a special reference count that CPython never changes, so it is never freed and, crucially for K3, referencing it never writes to its memory; but only a specific, version-dependent set of objects is immortal, and it doesn't include your app's dicts and lists.

## Why you should care

Copy-on-read happens because every reference writes `ob_refcnt` ([refcounting](refcounting.md)). Immortal objects are the one kind of object that can be read from a forked worker without copying its page. Meta built them in their Cinder fork of CPython for exactly Instagram's problem, and they went upstream as **PEP 683** in Python 3.12 (research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)). K3's experiment (b) asks how much they help when a graph's *leaf values* are immortal but its containers aren't.

## The mental model

**Analogy:** the library's reference section. Those books can't be borrowed, so nobody adds tally marks to their covers, and they're never thrown out. Reading them leaves them untouched.

**Where the analogy fails:** which books count as "reference" changes from edition to edition (Python version to Python version), and even depends on *how* a book got onto the shelf (see the `.pyc` surprise below).

```
 mortal object                         immortal object
 ┌──────────────────────┐              ┌───────────────────────────────┐
 │ ob_refcnt: 3 → 4 → 3 │ ← written    │ ob_refcnt: 3221225472 (fixed) │ ← Py_INCREF/DECREF
 │ ob_type              │   on every   │ ob_type                       │   see the immortal value
 │ ...                  │   reference  │ ...                           │   and do nothing
 └──────────────────────┘              └───────────────────────────────┘
   page gets copied in a forked worker   page stays shared
```

## See it for yourself

[`snippets/immortal-objects_1.py`](snippets/immortal-objects_1.py) asks `sys._is_immortal()` (new in 3.14) about common objects:

```bash
(wsl) $ KILN_PY=3.15 ./scripts/dev.sh run docs/concepts/snippets/immortal-objects_1.py
```

```text
Python 3.15.1 (GIL build)
  None, True, ()                     immortal: True
  small int 7                        immortal: True
  int 1000 (made at run time)        immortal: True
  int 5000 (made at run time)        immortal: False
  float 1.5 (made at run time)       immortal: False
  str '' (empty)                     immortal: True
  str made at run time               immortal: False
  sys.intern() of a new str          immortal: False
  sys.intern() of '__init__'         immortal: True
  a new list                         immortal: False
  a new dict                         immortal: False
refcount reported for None: 3221225472
```

Two lines differ between versions:
- **`int 1000`** is `False` on 3.14.9: the cache of preallocated small ints covers −5…256 on 3.14 and −5…1024 on 3.15 and 3.15t (probed with `int(str(n))` for every n up to 5000).
- **`sys.intern()` of a new str** is `True` on 3.15.1t: the free-threaded build makes every interned string immortal.

[`snippets/immortal-objects_2.py`](snippets/immortal-objects_2.py) shows the most surprising rule. It imports the same one-line module (`GREETING = "hello_from_my_module"`) in two fresh interpreters:

```text
first run (compiled from source)   literal immortal: False
second run (loaded from .pyc)      literal immortal: True
```

(Python 3.15.1; 3.14.9 is the same; 3.15.1t prints `True` twice.)

## How it really works

- **The mechanism (PEP 683).** Immortal objects carry a refcount value reserved to mean "immortal". `Py_INCREF` and `Py_DECREF` check for it and skip the write. `sys.getrefcount(None)` shows that value: `3221225472` on 3.14 and 3.15, `4294967295` on 3.13 (measured). Immortal objects are never deallocated.
- **What's immortal**, measured with `sys._is_immortal` on 3.14.9, 3.15.1 and 3.15.1t (3.13 has no `_is_immortal`):
  - always: `None`, `True`, `False`, `()`, `''`, and the small int cache (−5…256 on 3.14, −5…1024 on 3.15);
  - **strings interned by the interpreter itself**: identifiers it uses internally (`'__init__'`), and strings interned while **loading a `.pyc`** (observed with `immortal-objects_2.py`; the loading code is `Python/marshal.c`);
  - on **3.15t only**: every interned string, including `sys.intern()` at runtime and literals compiled from source, and the constants of compiled code (a `1.5` written in the source, tuple constants, big int constants), while the same values built at runtime (`float("1.5")`) stay mortal.
  - **never**: lists, dicts, sets, class instances, and any object your program builds at runtime that isn't one of the above.
- **Interning** means keeping one shared copy of a string, so equal strings are the same object. CPython interns identifiers and many literals automatically; `sys.intern(s)` does it on demand. Interned does *not* imply immortal on the GIL build: on 3.14.9 and 3.15.1, `sys.intern()` of a runtime string and a literal compiled from source both reported mortal.
- **Why the `.pyc` rule matters for experiments.** The *first* run of a program (no `.pyc` yet) and every later run can have different numbers of immortal strings, so memory numbers can differ between a cold and a warm start. spec/10 §3's warm-up runs, which are discarded, ensure `.pyc` files exist before the measured runs.
- **Containers are the limit.** A dict whose keys and values are all immortal is still mortal itself: iterating a list of such dicts writes each dict's refcount, and each dict's page gets copied. Immortality protects the leaves, not the structure.

## How Kiln uses it

- `src/kiln/labs/preload.py` builds an "immortal-heavy" graph: leaf values are small ints, `None`/`True`/`False` and short words, so experiment (b) can separate leaf refcount writes from container refcount writes. Per the measurements above, words interned with `sys.intern()` at runtime are **mortal** on the GIL builds; only the 3.15t run treats them as immortal.
- Gate G3.6 records which objects are immortal on each interpreter, from these snippets.
- Code tag: `CONCEPT(immortal-objects)`.

## Common misconceptions

- ❌ **Myth:** "immortal objects solve copy-on-read."
  ✅ **Reality:** only for the immortal objects themselves. Your app's data lives in mortal lists, dicts and instances, and touching those still copies pages.
- ❌ **Myth:** "interned strings are immortal."
  ✅ **Reality:** on 3.14.9 and 3.15.1 (GIL), runtime-interned strings and literals compiled from source are mortal; strings interned while loading a `.pyc` are immortal. On 3.15.1t, all interned strings are immortal.
- ❌ **Myth:** "a huge `sys.getrefcount` means the object is used a lot."
  ✅ **Reality:** `3221225472` is the immortal marker, not a count.

## Check yourself

1. Predict, on Python 3.15.1 (GIL): `sys._is_immortal(int("300"))`, `sys._is_immortal(int("3000"))`.
   <details><summary>Answer</summary>

   `True`, `False`. 3.15 preallocates −5…1024 as immortal small ints; 3000 is a new, mortal int. On 3.14, 300 would be mortal too.

   </details>
2. A forked worker iterates over a list of 1 million dicts whose values are all `None`. Are any pages copied?
   <details><summary>Answer</summary>

   Yes. Reading `None` doesn't write, but the list iteration references each dict, and dicts are mortal: each dict's refcount is written, so its page is copied.

   </details>
3. Why can the same program have more immortal strings on its second run than its first?
   <details><summary>Answer</summary>

   The first run compiles the source and writes a `.pyc`; literals interned during compilation are mortal on the GIL build. Later runs load the `.pyc`, and strings interned while unmarshalling it are made immortal.

   </details>
4. Why are immortal objects never freed, and why is that acceptable?
   <details><summary>Answer</summary>

   Their refcount never changes, so it never reaches 0. That's fine because they're a small, fixed set of objects that every program uses anyway (singletons, small ints, internal strings), so freeing them would save almost nothing.

   </details>

## Go deeper

- [PEP 683: Immortal Objects, Using a Fixed Refcount](https://peps.python.org/pep-0683/), whose motivation section describes the Instagram problem.
- [`sys._is_immortal`](https://docs.python.org/3.15/library/sys.html#sys._is_immortal) (3.14+).
- CPython source: `Include/refcount.h` (`_Py_IsImmortal`, `_Py_IMMORTAL_REFCNT`), `Objects/unicodeobject.c` (interning), `Python/marshal.c` (how `.pyc` strings are interned).
