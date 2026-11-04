# `tracemalloc` basics

> **First used in:** see the [ledger](README.md) (milestone K3; K6 goes deep) · **Needs:** [modules-and-imports](modules-and-imports.md) · **Reading time:** ~5 min
> **Terms introduced:** **`tracemalloc`**, **`tracemalloc.get_traced_memory`**

## What it looks like

```python
import tracemalloc

tracemalloc.start()                                  # begin recording Python allocations
data = [str(i) * 10 for i in range(100_000)]
current, peak = tracemalloc.get_traced_memory()      # bytes allocated now, and the peak
tracemalloc.stop()                                   # stop recording (and free its records)
```

## What it means

- **`tracemalloc`** hooks CPython's memory allocators and records every allocation made *through them* while tracing is on: the size, and where in the Python code it happened.
- `tracemalloc.get_traced_memory()` returns `(current, peak)` in bytes: memory allocated since `start()` and not yet freed, and the highest that value reached.
- `tracemalloc.stop()` ends tracing and throws the records away. `is_tracing()` tells you whether it's on.
- (K6 covers snapshots, `compare_to` and per-line statistics, for finding leaks.)

## Why people use it

To measure how much memory a piece of Python code allocates, independently of the process's RSS (which includes the interpreter, libraries and memory Python keeps for reuse; see [memory-metrics](../concepts/memory-metrics.md)).

## How Kiln uses it

- `src/kiln/labs/preload.py`: `build_graph(mb, ...)` starts tracing just for the build and keeps appending records until `get_traced_memory()[0]` reaches the target size, then stops. The result's `traced_bytes` says how big the graph really is (K3 spec §5.3).

## Gotchas

- **Tracing is slow and uses memory** for its own records. Start it only around what you measure, and stop it afterwards.
- **It only sees allocations made through Python's allocators.** Memory a C library gets with its own `malloc` is invisible; Memray (K6) sees it.
- **Allocations from before `start()` aren't counted**, even if they're freed later.
- **It's not RSS.** `current` going back down doesn't mean the process returned memory to the OS.

## Try it

```text
>>> import tracemalloc
>>> tracemalloc.start()
>>> before, _ = tracemalloc.get_traced_memory()
>>> data = [str(i) * 10 for i in range(100_000)]
>>> current, peak = tracemalloc.get_traced_memory()
>>> round((current - before) / 2**20, 1)
9.3
>>> del data
>>> current2, peak2 = tracemalloc.get_traced_memory()
>>> round(current2 / 2**20, 2), round(peak2 / 2**20, 1)
(0.0, 9.4)
>>> tracemalloc.stop()
>>> tracemalloc.is_tracing()
False
```

(Generated from [`snippets/tracemalloc-basics.repl`](snippets/tracemalloc-basics.repl) on Python 3.15.1.)

100,000 strings plus the list took 9.3 MiB of traced allocations. After `del data`, `current` dropped back to about 0, but `peak` remembers the high point.

## Check yourself

1. Why does `build_graph` stop tracing as soon as the graph is built?
   <details><summary>Answer</summary>

   Tracing slows every allocation and uses extra memory for its records. Leaving it on would distort the memory measurements the lab is about to make.

   </details>
2. A C extension allocates 50 MiB with its own `malloc`. Does `get_traced_memory()` show it?
   <details><summary>Answer</summary>

   No. tracemalloc only sees allocations made through Python's allocators. RSS shows it; Memray (K6) can attribute it.

   </details>
