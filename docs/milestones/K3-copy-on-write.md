# K3: Copy-on-write lab (Instagram's GC problem)

> **Status:** 📝 specified · **Depends on:** K1 (K2 for the spawn/forkserver variants) · **Branch:** `milestone/k3-cow` · **Size:** M
> **Incident:** research §2.1 (Instagram: the GC breaks copy-on-write sharing), errata E-03, E-14, E-15, E-16, [ADR-028](../decisions.md#adr-028).
> **Reference implementations (read *after* building):**
> - The CPython `gc` docs: the `gc.freeze()` recipe;
> - `Modules/gcmodule.c` / `Python/gc.c` (`gc_freeze_impl`, `gc_collect_main`, how `PyGC_Head` is written during a collection) and `Python/gc_free_threading.c` for 3.15t;
> - gunicorn `--preload` (`gunicorn/arbiter.py` `Arbiter.setup`/`spawn_worker`, plus `app.wsgi()` preloading);
> - Instagram Engineering's two posts (research §8; read via the secondary summary);
> - the Bolna and SSD Nodes gunicorn memory guides (research §4 K3).

---

## 1. The story (plain English)

Instagram ran Django, a big Python web framework, behind a pre-fork server, much like Kiln. The master process loaded all of Django once, then **forked** many workers. Forking is cheap because of a trick called **copy-on-write** (**CoW**). After a fork, the parent and child don't really get separate copies of memory. They *share* the same physical memory pages, and the kernel only makes a private copy of a page when one of them **writes** to it. So 30 workers could share a few hundred megabytes of Django code and data, almost for free.

Except it didn't stay free. Within seconds of serving requests, each worker's shared memory fell from about 250 MB to 140 MB. Instagram called it **"copy-on-read."** In CPython, merely *looking* at an object changes it. Every object carries a **reference count** that goes up and down as variables point at it. And the **garbage collector** (GC), when it runs, writes bookkeeping into every container object it scans. Both are writes, and every write to a shared page forces the kernel to copy that page into the worker's private memory.

First, Instagram simply disabled the GC. But `gc.disable()` wasn't enough: a library (`msgpack`) quietly called `gc.enable()` again. They found it with a debugger, and used `gc.set_threshold(0)` instead, which turns off automatic collections no matter who calls `gc.enable()`. They also skipped the final GC at shutdown with `atexit.register(os._exit, 0)`. The result was about 10% more capacity and about 8 GB of RAM saved per machine. Later, Python 3.7 added **`gc.freeze()`**, which moves all existing objects into a "permanent generation" that the GC never scans again.

In this milestone we reproduce this. Kiln's master preloads a big object graph (hundreds of MiB, built to look like a framework's data) and forks workers. We measure exactly how much memory each worker shares, using Linux's `/proc/<pid>/smaps_rollup`. Then we watch the sharing decay as workers read the data and the GC runs. We try each of Instagram's fixes, build a detector that catches a library re-enabling the GC, and learn the twist: **`gc.freeze()` can't stop refcount writes** (errata E-15). Reading objects still costs you, and only "immortal" objects escape.

## 2. What you will learn

- **Explain** virtual memory, pages and copy-on-write, and **predict** when a page gets copied. *(Memory & perf; runtime internals)*
- **Explain** reference counting, and why *reading* an object writes to it on the GIL build. *(Runtime internals)*
- **Explain** CPython's generational GC: what it scans, what it writes, generations and thresholds, `gc.disable` vs `set_threshold(0)` vs `freeze`. *(Runtime internals)*
- **Explain** immortal objects (PEP 683) and which objects are immortal. *(Runtime internals)*
- **Explain** finalization: `atexit` LIFO order, the final collection, and why `os._exit` skips both (ADR-028). *(Runtime internals)*
- **Read** `/proc/<pid>/smaps_rollup`, and **explain** RSS vs PSS vs USS, shared vs private, clean vs dirty. *(Memory & perf)*
- **Explain** how any library can change global interpreter state, and **design** a detector for it. *(Runtime internals; data model: monkeypatching, import hooks)*
- **Design** a memory experiment with phases, controls and repeats. *(Testing & statistics)*

## 3. Entry criteria

- K1 ✅. The K2 spawn launcher is needed for the spawn control variant and the exit-trick experiment. If K2 isn't done, those variants are deferred and marked `not measured (K2 pending)`. K3's order in the roadmap puts K2 first, so this shouldn't happen.
- `kiln doctor --milestone K3` exits 0 (D14 `smaps_rollup` readable; D17 includes gdb).

## 4. Scope

