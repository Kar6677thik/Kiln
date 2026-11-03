# K6 — Leak Lab: Planting, Finding and Fixing Memory Leaks in a Long-Running Server

> **Status:** 📝 specified · **Depends on:** K1 (workers, heartbeats with `rss_bytes`, `max_rss_mb` recycling), K3 (`worker.gc_mode`, `memory/procfs.py`, `memory/gcwatch.py`), K5 (gateway `/debug/*` relay) · **Branch:** `milestone/k6-leaks` · **Size:** M (6 steps)
> **Incident:** research §2.8 (Snyk/Fugue's "reflector" leaked through `requests`, found by diffing `tracemalloc` snapshots. Bloomberg built Memray to see native allocations) and §2.7 (Celery's `max-memory-per-child` as the operational band-aid)
> **Reference implementations (read *after* building):** [Memray docs](https://bloomberg.github.io/memray/), [Tachyon / `profiling.sampling` docs](https://docs.python.org/3.15/library/profiling.sampling.html), the CPython `tracemalloc` docs, the Snyk post

---

## 1. The story (plain English)

Python cleans up memory for you, so how can it leak? Python frees an object only when **nothing refers to it any more**. A "leak" in Python is almost always a **reference you forgot about**:

- a cache that never forgets
- a list of callbacks that only grows
- an object that resurrects itself in its own destructor
- a pile of reference cycles while the garbage collector is switched off

In a script that runs for a second, nobody notices. In a server that runs for weeks, memory creeps up until the machine runs out and the kernel kills the process. Research §2.7 tells exactly that story: Celery workers killed with only "worker lost" as a clue.

Snyk had one of these. Their "reflector" service slowly climbed from 8% to 20% memory. They found it by taking a **snapshot** of every live allocation (with `tracemalloc`) on each loop, **diffing** consecutive snapshots, and filtering by package. The growth pointed straight at the `requests` library, and switching to the stdlib `urllib` fixed it. Bloomberg hit leaks that `tracemalloc` couldn't see at all, because the memory was allocated by C code, so they built **Memray**, which tracks native allocations too.

Python 3.14 and 3.15 added two more tools. You can **attach a debugger to a running process** (`python -m pdb -p PID`, PEP 768). You can also **sample** a running process with almost no overhead using **Tachyon** (`python -m profiling.sampling`, PEP 799).

**In this milestone we will** plant five kinds of leak in Kiln workers on purpose (plus one native leak). We'll watch memory climb, use each tool to find the exact line responsible, fix each leak, and show the climb flatten. Along the way we bust a popular myth: *"objects with `__del__` in a cycle leak"*. That stopped being true in Python 3.4.

## 2. What you will learn

- **Explain** what a leak means in a garbage-collected language (unintended reachability) and list the common patterns. *(Runtime internals, Memory & perf)*
- **Use** `tracemalloc` snapshots and diffs to find the line that allocated the growing memory, and **explain** its overhead and its blind spot (native allocations). *(Memory & perf)*
- **Explain** the difference between a *tracing* profiler (sees everything, slows the program) and a *sampling* profiler (sees statistically, cheap), and read a flamegraph. *(Memory & perf)*
- **Explain** what PEP 768 remote attach does (`sys.remote_exec`, `pdb -p`), why it needs ptrace permission, and why profiler and target must be the same Python version and build. *(Testing & ops, Runtime internals)*
- **Explain** `weakref` and how it fixes registry-style leaks. *(Data model)*
- **Explain** PEP 442 (safe object finalization) and why `__del__` cycles are now collected. Also explain resurrection, which still leaks. *(Runtime internals)*
- **Compare** a real fix with recycling (`max_rss_mb`) as an operational band-aid. *(Processes)*

## 3. Entry criteria

- K1: heartbeats carry `rss_bytes`; `worker.max_rss_mb` recycling works.
- K3: `worker.gc_mode` (`default|disabled|threshold0|freeze`) works; `memory/procfs.py` reads RSS/PSS/USS.
- K5: the gateway relays `POST /debug/workers/<id>/<op>` to KP `debug`.
- `kiln doctor` reports ptrace status, plus `memray` installability via the `lab-k6` group (ADR-005).

## 4. Scope

### In scope
- Planted leak tasks in `src/kiln/labs/leaks.py`, registered under `labs.` when `labs.enabled`.
- Worker debug ops in `src/kiln/worker/debug.py`, plus master forwarding of KP `debug` (spec/02 §3.1) and the `kiln debug` CLI (spec/05 §4).
- Diagnosis walkthroughs with tracemalloc, Memray, Tachyon and `pdb -p`, all with real captured output.
- Fixes for each leak, and before/after memory slopes.
- The PEP 442 myth-vs-fact demo.

### Out of scope (and why)
- Reproducing Snyk's exact `requests` bug. We don't know its precise cause, so we don't pretend to (ADR-016). There's an optional pattern that is clearly labelled "modelled after".
- Leak detection in the master. The master runs no user code, though a stretch item covers it.
- Writing our own native-memory tracer. Memray is the reference tool here, used as-is.

## 5. Design

### 5.1 Planted leaks (`src/kiln/labs/leaks.py`)

**Location decision.** spec/01 §5 puts every `labs.*` task in the `kiln.labs` package, so workers can import them under every start method without touching `sys.path`. spec/08 §1 lists `labs/k6/leaky_tasks.py`. K6 follows **spec/01**: the code lives in `src/kiln/labs/leaks.py`, and `kiln/labs/tasks.py` registers it. `labs/k6/leaky_tasks.py` is **not** created. Step 2 corrects the spec/08 tree line to `labs/k6/ run_leaks.py, sample_rss.py, slope.py, diagnose_*.md`.

Every function carries `# LAB:` comments (documentation standard §4.3). Each leak has a **fixed twin** with the suffix `_fixed`, so the before and after runs use the same code shape.

| Task | Args | What leaks, and why | Fixed twin |
|---|---|---|---|
| `labs.leak_gc_disabled_cycles` | `n` (int), `kb` (int) | creates `n` two-object reference cycles (`a.other = b; b.other = a`), each holding a `kb` KiB `bytearray`, then drops them. With `worker.gc_mode = "disabled"` (K3) nothing ever collects cycles, so every job leaks `n × kb` KiB. With `gc_mode="default"` it doesn't leak, and that run is the **control** | `labs.leak_gc_disabled_cycles_fixed`: breaks the cycle explicitly (`a.other = None`) before returning, *or*, as the operational fix, the worker uses `gc_mode="freeze"` per K3's recipe, which re-enables collection in the child. Both are shown |
| `labs.leak_del_resurrection` | `n`, `kb` | objects whose `__del__` does `_GRAVEYARD.append(self)`. When their refcount hits 0, `__del__` runs and *re-references* the object, so it never dies. `_GRAVEYARD` is a module-level list | `_fixed`: `__del__` only logs, with no resurrection. Discussion: finalizers should never store `self` |
| `labs.leak_unbounded_cache` | `key` (str), `kb` | calls `_render(key)`, decorated `@functools.lru_cache(maxsize=None)`, which returns `bytes(kb*1024)`. Callers pass a **unique key per job** (the load generator sends `key=<job index>`), so the cache grows forever | `_fixed`: `@functools.lru_cache(maxsize=256)`, with the eviction explained |
| `labs.leak_closure_registry` | `kb` | defines `def on_event(): return len(payload)` closing over `payload = bytearray(kb*1024)`, and appends `on_event` to the module-level `_LISTENERS` list ("subscribe per request, never unsubscribe") | `_fixed`: registers a `weakref.WeakMethod`/`weakref.ref`-based listener **and** unsubscribes in `finally`. The doc explains both and why the weakref alone is enough here |
| `labs.leak_native` | `kb` | `ctypes.CDLL(None).malloc(kb*1024)` without `free`, the pointer discarded. **Invisible to tracemalloc**, visible in RSS and in Memray `--native` | `_fixed`: `free` in `finally` |
| `labs.leak_requests_session` *(optional, needs `lab-k6`)* | `url` | creates a new `requests.Session()` per job, mounts a custom `HTTPAdapter`, and stores it in a module dict keyed by `uuid4()` ("per-request client cached by mistake"). It's labelled **"modelled after the Snyk/Fugue story; not their actual bug"** | `_fixed`: a single module-level Session reused, closed on worker exit |
| `labs.cycle_with_del` | `n` | **The myth demo.** It creates cycles whose objects define `__del__` (no resurrection) and drops them. With the default GC these **are collected** (PEP 442, Python 3.4+), `gc.garbage` stays empty, and RSS stays flat | — |

Lab tasks return `{"live": <size of the relevant module-level container or cache_info()>}` so growth is visible in results too.

### 5.2 Debug ops (`src/kiln/worker/debug.py`)

**[spec/06 §5](../spec/06-observability.md#5-debug-operations-k6k7) is the authoritative op catalogue.** That covers names, params, reply shapes, the 5-snapshot limit, truncation, the master-side flow and the 10 s default reply timeout. K6 implements these K6-tagged ops exactly as written there: `tracemalloc_start`, `tracemalloc_snapshot`, `tracemalloc_diff`, `tracemalloc_stop`, `gc_stats`, `gc_collect`, `type_counts`, `stack_dump` and `memory`. If K3 didn't already provide `memory`, K6 provides it.

What K6 adds or pins down beyond spec/06 (step 1 copies each item into spec/06 §5, so the catalogue stays complete):

| Item | Definition |
|---|---|
| **New op `labs_containers`** | params: none. Reply `data`: `{"graveyard": len(_GRAVEYARD), "listeners": len(_LISTENERS), "sessions": len(_SESSIONS), "render_cache": _render.cache_info()._asdict()}`. Available only when `labs.enabled` (otherwise `BAD_REQUEST` "labs disabled") |
| **Default snapshot filters** | `tracemalloc_snapshot` applies `tracemalloc.Filter(False, tracemalloc.__file__)`, `Filter(False, "<frozen importlib._bootstrap>")`, `Filter(False, "<frozen importlib._bootstrap_external>")` and `Filter(False, <path of kiln/worker/debug.py>)`, so the tool doesn't report itself. The primer shows a diff with and without them |
| **`tracemalloc_diff` sorting** | `Snapshot.compare_to(old, key_type)` already sorts by absolute `size_diff`. Kiln re-sorts by **signed** `size_diff`, descending, so growth comes first. `include` globs are applied with `fnmatch` to each statistic's first traceback frame filename |
| **`tracemalloc_diff` with `to: "now"`** | takes a temporary snapshot (with the same filters) that isn't stored |
| **CLI rendering** | `kiln debug WORKER_ID OP [--params JSON]` pretty-prints `data`. For `tracemalloc_diff` it prints an aligned table: `size_diff (KiB) | count_diff | where`. For `type_counts` it prints `count | type` |

- **Where ops run:** this is unchanged from spec/06 §5. The main/control thread runs them between jobs, so under `sync` a request waits for the current job. `stack_dump` is served by the heartbeat thread, so it works while a job is stuck. The walkthrough demonstrates `stack_dump` on a worker running `labs.sleep ms=20000`.
- **Gateway:** `POST /debug/workers/<id>/<op>` with a JSON params body (spec/02 §5.2, built in K5) relays to the same ops.

### 5.3 Memory timeline sampler (`labs/k6/sample_rss.py`)

It follows spec/10 §7 ("sample from a separate process"). The sampler is its own process. Every **200 ms** it reads `/proc/<worker pid>/status` (`VmRSS`) and `/proc/<worker pid>/smaps_rollup` (PSS/USS) through `kiln.memory.procfs`, together with the master's `jobs.totals_since_start` from KP `stats` (spec/06 §3). It writes the `samples` plus parallel `t_s` and `jobs_done` metrics into the ExperimentRecord.

**The slope** (bytes per job, displayed as MiB per 1,000 jobs) is fitted by ordinary least squares of RSS against `jobs_done`, using `statistics.linear_regression` (stdlib, 3.10+). The fit lives in `labs/k6/slope.py`, about 15 lines with every line commented. The **first 10% of jobs is excluded** as warm-up (imports, first-allocation arenas), in line with spec/10 §8. The slope's CI comes from bootstrapping the per-repeat slopes (spec/10 §4.2).

### 5.4 Tool walkthroughs (`labs/k6/diagnose_tracemalloc.md`, `diagnose_memray.md`, `diagnose_tachyon.md`, `diagnose_pdb.md`; real output pasted; the lab report links to and summarizes them)

| Tool | How it's used on Kiln | What it shows | Blind spots |
|---|---|---|---|
| **tracemalloc** | `kiln debug 0 tracemalloc_start --params '{"frames":25}'` → load 1,000 jobs → `kiln debug 0 tracemalloc_snapshot --params '{"label":"base"}'` → load 5,000 more → `kiln debug 0 tracemalloc_diff --params '{"from":"base","to":"now","limit":10}'` | the top row is the leak's allocation line (`leaks.py:NN`) with size_diff ≈ jobs × kb | doesn't see `labs.leak_native`; overhead (measured in §7) |
| **Memray** | `memray run --follow-fork --native -o results/K6/memray/<leak>.bin -m kiln serve ... --workers 1` (gate G6.1), then `memray flamegraph`, `memray table`, `memray stats` on the worker's output file | native + Python allocation stacks; **catches `labs.leak_native`** | Linux/macOS only (we're in WSL, fine); large output files |
| **Tachyon** | `python -m profiling.sampling attach <worker pid>` (exact subcommand and flags per gate G6.3), capture during load, produce a flamegraph, and a **diff flamegraph** before/after a fix (`--diff-flamegraph`, research §00) | *time*, not memory. Use it to show that a leaky heap makes GC slower: the gen-2 collection frames grow in `leak_unbounded_cache` runs (with `gc_mode=default`), because a bigger live heap means more objects to traverse | can't see memory directly; same minor version + build as the target (research §2.8) |
| **pdb -p** | `python -m pdb -p <worker pid>` (gate G6.4), then `p len(kiln.labs.leaks._LISTENERS)`, `p kiln.labs.leaks._render.cache_info()`, `c` to detach | live inspection of a running worker *without restarting it* | needs ptrace; pauses the target while attached (so run it when idle, and note that jobs stall) |
| **`max_rss_mb` recycling** | `KILN_WORKER__MAX_RSS_MB=<baseline + 100>` with the unfixed leak | the sawtooth RSS graph: workers retire and get replaced, so memory stays bounded *without* fixing the bug | it hides the bug and costs reboots. That's Celery's `max-memory-per-child` |

### 5.5 Config keys used

`labs.enabled`, `debug.enabled`, `worker.gc_mode`, `worker.max_rss_mb`, `worker.memory_metric`, `master.workers`. No new keys.

## 6. Steps

1. **Debug ops + master forwarding + `kiln debug` CLI.**
   - *Docs first:* `concepts/tracemalloc.md`.
   - *Tests:* every K6 op in spec/06 §5 plus `labs_containers` returns its documented schema; at most 5 snapshots are kept; the diff's top row points at a test-planted allocation; `DEBUG_DISABLED` when off; debug requests issued while a sync job runs are answered after it finishes.
2. **Planted leaks + fixed twins + the myth demo.**
   - *Docs:* `concepts/memory-leaks-in-gc-languages.md`, `python/functools-lru-cache.md`, `python/weakref.md`, `python/del-and-finalizers.md`, `concepts/weakref.md`. Closures link to K2's closures note: `docs/python/closures.md` (if K2 didn't write it, K6 writes it).
   - *Tests* (fast versions, in-process with no server): each leak's container grows by exactly the expected count per call, and its fixed twin doesn't; `cycle_with_del` leaves `gc.garbage == []` and the objects are gone after `gc.collect()` (check with `weakref.ref`).
