# The `gc` module

> **First used in:** see the [ledger](README.md) (milestone K3) · **Needs:** [modules-and-imports](modules-and-imports.md) · **Reading time:** ~8 min
> **Terms introduced:** **`gc.collect`**, **`gc.set_threshold`**, **`gc.freeze`**, **`gc.callbacks`**, **`gc.get_stats`**

## What it looks like

```python
import gc

gc.disable()            # no automatic collections (until someone calls gc.enable())
gc.set_threshold(0)     # no automatic collections, even after gc.enable()
found = gc.collect()    # run a full collection now, whatever the settings
gc.freeze()             # move every tracked object to the permanent generation
```

## What it means

The `gc` module controls CPython's **cycle collector**, the part that frees groups of objects that refer to each other ([garbage-collector](../concepts/garbage-collector.md)). Ordinary objects are freed by reference counting and don't need it.

| Function | What it does |
|---|---|
| `gc.isenabled()`, `gc.enable()`, `gc.disable()` | read or flip the "automatic collections" flag |
| `gc.get_threshold()`, `gc.set_threshold(t0, t1, t2)` | when automatic collections trigger; `t0 = 0` means never |
| `gc.get_count()` | the counters compared with the thresholds |
| `gc.collect(generation=2)` | run a collection now (even when disabled); returns how many unreachable objects it found |
| `gc.get_stats()` | one dict per generation: `collections`, `collected`, `uncollectable`, and on 3.15 also `candidates` and `duration` |
| `gc.freeze()`, `gc.unfreeze()`, `gc.get_freeze_count()` | move tracked objects into / out of the permanent generation ([gc-freeze](../concepts/gc-freeze.md)) |
| `gc.is_tracked(obj)` | whether the collector tracks this object |
| `gc.callbacks` | a list of functions called as `cb(phase, info)` with `phase` `"start"` or `"stop"` around every collection |

## Why people use it

- To keep forked workers from copying shared memory (Instagram's `set_threshold(0)`, the `freeze` recipe).
- To control *when* pauses happen: disable automatic collections in a latency-critical section, then `gc.collect()` at a quiet moment.
- To observe the collector: `get_stats`, `get_count`, `callbacks` (K7 builds a pause histogram from callbacks).

## How Kiln uses it

- `src/kiln/memory/gcmodes.py` maps `worker.gc_mode` (`default`, `disabled`, `threshold0`, `freeze`) to these calls.
- `src/kiln/labs/tasks.py`: `labs.gc_collect` calls `gc.collect(generation)`, `labs.gc_stats` reports `isenabled`, `get_threshold`, `get_count`, `get_stats` and `get_freeze_count`.
- `src/kiln/memory/gcwatch.py` appends a `gc.callbacks` function to notice collections that shouldn't happen.

## Gotchas

- **`gc.disable()` isn't sticky.** Any library can call `gc.enable()`. Only `set_threshold(0)` survives that ([global-interpreter-state](../concepts/global-interpreter-state.md)).
- **`gc.collect()` ignores `disable()`.** It's an explicit request, and it runs.
- **`gc.callbacks` doesn't report enable/disable calls**, only collections (errata E-14).
- **The thresholds differ between versions and builds.** 3.13.17, 3.15.1 and 3.15.1t all start at `(2000, 10, 10)` (measured); older versions used 700. Read them with `get_threshold()` rather than assuming.
- **`gc.collect()`'s return value** is the number of *unreachable objects found*, not memory freed.

## Try it

```text
>>> import gc
>>> gc.isenabled()
True
>>> gc.get_threshold()
(2000, 10, 10)
>>> gc.get_count()
(1223, 6, 0)
>>> gc.collect()
0
>>> gc.get_count()
(33, 0, 0)
>>> gc.disable()
>>> gc.isenabled()
False
>>> gc.collect(0)
0
>>> gc.enable()
>>> gc.set_threshold(0)
>>> gc.isenabled(), gc.get_threshold()
(True, (0, 10, 10))
>>> gc.set_threshold(2000, 10, 10)
>>> starts = []
>>> gc.callbacks.append(lambda phase, info: phase == "start" and starts.append(info["generation"]))
>>> gc.collect(1)
0
>>> starts
[1]
>>> gc.callbacks.clear()
>>> gc.freeze()
>>> gc.get_freeze_count() > 0
True
>>> gc.unfreeze()
>>> gc.get_freeze_count()
0
>>> len(gc.get_stats()), sorted(gc.get_stats()[0])
(3, ['candidates', 'collected', 'collections', 'duration', 'uncollectable'])
```

(Generated from [`snippets/gc-module.repl`](snippets/gc-module.repl) on Python 3.15.1.)

`gc.collect(0)` ran although the GC was disabled. After `set_threshold(0)`, `isenabled()` is `True` but the first threshold is 0, so automatic collections never trigger. The callback saw one collection start, of generation 1.

## Check yourself

1. After `gc.disable()`, does `gc.collect()` do anything?
   <details><summary>Answer</summary>

   Yes. `disable()` stops only *automatic* collections; an explicit `collect()` always runs.

   </details>
2. Which call keeps automatic collections off even if a library later calls `gc.enable()`?
   <details><summary>Answer</summary>

   `gc.set_threshold(0)`. Enabling doesn't change the threshold, and a first threshold of 0 never triggers.

   </details>
3. You want to log every collection with its generation. Which part of `gc` do you use?
   <details><summary>Answer</summary>

   `gc.callbacks`: append a function `cb(phase, info)`; on `phase == "start"`, `info["generation"]` says which generation is being collected.

   </details>