### In scope
- `kiln/memory/procfs.py`: `read_smaps_rollup`, plus the K1 `read_status`.
- `kiln/memory/gcmodes.py` (**new module**; spec/01 §5 gets it added): the `worker.gc_mode` strategies.
- `kiln/memory/gcwatch.py` (the K3 part): the "who re-enabled the GC" detector. K7 adds the pause histogram to the same module.
- `kiln/labs/preload.py` (**new**): the preload object graph, built when `labs.preload_mb > 0`.
- New lab tasks: `labs.touch_preloaded` (specified in spec/03 §7; K3 implements it), `labs.churn`, `labs.gc_collect`, `labs.gc_stats` (new; added to spec/03 §7 by this milestone).
- Config: implement `worker.gc_mode` and `worker.exit_via_os_exit` (spec/05); `worker.memory_metric = pss | uss` for recycling (spec/04 §5.5); and a **new key `labs.gc_watch: bool = false`** (added to spec/05 by this milestone).
- `kiln mem` CLI.
- `labs/k3/`: `preload_graph.py`, `cow_experiment.py`, `rogue_gc_enabler.py`, `sampler.py`, `exit_trick.py`.
- The lab report.

### Out of scope (and why)
- Changing CPython, or using a custom allocator. We measure the stock runtime.
- `mmap`-based sharing or `multiprocessing.shared_memory`. These are different techniques; mention them in the primer's "go deeper" section only.
- The GC pause histogram (K7).
- Huge pages / THP effects. Mention them as a threat to validity; verify the THP setting in the Setup section.

## 5. Design

### 5.1 `kiln.memory.procfs` (K3 additions)

```python
@dataclass(frozen=True, slots=True)
class SmapsRollup:            # all values in bytes (the file reports kB; × 1024, with a GOTCHA comment)
    rss: int; pss: int; pss_anon: int; pss_file: int; pss_shmem: int
    shared_clean: int; shared_dirty: int; private_clean: int; private_dirty: int
    swap: int; swap_pss: int
    @property
    def uss(self) -> int: return self.private_clean + self.private_dirty
    @property
    def shared(self) -> int: return self.shared_clean + self.shared_dirty

def read_smaps_rollup(pid: int | Literal["self"]) -> SmapsRollup: ...
def children_of(pid: int) -> list[int]: ...         # from /proc/<pid>/task/*/children, with a fallback scan of /proc/*/stat ppid
```

