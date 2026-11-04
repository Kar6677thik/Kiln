# Lab K3: Copy-on-write, "copy-on-read", and the four GC strategies (Instagram)

> **Milestone:** [K3](../milestones/K3-copy-on-write.md) · **Incident:** research §2.1 (Instagram: the GC breaks copy-on-write sharing), errata E-03, E-15, E-21–E-23 · **Status:** final · **Commit:** the final commit of `milestone/k3-cow` (measured on top of `c68b227`) · **Run:** 2026-11-04, Python 3.15.1 (primary), 3.13.17 and 3.15.1t (secondary)

## 1. The incident

Instagram ran Django behind a pre-fork server. The master loaded the framework, then forked many workers that were supposed to *share* that memory through copy-on-write ([concepts/copy-on-write.md](../concepts/copy-on-write.md)). Within seconds of serving, each worker's shared memory fell from about 250 MB to 140 MB. Instagram called it "copy-on-read": in CPython, *using* an object writes its reference count, and the garbage collector writes into every container it scans. They disabled the GC, found that a library (msgpack) switched it back on, moved to `gc.set_threshold(0)`, and skipped the final collection at exit with `atexit.register(os._exit, 0)`. That gave about 10% more capacity and about 8 GB less RAM per machine (research §2.1). Python 3.7 later added `gc.freeze()`.

This lab rebuilds that situation in Kiln. The master preloads a 300 MiB object graph shaped like framework data, forks 4 workers, and we measure, from outside, how much of it each worker still shares as it reads the data and as the GC runs. We do this under each of Kiln's four `worker.gc_mode` strategies. Then we check which object types escape the problem (immortal objects), whether our detector catches a library that re-enables the GC, and what the shutdown trick saves.

## 2. Your prediction

> ✍️ **Learner: fill this in BEFORE reading further.**
> (a) Right after fork, how much of the 300 MiB graph does each worker share? After each worker *reads* the whole graph once, how much? Does `gc.freeze()` change that answer? What do the GC phases add, per mode?
> (b) If the graph's leaf values are immortal objects (small ints, None, string literals), does reading still copy pages? How much less?
> (c) Which detector layer catches a library calling `gc.enable()` in Python, the C function `PyGC_Enable()` at import, and `PyGC_Enable()` later from a function call?
> (d) How long does a spawned worker holding 300 MiB take to shut down, with and without `os._exit` from the last atexit handler?
>
> _(your answer here)_

### Implementer's hypothesis

- **(a)**
  - **P0:** every fork variant shares nearly all graph pages.
  - **P1/P2:** reading copies pages roughly in proportion to the fraction read, **in every mode**, `freeze` included: reading writes refcounts (E-15).
  - **P3 (churn):** `default` runs automatic gen-2 collections that scan the graph and copy more. `disabled`/`threshold0` collect nothing. `freeze` collects, but skips the frozen graph.
  - **P4 (explicit collect):** every mode except `freeze` scans and copies the rest.
  - **Spawn control:** nothing shared at any point, and the highest Σ PSS.
  - **3.15t:** "unknown; to be explained" (biased refcounting, a different GC).
- **(b)** Less copying than mortal, but not zero: the dicts and lists holding the leaves are still mortal.
- **(c)** `python` mode: shim (A), import flip (B) and runtime (C). `capi`: B and C (and gdb). `runtime`: only C (and gdb). Under `threshold0`, churn produces 0 automatic collections in every case.
- **(d)** The trick skips the final collection and module teardown, so shutdown is much faster; the app's atexit handlers still run. For forked workers it changes nothing (ADR-028).

## 3. Verified facts