3. **RSS sampler + slope fitting + the lab driver** `labs/k6/run_leaks.py` (lab id `k6-leaks`).
4. **Tool walkthroughs:** tracemalloc, then Memray, then Tachyon, then pdb -p.
   - *Docs:* `concepts/sampling-vs-tracing-profilers.md`, `concepts/flamegraphs.md`, `concepts/remote-debugging-pep768.md`.
   - *Gates:* G6.1–G6.6.
5. **Before/after runs for each leak + the recycling band-aid run.**
6. **Lab report, code tour, reference comparison.**

## 7. Lab design

- **Question:** for each leak pattern, how fast does memory grow per job, which tool finds it fastest and most precisely, and does the fix bring the growth slope to zero?
- **Implementer's hypothesis:**
  - Every unfixed leak grows linearly with jobs, at about `kb` KiB per job plus per-object overhead (resurrection and closure carry extra object overhead; we'll measure it).
  - Fixed twins and the `gc_mode=default` control show a slope whose CI includes 0.
  - tracemalloc pinpoints the line for every leak except `leak_native`. Memray `--native` catches all of them.
  - Tracing with tracemalloc at `frames=25` costs noticeable throughput, and the sampling profiler costs little. We'll measure both.
- **Independent variables:**
  - leak ∈ {gc_disabled_cycles (under gc_mode=disabled), gc_disabled_cycles under gc_mode=default (control), del_resurrection, unbounded_cache, closure_registry, native, requests_session (optional)} × variant ∈ {leaky, fixed}
  - tool ∈ {none, tracemalloc(frames=1), tracemalloc(frames=25), Tachyon attached, Memray run} for the overhead table
- **Dependent variables:**
  - RSS slope (MiB / 1,000 jobs) with a CI
  - the final container sizes
  - tracemalloc's top-row location (and whether it is correct)
  - throughput (jobs/s) with each tool attached, compared with none
- **Controls:**
  - `master.workers=1` (one worker, so the slopes are clean)
  - the `sync` executor
  - `kb=64`, `n=10` (the cycles), 6,000 jobs per run at concurrency 8
  - same interpreter (3.15), run from ext4
  - `max_rss_mb=0`, except in the band-aid run
- **Repeats and statistics:** per [spec/10](../spec/10-measurement-methodology.md).
- **Procedure:** `./scripts/dev.sh lab k6-leaks [--leak NAME]` runs `labs/k6/run_leaks.py`. For each cell it starts the server with the cell's env, starts the sampler, runs `kiln bench load --jobs 6000 --concurrency 8 --task labs.<leak> --args/--kwargs ...` (unique key per job for `unbounded_cache`: the load generator supports a `{i}` placeholder in `--kwargs`, or step 3 adds it), collects `labs_containers`, stops the server, fits the slope and writes the records.
  - **Note:** `kiln bench load` needs `--kwargs` and a per-job `{i}` substitution. If K1/K4 didn't add them, step 3 adds them (an additive change to spec/05 §4).
- **Result tables** (headers only):
  - *Leak slopes:* `leak | variant | gc_mode | slope MiB/1k jobs [95% CI] | final container size | verdict (leaks / flat)`
  - *Who found it:* `leak | tracemalloc top row (file:line) | correct? | Memray shows it? | Tachyon signal | pdb -p evidence`
  - *Tool overhead:* `tool | jobs/s [CI] | ratio vs none [CI] | worker RSS overhead MiB`
  - *Band-aid:* `max_rss_mb | recycles during run | peak RSS MiB | jobs lost (must be 0)`
  - *Myth demo:* `Python | objects created | collected after gc.collect | gc.garbage length | RSS slope`

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G6.1 | Memray installs and runs on 3.15 in WSL Debian, with `run --follow-fork` and `--native` | `uv sync --group lab-k6 --python 3.15 && uv run memray run --follow-fork --native -o /tmp/x.bin -c "print(1)"` | try 3.14. If there's no supported interpreter, the walkthrough uses tracemalloc + RSS only, and the report says Memray was unavailable (version, error). The native leak is then shown via RSS slope vs a flat tracemalloc total, which is itself a good lesson |
| G6.2 | Memray `attach` exists and works on a running worker | `memray attach <pid>` (needs gdb or lldb + ptrace) | skip attach; use `run --follow-fork` only. Document it |
| G6.3 | Tachyon CLI: `python -m profiling.sampling` subcommands (`run`, `attach`?), output formats (flamegraph/collapsed/pstats), modes (wall/cpu/gil/exception), `--diff-flamegraph` | `python3.15 -m profiling.sampling --help` and the subcommand `--help`s, pasted into the report | use the real names; correct errata. If the diff flamegraph is missing, produce two flamegraphs side by side |
| G6.4 | `python -m pdb -p PID` attaches on 3.14+/3.15 and needs ptrace permission | try with `ptrace_scope=1` (as a non-descendant → expect a failure message), then with `sudo` / `ptrace_scope=0` | document the exact failure message and the fix per spec/07 |
| G6.5 | the profiler must match the target's minor version and build (research §2.8) | try attaching 3.15's Tachyon to a 3.15t worker, capture the error | record what actually happens |
| G6.6 | PEP 442: `__del__` cycles are collected, `gc.garbage` stays empty | the `labs.cycle_with_del` test | if not (it would be very surprising), record it in errata and in the myth section |
| G6.7 | `ctypes.CDLL(None).malloc` is resolvable in WSL glibc | a quick test | use `ctypes.CDLL("libc.so.6")` |
| G6.8 | `gc.get_freeze_count()` exists (3.7+) | a trivial check | omit the field |

## 9. Acceptance criteria

- **AC-6.1** All debug ops in §5.2 work through KP, through `kiln debug`, and through the gateway (`tests/integration/test_debug_ops.py`).
- **AC-6.2** Each unfixed leak shows a slope whose 95% CI excludes 0. Each fixed twin and the control show a CI that includes 0 (or the report explains why not), with records in `results/published/K6/`.
- **AC-6.3** tracemalloc's diff top row names the correct `leaks.py` line for every Python-level leak, as shown in the report with real output.
- **AC-6.4** The native leak is shown to be invisible to tracemalloc and visible in RSS (and in Memray if G6.1 passed).
- **AC-6.5** The myth demo shows `__del__` cycles being collected on 3.15, with `gc.garbage` empty.
- **AC-6.6** The recycling band-aid run shows bounded RSS with **0 lost jobs** (all jobs SUCCEEDED).
- **AC-6.7** Walkthroughs for tracemalloc, Memray (or a documented unavailability), Tachyon and `pdb -p` contain real commands and real output.
- **AC-6.8** The Docs deliverables exist and `./scripts/dev.sh docs-check` passes.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/memory-leaks-in-gc-languages.md` | reachability, the leak pattern catalogue, the "growing container" heuristic, why RSS may not drop after a fix (allocator arenas: pymalloc, glibc) |
| Concept | `docs/concepts/tracemalloc.md` | how it hooks allocators, frames depth vs overhead, snapshots, `compare_to`, filters, blind spots |
| Concept | `docs/concepts/weakref.md` | strong vs weak references, `weakref.ref`, `WeakMethod`, `WeakValueDictionary`, callbacks, when weakrefs don't work (ints, lists) |
| Concept | `docs/concepts/sampling-vs-tracing-profilers.md` | how each works, overhead, accuracy, when to use which; cProfile vs Tachyon vs Memray vs tracemalloc |
| Concept | `docs/concepts/remote-debugging-pep768.md` | `sys.remote_exec`, how attach works (a safe point in the eval loop), ptrace, `ptrace_scope`, same-version requirement, security implications |
| Concept | `docs/concepts/flamegraphs.md` | how to read one (width = time/bytes, stacks bottom-up), diff flamegraphs, common misreadings |
| Python | `docs/python/functools-lru-cache.md` | decorator syntax recap, `maxsize`, `cache_info`, `cache_clear`, why `maxsize=None` is dangerous with unbounded keys |
| Python | `docs/python/closures.md` | (if K2 didn't write it) captured variables, cells, why a closure keeps its captures alive |
| Python | `docs/python/weakref.md` | the API walkthrough with REPL examples |
| Python | `docs/python/del-and-finalizers.md` | `__del__`, when it runs, PEP 442, resurrection, `weakref.finalize` as the better tool |
| Walkthroughs | `labs/k6/diagnose_tracemalloc.md`, `labs/k6/diagnose_memray.md`, `labs/k6/diagnose_tachyon.md`, `labs/k6/diagnose_pdb.md` | each one is a step-by-step session: the exact commands, the real output, what to look at, and what it proves |
| Tour | `docs/tours/K6.md` | stops: leaks.py (each leak + twin) → debug.py ops → master debug forwarding → CLI rendering → sampler/slope |
| Lab | `docs/labs/K6-leaks.md` | per the template; all §7 tables; the walkthroughs; the myth section |

## 11. Reference comparison assignment

1. **Snyk post:** compare their snapshot-diff loop with our `tracemalloc_diff` op. *Questions:* how did they filter by package? What would our `include` glob be for "only third-party code"?
2. **Memray docs** (the *native mode* and *follow-fork* sections): *Questions:* how does Memray intercept `malloc` (LD_PRELOAD-style symbol interposition)? Why can't tracemalloc do that?
3. **Tachyon docs:** *Question:* how does it read another process's Python stack without stopping it, and what does the same-version requirement tell you about how it does it?
4. **Celery `--max-memory-per-child`** source (`celery/concurrency/asynpool.py` or the `worker_max_memory_per_child` handling): *Question:* how does Celery measure memory (RSS? a resource module?), and when does it check (before or after a task)? Compare with spec/04 §5.5.

## 12. Risks and pitfalls

- **RSS doesn't go down after freeing.** Allocators keep freed memory for reuse, so "fixed" can still show a high but *flat* RSS. Always judge by the **slope**, not the level. The primer explains this.
- **Debug ops perturb the measurement.** Snapshots and `type_counts` allocate memory themselves. Take measurement runs with tracemalloc **off**, and diagnosis runs separately.
- **The native leak can exhaust memory quickly.** Keep `kb × jobs` under the ADR-023 budget (64 KiB × 6,000 ≈ 375 MiB).
- **`pdb -p` pauses the worker.** The master's heartbeat timeout (10 s) will SIGKILL a worker left paused at the pdb prompt. During walkthroughs set `KILN_WORKER__HEARTBEAT_TIMEOUT_S=600` and say so in the report. This is itself a useful lesson about supervisors and debuggers.
- **Memray output files are large.** Write them under `results/K6/memray/` (git-ignored) and never publish the binaries.

## 13. Checkpoint quiz (seed questions)

1. Python has a garbage collector. Give three ways a Python program can still leak memory.
2. Why does `@lru_cache(maxsize=None)` leak in a server but not in a short script?
3. Two objects in a cycle both define `__del__`. On Python 3.15, are they freed? What changed in Python 3.4?
4. What is "resurrection", and why does it defeat even PEP 442?
5. Why can't tracemalloc see the `labs.leak_native` allocation, while RSS and Memray can?
6. Why does a weak reference in the listener registry fix the closure leak?
7. A flat-but-high RSS after a fix: is the leak still there? How do you tell?
8. Why does attaching `pdb -p` to a Kiln worker risk the worker being killed, and by whom?
9. Sampling vs tracing: which one would you run in production for an hour, and why?
10. `max_rss_mb` keeps memory bounded. Why is it still not a fix?

## 14. Stretch / later

- A master-side leak check: `debug target=master tracemalloc_*` ops (spec/06 currently allows only `stats` and `gc_stats` on the master).
- An automatic leak detector: the worker takes snapshots every N jobs and warns when the same line grows K times in a row (in the spirit of the Snyk loop).
- Use `sys.remote_exec` directly (without pdb) to inject a snapshot script into a running worker. Compare with our debug-op design: what does each approach need?