- Missing keys (older kernels) are 0, and a DEBUG log names the missing key.
- Reading a vanished pid raises `ProcessGone`.
- Definitions follow [spec/10 §7](../spec/10-measurement-methodology.md#7-memory-metrics-definitions) exactly.
- `memory_metric = pss|uss` in the master's recycling check (spec/04 §5.5) reads `smaps_rollup` at most every 5 s per worker. **WHY:** reading smaps walks every mapping in the kernel, which costs milliseconds for big processes, and the master must not block (ADR-006).

### 5.2 `kiln mem`

`kiln mem PID... [--children-of PID] [--json] [--watch SECONDS]` prints a table with the columns `pid | role (from /proc/<pid>/cmdline) | RSS | PSS | USS | Shared | Private_Dirty | Swap`, a **Σ row**, and a footnote explaining that "Σ RSS counts shared pages several times; Σ PSS is the real total". The values are in MiB with one decimal. JSON uses bytes. The `--help` text defines every column in one line each.

### 5.3 The preload graph (`kiln.labs.preload`)

```python
GRAPH: PreloadGraph | None = None      # module global; the master sets it before forking (fork) or each worker sets it at boot (spawn)

@dataclass(slots=True)
class PreloadGraph:
    records: list[dict[str, object]]
    owners: list["Owner"]
    composition: str
    target_mb: int
    traced_bytes: int                   # what tracemalloc measured while building

class Owner:                            # a plain class with __dict__, to mimic a framework "model instance"
    def __init__(self, uid: int, name: str, perms: list[str]) -> None: ...

def build_graph(mb: int, *, seed: int = 0, composition: Literal["mortal", "immortal-heavy"] = "mortal") -> PreloadGraph: ...
def touch(graph: PreloadGraph, fraction: float) -> int: ...   # returns the number of objects visited (for sanity checks)
```

- **"mortal" composition** (default; it mimics framework data). Each record is a `dict` with:
  - `"id"`: an int ≥ 2**40, so it's not in the small-int cache;
  - `"name"`: a random 16-char `str`, built with `"".join(...)`, so it isn't interned;
  - `"tags"`: a list of 4 such strings;
  - `"scores"`: a list of 8 floats;
  - `"meta"`: a dict with 3 keys mixing int and str values;
  - `"owner"`: an `Owner` instance (one per 10 records, shared).
  - Records are appended to `records` until `tracemalloc.get_traced_memory()[0]` (tracing started just for the build, then stopped) reaches `mb` MiB.
- **"immortal-heavy" composition:** the same shape, but leaf values are small ints (−5..256), `None`/`True`/`False`, and `sys.intern`'d short words from a 50-word vocabulary. The containers (dicts and lists) are still mortal. This isolates "element refcount writes" from "container refcount writes" (experiment (b)).
- **Deterministic:** `random.Random(seed)`. The same mb + seed gives the same graph.
- `touch(graph, f)` visits the first `ceil(f × len(records))` records and reads every value. Reading `x = rec["id"]` increments and decrements refcounts, so it writes the object headers. It returns the count visited.
- **Where it's built:**
  - fork: in the master, after `gcmodes.apply_parent_early()` and the app preload, before the first fork;
  - spawn: in each worker after boot (the control: nothing shared);
  - forkserver: in the forkserver process (K2 preload path).
- `labs/k3/preload_graph.py` is a small script that builds a graph and prints its composition: object counts per type, `traced_bytes`, and the build time. The tour uses it.

### 5.4 Lab tasks (K3)

| Task | Args | Behaviour |
|---|---|---|
| `labs.touch_preloaded` | `fraction` | `touch(GRAPH, fraction)`; returns `{"visited": n, "pid": os.getpid()}` |
| `labs.churn` | `n` | creates and drops `n` small container cycles (`a = []; a.append(a)`) and short-lived dicts. This triggers **automatic** collections when the GC is enabled. Returns `gc.get_count()` before and after |
| `labs.gc_collect` | `generation` (0–2) | `gc.collect(generation)`. An explicit call runs **even when the GC is disabled**. That's a key lesson. Returns the count collected and the duration in ms |
| `labs.gc_stats` | — | `{"enabled": gc.isenabled(), "threshold": gc.get_threshold(), "count": gc.get_count(), "stats": gc.get_stats(), "frozen": gc.get_freeze_count(), "pid": ...}` |

Every task returns its pid, so the experiment can confirm *which* worker ran it.

### 5.5 `kiln.memory.gcmodes` (the `worker.gc_mode` strategies)

The research recipe, from the `gc` docs: *`gc.disable()` early in the parent, `gc.freeze()` right before `fork()`, `gc.enable()` early in the child.*

| `gc_mode` | `apply_parent_early()` (master or forkserver, **before** the app preload and graph build) | `before_fork()` (fork launcher and forkserver, right before every `os.fork()`) | `apply_child_early()` (worker, right after signal setup, **before** the app load) |
|---|---|---|---|
| `default` | — | — | — |
| `disabled` | `gc.disable()` | — | `gc.disable()` (a no-op for forked workers, which inherited it; needed for spawn) |
| `threshold0` | `gc.set_threshold(0)` | — | `gc.set_threshold(0)` (same reasoning) |
| `freeze` | `gc.disable()` | `gc.freeze()` | `gc.enable()` |

- **Spawn:** `freeze` has nothing to share. The master logs WARNING once ("gc_mode=freeze has no effect under spawn; nothing is shared") and the worker behaves like `default`. `disabled` and `threshold0` still apply (the control group).
- **The master's own GC under `freeze`/`disabled`:** it stays disabled, so the master may accumulate cycles. The master allocates few cycles; the lab reports the master's PSS anyway.
- **Every call is logged** (DEBUG, event `gc_mode_applied`, added to spec/06 by this milestone) with the phase and the resulting `gc.isenabled()`/`gc.get_threshold()`.

### 5.6 `worker.exit_via_os_exit` (Instagram's shutdown trick, scoped by ADR-028)

- **Forked workers** already end with `os._exit` (ADR-028; errata E-16). The setting is a no-op for them, and the master logs INFO once to say so.
- **Spawned workers:** `kiln/worker/__main__.py` registers `atexit.register(_os_exit_with_code)` as its **first** action, before importing anything else from Kiln or the app. atexit runs handlers last-in-first-out, so ours runs **last**: after the app's own handlers, but *before* interpreter finalization (the final collection and module teardown), which it skips. `_os_exit_with_code` reads a module global `EXIT_CODE` that `main()` sets just before `sys.exit(code)`.
- **Master:** when true, the arbiter registers the same handler first thing at startup.

### 5.7 `kiln.memory.gcwatch`: the "who re-enabled the GC?" detector

> **Constraint from K7:** `gcwatch.py` must stay **stdlib-only**, use only **3.13-compatible syntax**, and work when loaded **by file path** (`importlib.util.spec_from_file_location`) with no other Kiln module imported. K7 loads it into standalone scripts that run on 3.14.4 and 3.14.5 without Kiln installed.

**The problem** (errata E-03, E-14): `gc.callbacks` fires on *collections*, not on `gc.enable()` calls, and a C extension can re-enable the GC without any Python code running. One hook can't catch everything, so the detector has **three layers**, plus a manual fourth, each catching a different kind of culprit:

| Layer | Mechanism | Catches | Misses | Evidence quality |
|---|---|---|---|---|
| **A. Shim** | replace `gc.enable` (the module attribute) with a Python wrapper that records `traceback.extract_stack()` and then calls the original | Python code calling `gc.enable()`, *and* Cython-compiled code (which looks up `gc.enable` on the module at call time) | C code calling the C-API `PyGC_Enable()` directly; code that saved a reference to the original `gc.enable` before the shim was installed | **exact** call stack |
| **B. Import-flip** | a `sys.meta_path` finder at position 0 that delegates to the remaining finders, then wraps the found spec's loader so `exec_module` records `gc.isenabled()` before and after | anything that flips the GC on **while a module is being imported**, including C-API calls in extension modules. With nested imports, the flip is attributed to the **innermost** module whose before/after shows the flip, when none of its own child imports flipped | flips at runtime, after import | the module name |
| **C. Runtime poll + collection sentinel** | (1) the worker's heartbeat thread checks `gc.isenabled()` every interval; (2) a `gc.callbacks` function that, on `phase == "start"`, checks whether a collection began **while we expected the GC to be disabled** *and* `gc.isenabled()` is true (an automatic collection; an explicit `gc.collect()` while disabled is *not* flagged) | flips that happen at runtime from any source | which code did it (it only gives the time window and the job running then) | the job id + time; the stack at the collection is the *allocation site*, not the culprit, and the docs say so |
| **D. gdb (manual, Instagram's method)** | `gdb --args python -m kiln.worker ...`, or run the lab script under gdb, with `break PyGC_Enable` and `break gc_enable_impl`, then `bt` / `py-bt` | everything | — | an exact C + Python stack |

```python
@dataclass(frozen=True, slots=True)
class GcEvent:
    layer: Literal["shim", "import_flip", "poll", "collection_while_disabled"]
    t_mono: float
    module: str | None          # layer B
    stack: tuple[str, ...]      # layer A (exact), layer C (the allocation site, labelled as such)
    job_id: str | None          # layer C, filled in by the worker
    note: str

class GcWatch:
    def __init__(self, *, expect_disabled: bool, on_event: Callable[[GcEvent], None]) -> None: ...
    def install(self) -> None: ...       # A + B + C(2); idempotent
    def uninstall(self) -> None: ...     # restores gc.enable, removes the finder and the callback
    def poll(self) -> GcEvent | None: ...  # C(1), called by the heartbeat thread
```

- **Wiring:** with `labs.gc_watch = true` and a `gc_mode` that expects the GC off (`disabled`, `threshold0`, or `freeze` in the *parent*), the master installs `GcWatch` before preloading, and each worker installs it right after `apply_child_early`.
  - Under `threshold0` the GC is "enabled but never automatic". The poll and shim still report `gc.enable()` calls, with the note "harmless under threshold0". That's the reason Instagram switched.
  - Under `freeze`, the child *deliberately* enables the GC, so the worker uses `expect_disabled=False` and only layer A reports (informational).
- **Events** are logged as WARNING with the event name `gc_reenabled` (added to spec/06), fields `layer`, `module`, `job_id`, and `stack` (the top 8 frames).
- **Rejected alternative (to be written up in the primer):** a `sys.addaudithook` on the `import` event fires *before* a module executes, so it can't see the state *after* the import. It would need pairing with the next event, which is fragile with nested imports.

### 5.8 The culprit: msgpack, or a stand-in

- **Gate G3.1** (errata E-03) checks whether today's `msgpack` re-enables the GC at import or during `unpackb`.
- If it doesn't, the lab uses `labs/k3/rogue_gc_enabler.py`. It's a stand-in, clearly labelled `# LAB:` and described in PROVENANCE-style text at the top. Its behaviour is chosen with `KILN_ROGUE_MODE`:
  - `python`: `gc.enable()` at import. Expected catchers: A, B, C.
  - `capi`: `ctypes.pythonapi.PyGC_Enable()` at import, which bypasses the shim. Expected catchers: B, C, D.
  - `runtime`: exposes `parse(data)`, which calls `PyGC_Enable()` via ctypes when called from a task. Expected catchers: C, D.
- `examples/k3_app.py` is the lab app. It imports the rogue module (or msgpack) at top level and registers a task `k3.parse` that calls `parse()`.

### 5.9 Sampler and experiment driver

- `labs/k3/sampler.py` is a **separate process** (spec/10 §7), started by the driver. Every 200 ms it reads `smaps_rollup` for the master and every child, and writes `(t_s, pid, role, pss, shared, private_dirty, rss)` rows to a JSONL file. The driver turns those into ExperimentRecord `samples`.
- `labs/k3/cow_experiment.py` (lab id `k3-cow`):
  1. Start `kiln serve examples.k3_app:app --labs` with `labs.preload_mb`, `worker.gc_mode`, `start.method` and `workers = 4` set via env vars (spec/05 §1). Wait for READY using `stats`.
  2. Run the **phases** of §7 in order.
  3. A phase that needs "every worker did X" submits `workers × 2` concurrent jobs and checks the returned pids cover all workers. Otherwise it repeats, up to 5 rounds, and then fails the run with a clear message.
  4. After each phase, settle for 1 s, then snapshot `smaps_rollup` for all processes and `labs.gc_stats` for each worker.
  5. Write one ExperimentRecord per (variant, repeat).

## 6. Steps

| # | Goal | Docs first | Tests | Code | Done when |
|---|---|---|---|---|---|
| 3.1 | **Memory primers + smaps reader** | concepts `virtual-memory-and-pages`, `copy-on-write`, `smaps`; extend `memory-metrics` | `tests/unit/test_procfs.py` (fixture smaps_rollup and status files, the kB→bytes conversion, missing keys); `tests/integration/test_procfs_live.py` (a forked child that writes N MiB shows Private_Dirty growth ≥ N MiB) | `memory/procfs.py` additions, `cli.py mem` | AC-3.1 |
| 3.2 | **GC primers + gcmodes** | concepts `refcounting`, `garbage-collector`, `gc-freeze`, `immortal-objects`; python `gc-module`, `sys-getrefcount` | `tests/integration/test_gcmodes.py`: per mode × launcher, a worker's `labs.gc_stats` shows the expected enabled/threshold/freeze count | `memory/gcmodes.py`; hooks in the arbiter, fork launcher, forkserver and worker main | AC-3.2 |
| 3.3 | **Preload graph + lab tasks** | python `tracemalloc-basics` (the build measurement only; K6 goes deep) | `tests/unit/test_preload.py`: deterministic for a seed; size within ±10% of the target; touch counts | `labs/preload.py`, tasks in `labs/tasks.py`, `labs/k3/preload_graph.py` | graph builds at 300 MiB in a reasonable time (recorded) |
| 3.4 | **gcwatch + rogue lib** | python `monkeypatching`, `import-hooks-meta-path`, `ctypes-basics`, `traceback-module` | `tests/unit/test_gcwatch.py`: layer A catches `python` mode with the exact stack; layer B attributes `capi` mode to the right module (also when nested inside another import); layer C flags an automatic collection after a C-API enable, and does **not** flag an explicit `gc.collect()` while disabled; uninstall restores everything | `memory/gcwatch.py`, `labs/k3/rogue_gc_enabler.py`, `examples/k3_app.py`, worker/master wiring behind `labs.gc_watch` | AC-3.4 |
| 3.5 | **Exit trick + finalization primer** | concept `finalization-and-atexit`; python `atexit` | `tests/integration/test_exit_trick.py`: a spawned worker with the trick skips finalization (an object's `__del__` that prints at module teardown doesn't print), but the app's own atexit handler **does** run; for forked workers neither runs (ADR-028) | `worker/__main__.py`, arbiter startup | AC-3.5 |
| 3.6 | **Experiments + report** | — | `tests/lab/test_k3_lab_smoke.py` (20 MiB graph, 2 workers, 1 repeat, a valid record) | `labs/k3/cow_experiment.py`, `sampler.py`, `exit_trick.py` | lab report complete |

## 7. Lab design

Lab report: `docs/labs/K3-cow.md`. Lab ids: `k3-cow` (main), `k3-composition`, `k3-culprit`, `k3-exit`.

### (a) Main: sharing decay per GC mode (`k3-cow`)
- **Question:** how much of the preloaded graph stays shared in each worker as it serves reads and as the GC runs, under each `gc_mode`?
- **Implementer's hypothesis** (reasoning in the report; *never* numbers):
  - **P0:** every fork variant shares nearly all graph pages (Shared ≈ graph size; Private_Dirty small).
  - **P1/P2 (touch 0.25 / 1.0):** Private_Dirty rises roughly in proportion to the fraction touched, **in every mode**, because touching writes refcounts (E-15). `freeze` doesn't prevent this.
  - **P3 (churn):** under `default`, the automatic collections include gen-2 passes that scan the whole graph, so much of the remaining shared memory is copied. Under `disabled`/`threshold0` there are no collections, so no extra copying. Under `freeze` collections happen, but the frozen graph isn't scanned, so little extra copying.
  - **P4 (explicit `gc_collect(2)`):** `default`, `disabled` and `threshold0` all copy the remaining graph pages (an explicit collect runs regardless). `freeze` stays low.
  - **Spawn control:** Shared ≈ 0 for the graph in every phase. Σ PSS is the highest.
  - **3.15t (secondary):** different, because of biased refcounting and the free-threaded GC. The hypothesis is stated as "unknown; to be explained".
- **Independent variables:**
  - `gc_mode` ∈ {default, disabled, threshold0, freeze};
  - `start.method` ∈ {fork (primary), spawn (control), forkserver (secondary)};
  - interpreter ∈ {3.15 (primary), 3.13, 3.15t (secondary)}.
- **Phases:**
  - **P0** all READY + 1 s;
  - **P1** each worker ran `touch_preloaded(0.25)`;
  - **P2** each worker ran `touch_preloaded(1.0)`;
  - **P3** each worker ran `churn(n=200000)`;
  - **P4** each worker ran `gc_collect(2)`.
- **Dependent variables, per phase and per worker:** `shared_bytes`, `private_dirty_bytes`, `pss_bytes`, `uss_bytes`, `rss_bytes`; Σ PSS over master + workers; GC collections per generation (from `gc_stats`); plus the 200 ms time series.
- **Controls:**
  - `labs.preload_mb = 300`, `workers = 4` (ADR-023: about 300 MiB + 4 × the copied pages, under 3 GiB in the worst case, which the driver checks before starting);
  - `composition = mortal`;
  - seed 0;
  - an idle machine;
  - the THP setting recorded (`cat /sys/kernel/mm/transparent_hugepage/enabled`).
- **Repeats:** per [spec/10 §3](../spec/10-measurement-methodology.md#3-repeats-and-warm-ups).
- **Result table shapes:**
  - `mode | method | phase | shared/worker MiB (median, 95% CI) | private_dirty/worker MiB | Σ PSS MiB | gen2 collections`;
  - one line chart per mode (shared/worker over time, phase boundaries marked), rendered from the samples. K9 renders it properly; K3 may embed a simple SVG or a table.

### (b) Composition: mortal vs immortal-heavy (`k3-composition`)
- **Question:** do immortal objects (PEP 683) avoid copy-on-read?
- **Hypothesis:** with `immortal-heavy`, touching copies fewer pages than with `mortal`, because leaf refcount writes are skipped, but not zero, since the containers are mortal.
- **Variables:** composition × {default, freeze}, fork, 3.15. Phases P0–P2 only.
- **Result table shape:** `composition | mode | phase | private_dirty/worker MiB | Δ vs mortal`.

### (c) Culprit detection (`k3-culprit`)
- **Question:** which layer catches which kind of GC re-enable?
- **Procedure:** for each culprit ∈ {msgpack (if gate G3.1 shows it toggles), rogue `python`, rogue `capi`, rogue `runtime`}, run with `gc_mode = disabled`, `labs.gc_watch = true`. Record the `gc_reenabled` events. Then the same with `threshold0`: show that automatic collections never start even after the re-enable (P3 churn produces 0 collections). Finally, demonstrate layer D once with gdb and paste the real `bt` output.
- **Result table shape:** `culprit | layer A | layer B | layer C | gdb | under threshold0: collections during churn`.

### (d) Shutdown trick (`k3-exit`)
- **Question:** what does `atexit.register(os._exit, 0)` save, and where does it apply?
- **Procedure:**
  1. Spawned workers with `exit_via_os_exit` false/true and a 300 MiB per-worker graph: measure `shutdown_ms` (from the `shutdown` message to the reap), and whether app atexit handlers and finalizers ran.
  2. Forked workers: show it's a no-op (ADR-028). Instead, measure what a final collection *would* cost: Private_Dirty growth from a `labs.gc_collect(2)` submitted just before shutdown, under `default` vs `freeze`.
- **Result table shape:** `method | exit_via_os_exit | shutdown ms (median, CI) | atexit ran? | finalizers ran? | Δ private_dirty from a final collect`.

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check (command) | If false, fallback |
|---|---|---|---|
| G3.1 | Today's `msgpack` calls `gc.enable()` at import or during `unpackb` (E-03) | `labs/k3/check_msgpack.py`: `gc.disable(); import msgpack; print(gc.isenabled())`, then `msgpack.unpackb(msgpack.packb([{}]*10000))` and print again, on every matrix tag; also grep the sdist for `gc.enable`/`PyGC_Enable` | use `rogue_gc_enabler.py` (§5.8). Optionally search for an old msgpack release that toggles it **and** installs on 3.13+; pin it only in `lab-k3`. Update E-03 either way |
| G3.2 | `gc.set_threshold(0)` disables automatic collection on 3.13, 3.14 and 3.15 | `labs.churn` + `gc.callbacks` counter = 0 collections | document the version difference; drop that mode for that version, with a note |
| G3.3 | `gc.freeze()` / `gc.get_freeze_count()` exist and behave as documented on 3.15 **and 3.15t** | `labs.gc_stats` after freeze | on 3.15t: document the actual behaviour from `Python/gc_free_threading.c`, and run (a) without `freeze` there |
| G3.4 | Explicit `gc.collect()` runs while `gc.disable()` is in effect | snippet `concepts/snippets/gc_collect_while_disabled.py` | correct the hypothesis and the primer |
| G3.5 | `/proc/<pid>/smaps_rollup` for our own children is readable with `ptrace_scope=1` | doctor D14 + `test_procfs_live.py` | run the lab with `sudo sysctl kernel.yama.ptrace_scope=0` and document why (spec/07 §5) |
| G3.6 | Which objects are immortal on 3.15 (small ints, None, interned strings?) | `sys._is_immortal(obj)` if present (3.14+), else `sys.getrefcount(obj)` showing a huge constant | adjust the "immortal-heavy" composition to what's really immortal; record it |
| G3.7 | Cython-compiled code calls `gc.enable` through the module attribute (so layer A catches it) | if G3.1 finds a Cython culprit, check that the shim fires; otherwise build `labs/k3/cython_probe/` **only if** Cython is easily installable, else mark it as a claim, "documented, not verified" | the primer states the claim as unverified |
| G3.8 | `ctypes.pythonapi.PyGC_Enable` exists (C-API, 3.10+) | `python -c "import ctypes; ctypes.pythonapi.PyGC_Enable"` | `capi` mode calls `PyImport_ImportModule("gc")` + `PyObject_CallMethod` via ctypes (still bypasses the shim if done through the C function pointer); document it |
| G3.9 | The preload graph at 300 MiB fits the memory budget with 4 workers in the worst case (everything copied) | the driver's pre-check: `MemAvailable` ≥ 300 × 5 MiB + 512 MiB | reduce to 200 MiB, then 100 MiB; record it |
| G3.10 | THP doesn't distort the sharing numbers | record `/sys/kernel/mm/transparent_hugepage/enabled`; if `always`, also run one variant with `MADV_NOHUGEPAGE`-free reasoning, or with THP set to `madvise` (sudo), and compare | report it as a threat to validity |

## 9. Acceptance criteria

- **AC-3.1** `kiln mem --children-of <master>` prints correct PSS/USS/shared for the master and its workers. The Σ PSS ≤ Σ RSS invariant holds. The unit tests pass on fixtures.
- **AC-3.2** For each `gc_mode` × {fork, spawn, forkserver}, `labs.gc_stats` in a worker reports exactly the state from the §5.5 table (`test_gcmodes.py`).
- **AC-3.3** Experiment (a) has published records for every primary variant (4 modes × fork, + spawn control) with the §7 repeats, and the report tables are filled from them.
- **AC-3.4** Experiment (c): every culprit mode is caught by exactly the layers predicted in §5.8, or the report explains each deviation. Under `threshold0`, churn after a re-enable produces 0 automatic collections.
- **AC-3.5** Experiment (d) shows the trick's effect on spawned workers, and shows the ADR-028 no-op for forked workers.
- **AC-3.6** The lab report's analysis explains, with numbers from the records, whether `freeze` restored sharing after *touching* (E-15). The learner can see the refcount lesson in the data.
- **AC-3.7** All docs deliverables exist. Spec updates done: spec/01 (gcmodes, labs/preload), spec/03 §7 (new tasks), spec/05 (`labs.gc_watch`), spec/06 (`gc_mode_applied`, `gc_reenabled`). `dev.sh check` and `test-matrix` are green.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/virtual-memory-and-pages.md` | virtual vs physical addresses; pages (4 KiB); page tables; page faults; how `fork` duplicates page tables, not pages |
| Concept | `docs/concepts/copy-on-write.md` | the CoW mechanism step by step with a diagram; what counts as a write; why Python's "reads" are writes; Instagram's numbers (research §2.1) |
| Concept | `docs/concepts/smaps.md` | how to read `/proc/<pid>/smaps_rollup`, field by field; real output from a Kiln worker; RSS/PSS/USS arithmetic with a worked 2-process example |
| Concept | `docs/concepts/refcounting.md` | `ob_refcnt`; incref/decref on every reference; `sys.getrefcount`'s +1; deallocation at 0; why refcounting can't free cycles; a note on biased refcounting in 3.15t |
| Concept | `docs/concepts/garbage-collector.md` | why a cycle collector exists; tracked containers; generations and thresholds; what a collection writes (`gc_refs` in the GC header); `gc.disable` vs `set_threshold(0)` vs explicit `collect`; a note on the 3.14 incremental GC (link K7) |
| Concept | `docs/concepts/gc-freeze.md` | the permanent generation; the docs' recipe and *why each step is where it is*; what `freeze` doesn't fix (E-15) |
| Concept | `docs/concepts/immortal-objects.md` | PEP 683; which objects are immortal (from gate G3.6); how immortality avoids refcount writes; Cinder's origin |
| Concept | `docs/concepts/finalization-and-atexit.md` | the interpreter shutdown sequence; atexit LIFO; the final collection; module teardown; `os._exit` vs `sys.exit`; ADR-028 and why forked children skip all of it; Instagram's trick |
| Concept | `docs/concepts/global-interpreter-state.md` | "any library can change global state" (gc, signal handlers, locale, `sys.setrecursionlimit`); how to detect it; the four gcwatch layers |
| Python | `docs/python/gc-module.md` | `enable/disable/isenabled`, `collect`, `get_threshold/set_threshold`, `get_count`, `get_stats`, `freeze/unfreeze/get_freeze_count`, `callbacks` |
| Python | `docs/python/atexit.md` | `register`, LIFO order, what doesn't run handlers (`os._exit`, signals) |
| Python | `docs/python/sys-getrefcount.md` | reading refcounts, the +1 for the argument, immortal values, `sys._is_immortal` |
| Python | `docs/python/monkeypatching.md` | replacing module attributes at runtime; why it works for `gc.enable`; why saved references bypass it |
| Python | `docs/python/import-hooks-meta-path.md` | `sys.meta_path`, finders, specs, loaders, `exec_module`; the delegate-and-wrap pattern |
| Python | `docs/python/ctypes-basics.md` | `ctypes.pythonapi`, `CDLL` vs `PyDLL` (links K1's hold_gil), calling C functions, the risks |
| Python | `docs/python/traceback-module.md` | `extract_stack`, `format_list`, frames as text |
| Python | `docs/python/tracemalloc-basics.md` | start/stop, `get_traced_memory`; just enough for the graph build (K6 goes deep) |
| Tour | `docs/tours/K3.md` | stops: procfs smaps → gcmodes table in code → the arbiter/launcher/worker hook points → preload graph → touch → gcwatch layers A/B/C → exit trick in `worker/__main__` → the experiment driver |
| Lab | `docs/labs/K3-cow.md` | experiments (a)–(d) |

## 11. Reference comparison assignment

The lab report's "Reference comparison" must answer:
1. **CPython `gc_freeze_impl`**: what does it actually do to the generation lists? Why is it O(1)-ish, and why must it happen *before* fork?
2. **What a collection writes:** find where `gc_collect_main` (or its 3.15 equivalent) writes to every scanned object's header. That's the "copy-on-read" source.
3. **3.15t `gc_free_threading.c`**: how does the free-threaded GC find objects (no generation lists)? What does `freeze` mean there?
4. **gunicorn `--preload`**: when does gunicorn load the app relative to forking? Does gunicorn apply any GC recipe? (Search for `gc.freeze` in gunicorn and its issue tracker.)
5. **Instagram's approach vs ours:** they used uWSGI and process-wide `gc.set_threshold(0)`. Which of our measurements match their reported 250 MB → 140 MB pattern, in *shape* (not absolute numbers)?

## 12. Risks and pitfalls

| Risk | Mitigation |
|---|---|
| The memory blow-up exceeds the WSL budget | the G3.9 pre-check; `workers = 4`; 300 MiB default; the driver aborts if `MemAvailable` drops below 512 MiB during a run |
| Measuring with an in-process reader that allocates | the separate sampler process (spec/10 §7) |
| Tasks not reaching every worker | the phase protocol of §5.9 (pid coverage check + bounded retries) |
| gcwatch's meta-path wrapper breaking imports (namespace packages, extension modules, `spec.loader` identity checks) | only wrap loaders that have `exec_module`; delegate every other attribute; a test imports a namespace package, a C extension module (`_json`) and a zip-imported module with the watch installed |
| The shim leaking outside labs | installed only when `labs.gc_watch = true`; `uninstall()` in tests; a unit test asserts `gc.enable is original` after uninstall |
| Confusing "shared" with "not copied" for file-backed pages (the interpreter binary, .so files) | the primer separates anon vs file PSS (`pss_anon` vs `pss_file`); the analysis focuses on `pss_anon` and `private_dirty` |
| Results differ between 3.15 and 3.15t in confusing ways | 3.15t is secondary; it gets its own subsection with the "unknown → explained" framing |

## 13. Checkpoint quiz (seed questions)

1. After `fork()`, the parent and child "share" 300 MiB. What exactly is shared, and when does it stop being shared?
2. Why does *reading* `record["name"]` in a worker cause a page copy on the GIL build?
3. `gc.disable()` vs `gc.set_threshold(0)`: which one survives a library calling `gc.enable()`, and why?
4. In the freeze recipe, why `gc.disable()` *early* in the parent? What would go wrong without it?
5. After `gc.freeze()`, does touching every object still copy pages? Why?
6. Σ RSS across 4 workers is 1.4 GiB on a machine with 1 GiB free, yet nothing was killed. How?
7. Which gcwatch layer would catch a C extension that calls `PyGC_Enable()` at import time? And during a later function call?
8. Why is `worker.exit_via_os_exit` a no-op for forked Kiln workers?
9. What's the difference between Private_Dirty and USS?
10. What are immortal objects, and why don't they solve copy-on-read completely?

## 14. Stretch / later

- An `madvise`/THP variant with sudo (G3.10).
- Plot the sampler time series with K9's renderer once it exists (K9 will pick up the records).
- A "Django-like" composition (classes with descriptors, `__dict__`-heavy) to compare with the plain dict graph.