| # | Claim (source) | Command run | Observed | ✅/❌ |
|---|---|---|---|---|
| G3.1 | Today's msgpack calls `gc.enable()` at import or in `unpackb` (E-03) | `./scripts/dev.sh lab k3-msgpack` + the same on every tag | msgpack 1.2.4: `isenabled()` stays False after import and after `unpackb`, on 3.13, 3.14, 3.15, 3.15t ([record](../../results/published/K3/msgpack-gate/20261104T105325Z-py3.15-7993.json)) → the lab uses `labs/k3/rogue_gc_enabler.py` | ❌ (fallback) |
| G3.2 | `gc.set_threshold(0)` disables automatic collection on 3.13–3.15 | a `gc.callbacks` counter around 200k cycles; `test_gcmodes.py::test_threshold0_means_no_automatic_collections` | 0 collections on 3.13, 3.14, 3.15, 3.15t | ✅ |
| G3.3 | `gc.freeze()`/`gc.get_freeze_count()` work on 3.15 and 3.15t | `labs.gc_stats` after freeze (`test_gcmodes.py`) | frozen > 0 in fork/forkserver workers on both | ✅ |
| G3.4 | An explicit `gc.collect()` runs while the GC is disabled | probe script; `test_explicit_collect_runs_while_disabled` | `gc.collect()` returned 7 on 3.15 with `isenabled()` False | ✅ |
| G3.5 | Our children's smaps_rollup is readable with `ptrace_scope=1` | `kiln mem --children-of`, the sampler | readable (same user, direct children); no sysctl change needed | ✅ |
| G3.6 | Which objects are immortal on 3.15 | `sys._is_immortal` probes ([concepts/immortal-objects.md](../concepts/immortal-objects.md)) | small ints −5…**1024** on 3.15 (−5…256 on 3.14); None/True/False/`()`/`''`; literals loaded from `.pyc`; runtime-built str, lists, dicts: mortal. `sys.intern()` at runtime is mortal on GIL builds, immortal on 3.15t ([E-22](../research/errata.md#e-22-which-strings-are-immortal-depends-on-the-version-and-on-how-they-were-made)) | ⚠️→adjusted |
| G3.7 | Cython code calls `gc.enable` through the module attribute | — | not verified: no Cython culprit was found (G3.1) and Cython isn't installed; stated as a documented claim in the primer | — |
| G3.8 | `ctypes.pythonapi.PyGC_Enable` exists | `hasattr(ctypes.pythonapi, "PyGC_Enable")` | True on all four tags; it returns the previous state | ✅ |
| G3.9 | 300 MiB × 4 workers fits the budget | the driver's pre-check (`300 × 2 × 5 + 512` MiB) | 3,512 MiB needed, ~6,900 MiB available | ✅ |
| G3.10 | THP doesn't distort sharing | `cat /sys/kernel/mm/transparent_hugepage/enabled` | `always [madvise] never`: only regions that ask for huge pages get them; CPython's allocator doesn't ask | ✅ |
| new | tracemalloc can size the graph without side effects | `labs/k3/preload_graph.py --mb 300` | ❌ tracing the whole build grew the process by 817 MiB; sampled sizing → 347 MiB ([E-21](../research/errata.md#e-21-tracemallocs-own-bookkeeping-can-dwarf-what-it-measures)) | ❌→fixed |
| new | A module-level `__del__` proves finalization ran | `test_exit_trick.py` | ❌ not reliable at all ([E-23](../research/errata.md#e-23-__del__-during-module-teardown-is-not-a-reliable-finalization-ran-probe)); replaced by a garbage cycle made at exit | ❌→fixed |

## 4. Setup

- **Machine:** WSL2 Debian 13, kernel 6.6.87.2-microsoft-standard-WSL2, 14 CPUs, 7.2 GiB RAM visible to WSL, page size 4096, THP `madvise`.
- **Interpreters:** CPython 3.15.1 (GIL) primary; 3.13.17 and 3.15.1t secondary.
- **Code location:** the repo on `/mnt/c` (9P). Memory isn't affected by where the code lives, and no timing here depends on imports (`source=9p` in every record).
- **The graph** (`labs.preload_mb = 300`, seed 0, `mortal`): about 242,000 records (dicts with an int ≥ 2⁴⁰, a 16-character str, 4 tag strs, 8 floats, a 3-key meta dict, and a shared `Owner` instance per 10 records): 482k dicts, 506k lists, 1.54 M str, 1.93 M floats, 0.72 M ints. It is built in 2.3–3.2 s and grows the master by 347 MiB RSS (`labs/k3/preload_graph.py`).
- **Commands:**

```bash
(wsl) $ ./scripts/dev.sh lab k3-msgpack                                   # gate G3.1
(wsl) $ ./scripts/dev.sh lab k3-cow --repeats 5 --methods fork            # (a) 4 modes
(wsl) $ ./scripts/dev.sh lab k3-cow --repeats 5 --methods spawn --modes default
(wsl) $ ./scripts/dev.sh lab k3-cow --repeats 3 --methods forkserver --modes default freeze
(wsl) $ KILN_PY=3.13  ./scripts/dev.sh lab k3-cow --repeats 3 --modes default freeze
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh lab k3-cow --repeats 3
(wsl) $ ./scripts/dev.sh lab k3-composition --repeats 5                   # (b)
(wsl) $ ./scripts/dev.sh lab k3-culprit                                   # (c)
(wsl) $ ./scripts/dev.sh lab k3-exit --repeats 10                         # (d)
```

The driver is [`labs/k3/k3_lab.py`](../../labs/k3/k3_lab.py); the time series comes from [`labs/k3/sampler.py`](../../labs/k3/sampler.py), a separate process reading `/proc` every 200 ms.

## 5. Method

| Experiment | Varied | Measured | Fixed | Repeats |
|---|---|---|---|---|
| (a) cow | `gc_mode` × start method × interpreter | per worker after each phase: Shared, Private_Dirty, PSS, USS, RSS; Σ PSS and Σ RSS over all processes; GC collections per generation (`labs.gc_stats`) | 300 MiB mortal graph, 4 workers, seed 0 | 5 per primary variant (3.15 fork × 4 modes, spawn control); 3 per secondary |
| (b) composition | composition × {default, freeze} | the same, phases P0–P2 | 3.15, fork | 5 |
| (c) culprit | rogue mode × {disabled, threshold0} | `gc_reenabled` events per layer and process; automatic collections during churn in each worker | 3.15, fork, 2 workers, `labs.gc_watch = true` | 1 (deterministic) |
| (d) exit | `exit_via_os_exit` | `shutdown_finished duration_ms`; did the app's atexit handler run; did the final collection run | 3.15, spawn, 1 worker with its own 300 MiB graph | 10 |

- **Phases (a):** P0 all workers ready + 1 s; P1 each worker ran `labs.touch_preloaded(0.25)`; P2 `touch_preloaded(1.0)`; P3 `labs.churn(n=200000)`; P4 `labs.gc_collect(2)`. "Each worker ran it" is checked from the returned pids (K3 §5.9); each lab task holds its worker 300 ms after the measured work so a burst reaches every worker (ADR-036 §3). After each phase: 1 s settle, then a snapshot from outside the server.
- **Statistics:** per run, the median of the 4 workers; per variant, the median over runs with a 95% bootstrap CI (2,000 resamples, seed 2). spec/10 §3 allows ≥ 5 repeats for memory if the spread is tiny: the CV of Σ PSS was ≤ 0.22% in every primary cell, so it is. GC counters are cumulative and include the collections the master ran before forking (3 gen-2 collections under `default`).
- **Order:** variants round-robin inside each repeat; a fresh server per run.

## 6. Results: before

"Before" is the Instagram situation: `default` mode, nothing done about the GC. 3.15, fork, medians of 5 ([records](../../results/published/K3/cow/)):

| phase | shared/worker MiB | private dirty/worker MiB | Σ PSS MiB | Σ RSS MiB |
|---|---|---|---|---|
| P0 ready | 373.8 | 3.4 | 389 | 1,890 |
| P2 read everything once | 108.5 | 268.9 | 1,451 | 1,891 |
| P3 + application garbage | 102.1 | 275.5 | 1,477 | 1,892 |

Each worker started out sharing 374 MiB and, after merely *reading* the data once, shared 109 MiB. The machine's real usage (Σ PSS) went from 389 MiB to 1,451 MiB, 3.7×. **Σ RSS didn't move** (1,890 → 1,892 MiB): RSS counts a page whether it is shared or copied, so the tool most dashboards show can't see this incident at all.

## 7. The fix

There's no fix for reading. The fixes are for the GC part, and the table in §8 compares them: `disabled` and `threshold0` (Instagram's), and `freeze` (the `gc` docs' recipe: disable early in the parent, freeze right before fork, enable early in the child). Building K3 also needed two fixes in Kiln, both found by measurements:

1. **`freeze` must freeze once per process.** The first version froze before *every* fork. On 3.15t, `gc.freeze()` sets a bit in every object's header (`visit_freeze`: `op->ob_gc_bits |= _PyGC_BITS_FROZEN`), so freezing again rewrote pages already shared with earlier workers. Those three workers started with 62.3 MiB of private copies each, the last-forked one with 3.9 MiB ([records](../../results/published/K3/cow-refreeze-before-fix/), ADR-036 §12). On the GIL builds the four workers were identical (3.3–3.4 MiB), because there freeze only splices lists.
2. **Sizing the graph without tracing all of it** (E-21), so the master doesn't carry ~500 MiB of tracemalloc leftovers into every fork.

## 8. Results: after

### (a) Sharing per GC mode (3.15, fork, 4 workers, medians of 5; [records](../../results/published/K3/cow/))

| mode | phase | shared/worker MiB (95% CI) | private dirty/worker MiB | Σ PSS MiB | gen-2 collections (cumulative) |
|---|---|---|---|---|---|
| default | P0 | 373.8 [373.7, 373.9] | 3.4 | 389 | 3 |
| default | P1 | 307.4 [307.3, 307.5] | 69.9 | 655 | 3 |
| default | P2 | 108.5 [108.4, 108.6] | 268.9 | 1,451 | 3 |
| default | P3 | **102.1** [102.0, 102.2] | 275.5 | 1,477 | 4 |
| default | P4 | 102.1 [102.1, 102.2] | 275.5 | 1,477 | 6 |
| disabled | P0 | 373.9 [373.9, 374.0] | 3.3 | 388 | 0 |
| disabled | P2 | 108.7 [108.6, 108.7] | 268.8 | 1,451 | 0 |
| disabled | P3 | 108.7 [108.6, 108.7] | **305.5** | 1,598 | 0 |
| disabled | P4 | **102.1** [102.0, 102.1] | 278.5 | 1,490 | 2 |
| threshold0 | P0 | 374.1 [374.0, 374.3] | 3.3 | 389 | 0 |
| threshold0 | P2 | 108.8 [108.7, 109.0] | 268.8 | 1,451 | 0 |
| threshold0 | P3 | 108.8 [108.7, 109.0] | **305.6** | 1,599 | 0 |
| threshold0 | P4 | **102.2** [102.1, 102.4] | 278.5 | 1,490 | 2 |
| freeze | P0 | 373.9 [373.8, 374.2] | 3.3 | 389 | 0 |
| freeze | P1 | 307.6 [307.5, 307.9] | 69.8 | 655 | 0 |
| freeze | P2 | 108.6 [108.6, 108.9] | 268.8 | 1,451 | 0 |
| freeze | P3 | **108.6** [108.5, 108.8] | 269.1 | 1,452 | 1 |
| freeze | P4 | **108.6** [108.5, 108.9] | 269.1 | 1,453 | 3 |
| spawn control (default) | P0…P4 | 11.3 → 11.5 | 366.9 → 367.1 | 1,490 → 1,491 | 3 → 6 |

(P1 for disabled/threshold0 is 307.6/307.8 MiB shared and 69.8 MiB private, the same as the others.)

**Secondary variants** (medians of 3; shared/worker MiB at P0 → P2 → P3 → P4):

| variant | P0 | P2 | P3 | P4 |
|---|---|---|---|---|
| 3.15 forkserver, default | 370.7 | 105.8 | 100.5 | 100.5 |
| 3.15 forkserver, freeze | 370.7 | 105.9 | 105.8 | 105.8 |
| 3.13 fork, default | 370.7 | 106.9 | 101.2 | 101.4 |
| 3.13 fork, freeze | 371.1 | 107.0 | 106.9 | 107.1 |
| 3.15t fork, default | 376.2 | 125.5 | 118.8 | 118.9 |
| 3.15t fork, disabled | 378.2 | 127.9 | 127.8 | 118.6 |
| 3.15t fork, threshold0 | 378.4 | 128.0 | 128.0 | 118.8 |
| 3.15t fork, freeze (after the fix) | 378.3 | 128.0 | 127.0 | 127.1 |

### (b) Composition (3.15, fork, medians of 5; [records](../../results/published/K3/composition/))

| composition | mode | P0 private dirty | P1 private dirty | P2 private dirty | Δ P2 vs mortal |
|---|---|---|---|---|---|
| mortal (242k records) | default | 3.3 | 69.9 | 268.8 | — |
| mortal | freeze | 3.3 | 69.8 | 268.8 | — |
| immortal-heavy (442k records) | default | 3.3 | 39.8 | 148.4 | −120.4 MiB (−45%) |
| immortal-heavy | freeze | 3.3 | 39.8 | 148.4 | −120.4 MiB (−45%) |

(MiB per worker. Both graphs are 300 MiB by tracemalloc; the immortal-heavy one shares 337 MiB per worker at P0, against 374 MiB for the mortal one.)

### (c) Culprit detection (3.15, fork, 2 workers; [records](../../results/published/K3/culprit/))

| culprit | mode | A shim | B import flip | C poll | C sentinel | gdb (D) | automatic collections during churn, per worker |
|---|---|---|---|---|---|---|---|
| `python` (`gc.enable()` at import) | disabled | ✅ master | ✅ master: `rogue_gc_enabler` | ✅ master | ✅ master | not measured | 0, 0 |
| `python` | threshold0 | ✅ master ("harmless under threshold0") | — | — | — | not measured | 0, 0 |
| `capi` (`PyGC_Enable()` at import) | disabled | — | ✅ master: `rogue_gc_enabler` | ✅ master | ✅ master | not measured | 0, 0 |
| `capi` | threshold0 | — | — | — | — | not measured | 0, 0 |
| `runtime` (`PyGC_Enable()` inside a task) | disabled | — | — | ✅ both workers | ✅ both workers | not measured | **193, 193** |
| `runtime` | threshold0 | — | — | — | — | not measured | 0, 0 |

Every layer fired exactly where §5.8 predicted. gdb (layer D) wasn't demonstrated: gdb isn't installed (`sudo apt install gdb` needs the learner's password).

### (d) The shutdown trick (3.15, spawn, 1 worker with a 300 MiB graph, 10 repeats; [records](../../results/published/K3/exit/))

| method | `exit_via_os_exit` | shutdown ms, median [95% CI] | app atexit handler ran | final collection ran |
|---|---|---|---|---|
| spawn | false | 817.2 [578.9, 850.3] | 10/10 | 10/10 |
| spawn | true | **11.4** [10.9, 13.7] | 10/10 | 0/10 |
| fork / forkserver | either | not measured: a no-op (ADR-028); `test_exit_trick.py::test_forked_workers_skip_both_anyway` shows neither ran | 0/1 | 0/1 |

What a final collection *would* cost a forked worker, read from lab (a)'s P3 → P4 step (an explicit `gc_collect(2)`): under `disabled`/`threshold0` it lowered shared memory by 6.6 MiB per worker (108.7 → 102.1); under `freeze`, by 0.0 MiB; under `default`, by 0.0 MiB, because the automatic gen-2 collection during churn had already done that damage.

## 9. Analysis

- **Reading alone did most of the damage, and no GC mode prevents it.** In every mode, reading the whole graph once took each worker from 374 MiB to 109 MiB shared and copied 265 MiB into it (P2). P1 copied 66 MiB for 25% of the records, almost exactly proportional. `freeze` produced the same P1 and P2 numbers to 0.3 MiB. This is the lesson of E-15 in data: on the GIL build every read is a refcount write, and freezing only stops the *collector* from scanning.
- **The GC part is real but small next to reading, and each mode handles it as predicted.** After P2, `default`'s automatic gen-2 collection during churn copied another 6.4 MiB per worker (108.5 → 102.1). `disabled` and `threshold0` ran no collections, so they lost no sharing at P3. But the 200,000 garbage cycles weren't freed either: private memory grew by 37 MiB per worker (305.5 MiB). The explicit `gc.collect(2)` in P4 then copied the same 6.6 MiB that `default` lost at P3 and freed the garbage. Only `freeze` kept its 108.6 MiB through both GC phases, while still collecting its own garbage (gen-2 count 0 → 3). The forkserver and 3.13 results show the same shape.
- **So `freeze` is the right mode, but it protects only the 30% that reading left shared.** In this workload that's 6.5 MiB per worker. A real server whose requests read *part* of the preloaded data would gain more, because more stays shared for the collector to ruin.
- **Instagram's shape is reproduced.** Their workers' shared memory fell from about 250 to 140 MB within seconds of serving. Ours fell from 374 to 109 MiB after one read of everything. Their gain came from stopping the collector part, the part our P3/P4 rows show.
- **Immortal leaves cut the copying by 45%, not 100%** (lab b). The leaves (small ints, None/True/False, string literals from a `.pyc`) were never written. The 148 MiB still copied are the dicts, lists and `Owner` objects holding them, whose refcounts and headers *are* written. Immortality helps exactly as far as it reaches, and containers are never immortal.
- **3.15t: "unknown" explained in part.** Its workers copied a little less on reading: 254 MiB at P2 vs 269 MiB, which is 68% vs 72% of what they shared at P0. Its graph holds 18% fewer records for the same 300 MiB, because its objects are bigger. Reads still write. The likely reason, which we didn't verify: the free-threaded build's *biased* reference counting only avoids atomic writes for the thread that owns an object, and a forked worker's thread is not the owner. Its collector has no generations and scans the whole heap (its automatic collections are all counted as gen 0). `default` lost 6.7 MiB at P3, like the GIL build. The re-freeze bug of §7 was the surprise: freezing is an O(objects) write on 3.15t, but an O(1) list splice on the GIL build.
- **The detector's layers caught each culprit exactly as designed** (lab c). An import-time flip was caught *in the master*: under fork the app is preloaded there, and the workers' own `gc.disable()` (`apply_child_early`) put them back in order, which is why their churn ran 0 collections. The runtime C-level flip was invisible to the shim and the import hook. Only the poll and the sentinel saw it, and they could tell *when* (the job running), not *who*, which is where gdb (layer D) would take over. Under `threshold0` none of the three culprits caused a single automatic collection: Instagram's reason for switching from `gc.disable()`.
- **The exit trick cut a 300 MiB spawned worker's shutdown from 817 ms to 11 ms** (lab d), about 72× faster, with the app's atexit handler still running every time. The time saved is interpreter finalization: the final collection plus tearing down about 5 million objects. For forked Kiln workers there was nothing to save: they never finalize (ADR-028).

## 10. Reference comparison

Read after building. Sources: CPython 3.15 `Python/gc.c` and `Python/gc_free_threading.c`, gunicorn `arbiter.py` (main branch), all fetched 2026-11-04.

1. **`gc_freeze_impl`/`_PyGC_Freeze` (GIL build)** loops over the generations and `gc_list_merge`s each one onto `permanent_generation.head`, setting each generation's `count` to 0. A merge relinks the two list ends, so it touches only a few list headers, whatever the number of objects: O(1)-ish. It has to happen *before* fork because the child must inherit the permanent generation. Frozen in the parent means "never scanned" in every child. Freezing in the child would be too late: the child's first collection would already have scanned (written) everything.
2. **What a collection writes.** `update_refs()` copies each object's refcount into its GC header (`gc_reset_refs(gc, Py_REFCNT(op))`). `visit_decref()` decrements it (`g->_gc_prev -= 1 << _PyGC_PREV_SHIFT`). `visit_reachable()`/`move_unreachable()` set it and rewrite `_gc_prev`/`_gc_next`. Every scanned container gets several writes, which is the "copy-on-read" of the collector part. (3.15's `gc.c` has no incremental collector: `gc_collect_main` does the whole generation in one pass. See K7 for the 3.14 incremental GC and its revert.)
3. **3.15t `gc_free_threading.c`** keeps no object lists: `gc_visit_heaps()` walks mimalloc's GC heaps during a stop-the-world pause. `freeze` is a heap walk that sets `_PyGC_BITS_FROZEN` in each object's `ob_gc_bits`, and later passes skip frozen objects. A collection repurposes `ob_tid` to hold `gc_refs` for every tracked, non-frozen object, then restores the thread id. That per-object write in `visit_freeze` is exactly why re-freezing dirtied pages (§7).
4. **gunicorn `--preload`.** With `preload_app`, `Arbiter.setup()` calls `self.app.wsgi()` in the master, and `spawn_worker()` forks afterwards, so workers inherit the loaded app, the same as Kiln's `app.preload` under fork. gunicorn's `arbiter.py` contains neither `gc.freeze` nor `gc.disable`: applying a GC recipe is left to the user's `pre_fork`/`post_fork` hooks.
5. **Instagram vs us.** They used uWSGI and a process-wide `gc.set_threshold(0)`. Our measurements match their *shape*: most sharing disappears just by serving (our P2), and stopping the collector protects what's left (our P3/P4 under threshold0 and freeze). Their 250 → 140 MB (−44%) is milder than our 374 → 109 MiB (−71%), because our workload reads *every* record, while a web request reads a fraction. Our P1 (25% read, −18%) is closer to theirs.

## 11. Limitations and threats to validity

- **A synthetic graph.** Real framework data has more types (classes with descriptors, code objects, which are immortal on some builds), so the proportions differ. The *direction* of every effect is what transfers.
- **Reading everything is a worst case.** P2 makes every worker read every record. Real requests touch less.
- **5 repeats (3 for secondaries)**, allowed by spec/10 §3 for memory because the spread is tiny (CV of Σ PSS ≤ 0.22% in every primary cell). The CIs are correspondingly narrow.
- **Measurement granularity.** Each snapshot comes 1 s after a phase. The 200 ms time series is in the records (`ts_worker_*`) but isn't analysed here; K9 plots it.
- **Layer D not demonstrated** (gdb missing). Lab (c) ran once per cell: the logic is deterministic, but the *timing* of a poll sighting isn't.
- **Lab (d) on spawn only, 1 worker.** `shutdown_ms` includes the master's own bookkeeping (a few ms).
- **The GIL-build `freeze` records were taken before the once-per-process fix** (ADR-036 §12). Their per-worker P0 numbers are identical (3.3–3.4 MiB private), showing re-freezing had no effect there.
- **WSL2, 9P source.** The memory accounting is the kernel's own and doesn't depend on the filesystem.

## 12. What you should now be able to explain

- [ ] What fork really copies (page tables), and when a shared page stops being shared (*Memory*)
- [ ] Why *reading* a Python object writes to its page on the GIL build, with the P2 numbers (*Runtime internals*)
- [ ] What a GC collection writes, and why `freeze` only protects against that part (*Runtime internals*)
- [ ] `gc.disable()` vs `set_threshold(0)` vs `freeze`, and the P3/P4 rows that tell them apart (*Runtime internals*)
- [ ] Why Σ RSS hides the whole incident and Σ PSS shows it (*Memory & perf*)
- [ ] What immortal objects save, and why containers limit it to 45% here (*Runtime internals*)
- [ ] Which detector layer sees which culprit, and why only gdb can name a C-level runtime culprit (*Runtime internals; data model*)
- [ ] What `os._exit` from the last atexit handler skips, and why forked Kiln workers don't need it (*Runtime internals*)

## 13. Checkpoint quiz

1. In the "before" table, Σ RSS stayed at about 1,890 MiB while Σ PSS rose from 389 to 1,451 MiB. Which number tells you the machine's real memory use, and why does the other one not move?
   <details><summary>Answer</summary>

   Σ PSS. PSS divides each shared page between its sharers, so the sum counts every physical page once. RSS counts every resident page of a process in full, shared or not. A page that gets copied was already counted in both the master's and the worker's RSS before the copy, so RSS can't see sharing being lost.

   </details>
2. `freeze` and `default` had the same shared memory after P2 (108.6 vs 108.5 MiB). What does `freeze` do, then?
   <details><summary>Answer</summary>

   It stops the *collector* from scanning the frozen graph, and so from writing GC headers. That's visible at P3/P4: `freeze` stays at 108.6 MiB while `default` drops to 102.1. Reading still writes refcounts, which freeze can't prevent.

   </details>
3. Under `disabled`, P3 shows private memory rising by 37 MiB per worker without losing any sharing. What is that memory?
   <details><summary>Answer</summary>

   The 200,000 garbage cycles `labs.churn` created. With no collections they are never freed, so they occupy new, private pages. The explicit collection at P4 freed them (305.5 → 278.5 MiB) and copied 6.6 MiB of shared pages while scanning.

   </details>
4. Why did the immortal-heavy graph still copy 148 MiB per worker on reading?
   <details><summary>Answer</summary>

   Only its leaf values are immortal. The dicts and lists that hold them, and the `Owner` objects, are ordinary objects whose refcounts are written on every access.

   </details>
5. In lab (c), the `capi` culprit was seen by layer B but not by layer A. Why?
   <details><summary>Answer</summary>

   Layer A replaces the Python attribute `gc.enable`. A call to the C function `PyGC_Enable()` (here through `ctypes.pythonapi`, as a C extension would do) never looks that attribute up. Layer B doesn't care *how* the GC was switched on: it compares `gc.isenabled()` before and after each module's import.

   </details>
6. The first `freeze` implementation left three of four 3.15t workers with 62 MiB of private memory before they did anything. What happened?
   <details><summary>Answer</summary>

   The master called `gc.freeze()` again before each fork. On 3.15t that sets a bit in every object's header, so the master *wrote* to pages it already shared with the workers forked earlier. Copy-on-write gave the master new copies, and each earlier worker was left as the only user of the old pages, which then counted as its private memory. The last worker forked after the last freeze, so it was unaffected.

   </details>
