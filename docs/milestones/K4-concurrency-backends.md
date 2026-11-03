# K4 — Concurrency Backends: Threads, Subinterpreters, asyncio, and Free-Threading

> **Status:** 📝 specified · **Depends on:** K1 (worker + `executors/base.py` + `executors/sync.py`), K2 (spawn launcher, used to run workers on other interpreters), K3 (`memory/procfs.py` for CPU and RSS readings) · **Branch:** `milestone/k4-concurrency` · **Size:** L (8 steps)
> **Incident:** research §2.9 (free-threading, subinterpreters, and "importing a C extension re-enables the GIL") and §2.7 (SquadStack: a gevent/thread pool deadlocked because some code wasn't thread-safe)
> **Reference implementations (read *after* building):** [pytest-run-parallel](https://sources.debian.org/src/python-pytest-run-parallel/0.8.2-1/PKG-INFO) (it runs tests in many threads and warns when the GIL is re-enabled), [PyVFT](https://pypi.org/project/pyvft/) and [threadcheck](https://pypi.org/project/threadcheck/) (pure-Python race detectors), and CPython's `Lib/concurrent/futures/interpreter.py`

---

## 1. The story (plain English)

For about 30 years, CPython had one big rule. **Only one thread can run Python code at a time.** The rule is enforced by a lock called the **GIL** (Global Interpreter Lock). Threads were still useful for *waiting* (on the network, on disk), because a waiting thread lets go of the GIL. But four threads doing *math* ran no faster than one. To use more than one CPU core, Python programs started more **processes**. That's exactly what Kiln's workers are.

That is changing. Python 3.13 shipped an experimental **free-threaded build**, which has no GIL. Python 3.14 made it officially supported (PEP 779). In Kiln we install it as `3.15t`. On this build, threads really run in parallel. There's a catch, though. Many libraries contain **C extensions** (code written in C for speed) that were written assuming the GIL protects them. So when a free-threaded Python imports a C extension that hasn't declared "I'm safe without the GIL", it **quietly turns the GIL back on** for the whole process. It prints a single warning. Your program carries on, but it's slow again, and you might never notice. There's a second catch: code that was "accidentally safe" because of the GIL can now suffer **data races**, where two threads update the same value at once and one update gets lost. SquadStack's production deadlocks in research §2.7 are the classic cautionary tale of thread-unsafe code in a thread pool.

There's also a third option: **subinterpreters** (PEP 734, `concurrent.interpreters` in 3.14). These are several independent Python interpreters inside **one process**. Each has its own GIL, so they run in parallel even on the normal build, and they share almost nothing, so data races between them are hard to create by accident.

**In this milestone we will** teach each Kiln worker to run several jobs at once in four different ways: inline (`sync`), a thread pool (`threads`), a subinterpreter pool (`interpreters`) and an event loop (`asyncio`). We'll measure which wins for CPU-heavy work and which for waiting-heavy work, on both the normal and the free-threaded build. We'll build two tiny C extensions, one "GIL-safe" and one "legacy", to watch the GIL come back on and catch the culprit by name. Finally we'll plant a real data race in Kiln-style code, watch it lose updates on 3.15t, and fix it.

## 2. What you will learn

- **Explain** what the GIL protects, when a thread releases it (blocking I/O, `time.sleep`, every ~5 ms switch interval), and why CPU-bound threads don't speed up on the GIL build. *(Concurrency)*
- **Explain** what "free-threaded" means, how to check it (`sys._is_gil_enabled()`, `sysconfig.get_config_var("Py_GIL_DISABLED")`) and what turns the GIL back on. *(Concurrency, Native code)*
- **Predict** whether a given workload (CPU-bound or IO-bound) will speed up with threads, subinterpreters, asyncio or processes, using **Amdahl's law**. *(Concurrency, Memory & perf)*
- **Explain** what a **data race** is, why `counter += 1` isn't atomic, why built-in `dict`/`list` single operations are internally locked on the free-threaded build while *compound* operations (read-then-write) are not, and how a `threading.Lock` fixes it. *(Concurrency)*
- **Explain** how a subinterpreter differs from a thread and from a process: what is shared, what must be copied, and why the task function must be importable by name. *(Concurrency, Import system)*
- **Explain** how a C extension declares free-threading support (`Py_mod_gil` slot) and what multi-phase module initialization is. *(Native code)*
- **Explain** cooperative cancellation in asyncio versus hard killing a process. *(asyncio)*

## 3. Entry criteria

- K1 done: workers with the `sync` executor, the `Executor` protocol in `src/kiln/worker/executors/base.py`, heartbeats, the master's capacity and slot accounting, `run`/`result` messages, timeouts that SIGKILL the worker.
- K2 done: the spawn launcher (needed to run a worker on an interpreter other than the master's, see §5.8).
- K3 done: `memory/procfs.py` can read `/proc/<pid>/stat` (CPU time) and `status` (RSS). If K3 only added PSS/USS, K4 step 7 adds a `read_cpu_times(pid)` helper to `procfs.py`.
- `kiln doctor` reports `3.15` and `3.15t` installed, plus `gcc` and Python headers for both (the build-essential check from K0).

## 4. Scope

### In scope
- Executors `threads`, `interpreters` and `asyncio` in `src/kiln/worker/executors/`, plus refinements to `sync` (running async tasks).
- A thread-safe channel writer in the worker (several threads now send `result`).
- Cooperative timeout for async tasks in the `asyncio` executor, and the master-side timer offset (`jobs.cancel_grace_s`).
- `src/kiln/worker/gilwatch.py`: on free-threaded builds, detects and names the module whose import re-enabled the GIL.
- `labs/k4/gil_reenabler/`: two ~30-line C extensions (ADR-015).
- `labs/k4/race_demo.py`: the planted race and its fix, plus making sure Kiln's own worker counters are race-free.
- A new lab task `labs.async_sleep` (see §5.6).
- The benchmark lab: {CPU-bound, IO-bound} × {sync-as-processes, threads, interpreters, asyncio} × {3.15, 3.15t}.

### Out of scope (and why)
- **gevent/eventlet** (monkeypatching green threads). It's a whole different model. We mention it in the primer only, because SquadStack used it.
- **Killing one job inside a multi-slot worker.** Python can't safely kill a thread (spec/04 §5.3). A timeout kills the whole worker, and that collateral damage is documented, not "fixed".
- **Sharing memory between subinterpreters** (`memoryview` channels, queues of shareable objects). We pass JSON-compatible values only, which keeps ADR-009's rule.
- **Making the master multi-threaded.** That's forbidden by ADR-006.

## 5. Design

### 5.1 The `Executor` protocol (finalized here)

K1 created `executors/base.py`. K4 makes it final, and if K1's shape differs, K4 refactors K1's code to match this:

```python
class ResultSink(Protocol):
    def __call__(self, msg: dict) -> None: ...      # sends one `result` message; MUST be thread-safe

class Executor(Protocol):
    name: str                       # "sync" | "threads" | "interpreters" | "asyncio"
    capacity: int                   # reported in `ready`; master never sends more `run`s than this

    def start(self, registry: TaskRegistry, sink: ResultSink) -> None: ...
    def submit(self, run: dict) -> None: ...         # MUST return quickly (except sync, see below)
    def inflight(self) -> int: ...                   # reported in heartbeats
    def shutdown(self, wait: bool) -> None: ...      # graceful: wait=True finishes in-flight jobs
```

- `submit` takes the decoded `run` message (spec/02 §4.2). Eventually it calls `sink(result_msg)` exactly once per `run`, from whichever thread finished the job.
- **Sync is special.** `SyncExecutor.submit` runs the job inline on the worker's main thread and returns afterwards. That's why its capacity is 1.
- `executor_factory(config, registry) -> Executor` in `executors/__init__.py` picks the class from `worker.executor`, and imports gated modules lazily (`interpreters` needs 3.14+).

### 5.2 Worker main loop with several slots

```
main thread:  loop { msg = channel.recv_frame() ; if run → executor.submit(msg) ; if shutdown → ... ; if debug → ... }
pool threads: run task → sink(result)  ──┐
heartbeat:    every interval → sink-like send(heartbeat)  ─┤──▶ ChannelWriter (one threading.Lock around sendall)
```

- **`ChannelWriter`** (in `worker/main.py`, or `worker/channel.py` if K1 already split it): `send(msg: dict) -> None` encodes the frame *outside* the lock, then does `with self._lock: self._sock.sendall(frame)`. **Why:** two threads calling `sendall` at the same time can interleave their bytes, and that corrupts the framing. The test `test_concurrent_senders_never_interleave_frames` fires 8 threads × 1,000 messages through a real socketpair and decodes everything on the other side.
- The worker's counters (`jobs_done`, `inflight`) are updated under a `threading.Lock` (`WorkerCounters`). This is the "fixed" version of §5.7's race. The test `test_jobs_done_exact_under_threads` runs 2,000 jobs on `threads` with concurrency 8 and asserts that `jobs_done == 2000` on 3.15t.
- **Graceful shutdown** with several slots: stop calling `submit`, call `executor.shutdown(wait=True)`, then exit 0. The master's `graceful_timeout_s` still applies.
- **`max_jobs` / `max_rss` recycling** (spec/04 §5.5) is checked after *each* result inside the sink path, and the `retiring` message is sent only once (it's guarded by a flag under the counters lock).

### 5.3 `threads` executor (`executors/threads.py`)

- It wraps `concurrent.futures.ThreadPoolExecutor(max_workers=executor_concurrency, thread_name_prefix="kiln-job")`. `capacity = executor_concurrency`.
- Each job runs `_run_one(spec, run) -> result_msg`, which catches *every* exception (`BaseException` except `KeyboardInterrupt`/`SystemExit`, which are re-raised after reporting). It serializes the return value with `json.dumps(..., allow_nan=False)` to check it's JSON-safe (failure → `error.type="SerializationError"`, which the master maps to `error.kind="serialization"`).
- **Async tasks** (`spec.is_async`): `asyncio.run(func(*args, **kwargs))` inside the pool thread, giving a fresh event loop per job. **Why:** each thread needs its own loop, and creating one per job is the simplest correct rule. The cost (about tens of microseconds, to be measured in step 7) is recorded in the lab.
- Timeouts: the master's hard kill at `timeout_s` (spec/04 §5.3). Other in-flight jobs in that worker become collateral `worker_lost`.

### 5.4 `asyncio` executor (`executors/asyncio_exec.py`)

- The worker main thread stays the channel reader, the same as every other executor, so the main loop is uniform. The executor starts **one dedicated loop thread** `kiln-loop` running `loop.run_forever()`.
- `submit(run)` calls `asyncio.run_coroutine_threadsafe(self._run_job(run), loop)`.
- `capacity = executor_concurrency`.
- `_run_job`:
  - **async task:** `async with asyncio.timeout(run["timeout_s"]): value = await func(*args, **kwargs)`. On `TimeoutError` it reports `result{ok:false, error:{type:"TimeoutError", ...}, timed_out:true}`.
  - **sync task:** `value = await asyncio.to_thread(func, *args, **kwargs)`. The loop's default executor is replaced by `ThreadPoolExecutor(max_workers=executor_concurrency, thread_name_prefix="kiln-tothread")`, so slots and threads match. **No cooperative timeout** for sync tasks: cancelling the `await` would *not* stop the thread, which would keep running and silently use a slot the master thinks is free. So sync tasks rely only on the master's hard kill.
- **New `result` field `timed_out` (bool, default false).** When it's true, the master treats the attempt like its own timeout (error.kind `timeout`, `retry_on` checks `"timeout"`) but **doesn't kill the worker**, because the worker recovered cooperatively. **This extends spec/02 §4.1.** The implementer adds the field there in step 3 (it's an additive, forward-compatible change).
- **Master timer offset:** for workers whose `ready.executor == "asyncio"`, the master's per-job timer fires at `timeout_s + jobs.cancel_grace_s` (spec/04 §5.3). Uncooperative async code (code that swallows `CancelledError` or blocks the loop) gets hard-killed at that point. The lab demonstrates both outcomes with `labs.async_sleep` and a task that blocks the loop.
- Shutdown: stop accepting, `await` all in-flight jobs (up to the graceful timeout, which the master enforces), `loop.stop()`, join the thread.

### 5.5 `interpreters` executor (`executors/interpreters.py`, Python ≥ 3.14 only)

- **Version gate:** if `sys.version_info < (3, 14)`, config validation fails with exit 2: `worker.executor = "interpreters" needs Python ≥ 3.14 (this is 3.13.x). Use "threads" or a newer interpreter.`
- It wraps `concurrent.futures.InterpreterPoolExecutor(max_workers=executor_concurrency)`, and the exact name is confirmed by gate G4.1. `capacity = executor_concurrency`.
- **What runs inside the subinterpreter:** never the task function object itself. Instead the executor submits the **module-level shim** `kiln.worker.executors.interpreters._run_in_interpreter(app_spec: str, task: str, args_json: str, kwargs_json: str) -> str`. The shim:
  1. imports and caches the app *inside that subinterpreter* on first use (a module-global dict keyed by `app_spec`, which is per interpreter, because each subinterpreter has its own module objects)
  2. looks up the task in that app's registry
  3. runs it. An async task runs with `asyncio.run` inside the subinterpreter, the same rule as sync/threads (decision D4-1 below)
  4. returns a **JSON string** `{"ok":true,"value":...}` or `{"ok":false,"error":{...}}`.
- **Why a shim with string arguments?** `InterpreterPoolExecutor` has to *transfer* the callable and its arguments into another interpreter. Functions travel by pickling (by qualified name, which is why the callable must be importable at module level), and arguments are pickled or shared. Strings are the cheapest and most predictable thing to transfer, and our payloads are JSON anyway (ADR-009). The primer explains this, with a demonstration that a lambda **fails** to submit.
- **Gotchas the primer and the lab must cover:**
  - Every subinterpreter **re-imports** the app and its dependencies. Preloading in the master (K3) doesn't help them, and memory goes up per interpreter. Measure RSS.
  - C extensions that don't support subinterpreters (single-phase init, no `Py_mod_multiple_interpreters` support) raise `ImportError` inside the interpreter. The shim reports this as `error.type = "ImportError"` with the original message, and the worker logs `event=interp_import_failed`.
  - Module-level state (like a cache dict) isn't shared between subinterpreters or with the main interpreter.
- **Decision D4-1 (async tasks under `interpreters`):** run them via `asyncio.run` inside the subinterpreter. **Justification:**
  1. It's the same rule as `sync`/`threads`, so there's one rule to learn.
  2. asyncio is pure-Python plus `_asyncio`, which supports subinterpreters on 3.14+ (verified by G4.5).
  3. Rejecting would make the manifest executor-dependent, which complicates the master.
  - **Fallback if G4.5 fails:** the shim returns `error.type="UnsupportedInExecutor"`, message `async tasks cannot run under the "interpreters" executor on this Python; use "asyncio" or "threads"`, and the worker logs a WARNING at boot listing the async tasks in the manifest. This gets recorded in an ADR.

### 5.6 Async tasks under `sync`, and the new lab task

- `SyncExecutor` runs async tasks with `asyncio.run(...)` inline. That gives a fresh loop per job and blocks the main thread for the job's duration, which is fine at capacity 1.
- **New lab task `labs.async_sleep(ms)`:** `await asyncio.sleep(ms/1000)`. It's the async twin of `labs.sleep`, needed so the asyncio executor's native path can be measured. It's added to `kiln/labs/tasks.py` and to the table in spec/03 §7 (an additive change, made in step 3).
- **New lab task `labs.block_loop(ms)`:** an `async def` that calls `time.sleep(ms/1000)` **without** awaiting. That's the "uncooperative" case for §5.4. (K5 reuses the idea for the gateway.)

### 5.7 The planted data race (`labs/k4/race_demo.py`) and its fix

```python
# LAB: deliberately racy — never copy this pattern
class RacyStats:
    def __init__(self) -> None:
        self.count = 0
        self.per_task: dict[str, int] = {}
    def record(self, task: str) -> None:
        self.count += 1                                        # read, add, write: three steps
        self.per_task[task] = self.per_task.get(task, 0) + 1   # get, add, set: three steps

class LockedStats(RacyStats):
    def __init__(self) -> None:
        super().__init__(); self._lock = threading.Lock()
    def record(self, task: str) -> None:
        with self._lock:
            super().record(task)
```

- **Procedure:** `T` threads (default 8) wait on a `threading.Barrier(T)`, so they all start at the same instant (that makes the race reproducible). Each calls `record("t")` `N` times (default 200,000). Expected total: `T × N`. Lost updates are `T×N − count`, and likewise per task. The run is repeated `R` times (default 20).
- **CLI:** `KILN_PY=3.15t ./scripts/dev.sh lab k4-race --threads 8 --iters 200000 --repeats 20 --impl racy|locked` runs `labs/k4/race_demo.py`. It writes one ExperimentRecord (spec/10) per trial under `results/K4/race/` (dev.sh passes `--results-dir`, spec/08 §3).
- **Expected story** (to be verified, never assumed):
  - On 3.15t, `racy` loses updates in many runs.
  - On 3.15 (GIL build), `racy` loses updates **rarely or never**. The GIL switches threads only at bytecode boundaries every ~5 ms, but `+=` is several bytecodes, so a lost update *is possible* on the GIL build too. It's just improbable. The primer must stress this: **"the GIL makes races rare, not impossible."**
  - `locked` is always exact.
- **The dict detail** (the primer `thread-safety-of-builtins` must explain it): on the free-threaded build, each single `dict` operation (`d[k] = v`, `d.get(k)`) is internally locked (per-object locks), so the dict never gets *corrupted*. But `get` followed by `set` is two operations, so updates are still lost. The demo shows `per_task` totals going wrong while the program never crashes.
- **Kiln's real code** uses the `WorkerCounters` lock from §5.2, so Kiln itself has no such race.

### 5.8 `gilwatch.py`: who turned the GIL back on?

- **Active only** when `sysconfig.get_config_var("Py_GIL_DISABLED") == 1` (a free-threaded build). Otherwise it's a no-op that logs nothing.
- It's installed **as early as possible**: in the master before app preload, and in each worker before importing the app.
- **Mechanism:**
  1. A `sys.meta_path` wrapper finder, `GilWatchFinder`, delegates to the real finders. For every module spec it finds whose loader has `exec_module`, it wraps that loader so that `exec_module` records `before = sys._is_gil_enabled()`, runs the real import, and records `after`.
  2. When `before is False and after is True`, it records a `GilReenabled(module=<name>, import_chain=[outer..., inner], origin=<file path>)`. The import chain is a thread-local stack of module names currently importing, so the *innermost* module that flipped the GIL is named even when you imported a package that imported it.
  3. A `warnings` hook (`warnings.catch_warnings` can't be used for a whole process lifetime, so it wraps `warnings.showwarning`) also captures CPython's own `RuntimeWarning` about the GIL being enabled to load a module (the exact text is captured by gate G4.3), and cross-checks it against the finder's result.
- **Output:**
  - log `level=WARNING event=gil_reenabled module=kiln_gil_labs.with_gil detected_after_import_of=app import_chain=app>helpers>kiln_gil_labs.with_gil origin=/…/with_gil.cpython-315t-x86_64-linux-gnu.so hint="this extension did not declare Py_mod_gil; set PYTHON_GIL=0 to force (unsafe) or upgrade it"`. `module` and `detected_after_import_of` (the outermost import that triggered it) are the spec/06 §2.6 fields. `import_chain`, `origin` and `hint` are additive, and step 5 adds them to spec/06 together with the new events `gil_forced_off` (WARNING) and `interp_import_failed` (ERROR, §5.5)
  - the existing `gil_enabled` field in `ready`/`heartbeat` (spec/02) shows the effect
  - the culprit list is also exposed via `kiln stats` (a worker table column `gil`)
- **`PYTHON_GIL=0`:** if it's set, the GIL stays off even for legacy extensions. gilwatch then logs `event=gil_forced_off module=...` at WARNING ("running unsafe on purpose").
- **Environment scan (informational):** `python labs/k4/scan_gil.py` imports each top-level module in the active venv inside a **fresh subprocess** on 3.15t (one subprocess per module, because the GIL can't be turned back off once on) and reports which flip it. The lab report shows the output dated and labelled "informational; changes month to month" (ADR-015).

### 5.9 The C extension pair (`labs/k4/gil_reenabler/`, the local package `kiln-gil-labs`)

This is a small, separate Python project (spec/08 §1, §2.2). The `lab-k4` dependency group (ADR-005) installs it from its local path.

| File | Content |
|---|---|
| `pyproject.toml` | `name = "kiln-gil-labs"`, build backend `setuptools.build_meta`, and the two `ext-modules`: `kiln_gil_labs.with_gil` and `kiln_gil_labs.without_gil` (declared through a minimal `setup.py` if `pyproject`-only ext-module config isn't supported by the installed setuptools; G4.7b). Every line is commented |
| `src/kiln_gil_labs/__init__.py` | empty except for a module docstring explaining the package |
| `with_gil.c` | multi-phase init module `kiln_gil_labs.with_gil` with one function `add(a, b)`. It has **no** `Py_mod_gil` slot, so on a free-threaded build the module implicitly says "I need the GIL" |
| `without_gil.c` | identical but with `{Py_mod_gil, Py_MOD_GIL_NOT_USED}` in its slots, meaning "I'm safe without the GIL" |
| `README.md` | what each line of C does, for a reader who has never seen C. It links to `docs/concepts/c-extensions.md` |

- **Naming:** `with_gil` means *needs the GIL* (the legacy situation). `without_gil` means *runs without the GIL*. The primer states this clearly, because the names are easy to misread.
- **Build:** `KILN_PY=3.15t ./scripts/dev.sh sync --group lab-k4` (and the same for 3.15). uv builds the package with setuptools inside that interpreter's venv, so each interpreter gets its own correctly tagged `.so`. `./scripts/dev.sh lab k4-gil` does this automatically before running the GIL demo.

### 5.10 Running workers on 3.15t from a 3.15 master

There are two supported ways. The lab uses **(a)**.

- **(a) Run the whole Kiln on the target interpreter:** `KILN_PY=3.15t ./scripts/dev.sh serve-demo ...` (spec/08 §3 picks the interpreter and venv from `$KILN_PY`). The master and the workers share one interpreter. This is the simplest and has no mixed versions.
- (b) `start.method="spawn"` with a future `start.worker_python` key. This is **not** added in K4, and it's listed in Stretch.

### 5.11 Config keys used

`worker.executor`, `worker.executor_concurrency`, `jobs.cancel_grace_s`, `master.workers`, `labs.enabled` (all in spec/05). No new keys.

## 6. Steps

1. **Executor protocol + thread-safe channel writer + counters lock.**
   - *Docs first:* `concepts/threads.md`, `python/threading.md`, `python/locks.md`.
   - *Tests:* frame interleaving test; counters exactness test (skipped until step 2 has a threaded executor; use a stub executor).
   - *Code:* §5.1, §5.2.
   - *Done when:* `sync` still passes all K1 tests through the new protocol.
2. **`threads` executor.**
   - *Docs:* `concepts/gil.md`, `python/concurrent-futures.md`.
   - *Tests:* capacity respected (the master never sends more than C `run`s; assert this from the master's side); exceptions become `FAILED`; a non-JSON return becomes `serialization`; timeout kills the worker and other in-flight jobs become `worker_lost` with the "collateral" message; graceful shutdown waits for in-flight jobs.
   - *Done when:* the K1 load test passes with `--executor threads`.
3. **`asyncio` executor + `timed_out` + `labs.async_sleep`/`labs.block_loop`.**
   - *Docs:* `python/async-await-basics.md`, `python/asyncio-run.md`, and the cooperative-cancellation section of `concepts/cpu-vs-io-bound.md` (or a short `concepts/cooperative-cancellation.md` if it gets long). Update spec/02 §4.1 and spec/03 §7 (additive).
   - *Tests:* the async task timeout yields `TIMED_OUT` with no worker kill (check `kill_sent` is None); `block_loop` past `timeout_s + cancel_grace_s` yields a hard kill; a sync task under asyncio runs in a `kiln-tothread` thread (assert the thread name inside the task via a lab task variant).
3b. *(Same step, same commit)* **Sync executor runs async tasks via `asyncio.run`.** Test: an async task under `sync` returns the right value.
4. **`interpreters` executor (3.14+).**
   - *Docs:* `concepts/subinterpreters.md`.
   - *Gates:* G4.1, G4.5, G4.6.
   - *Tests:* marked `requires_314` (spec/09 §8); a task runs and returns; a module-level cache isn't shared between interpreters (a lab task returns `id()` of a module dict and `os.getpid()`, so the same pid with different module ids is observed); an ImportError inside the interpreter is reported cleanly; config validation fails on 3.13 with the spec'd message.
5. **gilwatch + C extension pair** (`./scripts/dev.sh lab k4-gil`).
   - *Docs:* `concepts/free-threading.md`, `concepts/c-extensions.md`, `labs/k4/gil_reenabler/README.md`.
   - *Gates:* G4.2, G4.3, G4.4, G4.7, G4.7b, G4.9.
   - *Tests* (marked `requires_ft`; skipped with a reason elsewhere): importing `kiln_gil_labs.without_gil` keeps the GIL off and records nothing; importing `kiln_gil_labs.with_gil` through a wrapper package `labs/k4/wrapper_pkg` gets reported with the chain `wrapper_pkg>kiln_gil_labs.with_gil`; with `PYTHON_GIL=0`, `gil_forced_off` is logged.
6. **Race demo + fix.**
   - *Docs:* `concepts/data-races.md`, `concepts/thread-safety-of-builtins.md`.
   - *Gate:* G4.6b.
   - *Code:* §5.7.
   - *Tests:* follow spec/09 §8's special rule for the K4 race test. `tests/lab/test_race_demo.py` is marked `requires_ft`. It asserts that the `locked` version is exact in all 20 trials. It also asserts that `racy` loses updates in **at least one of 20 trials**. If `racy` never loses an update, the test is reported as `xfail(strict=False)` with the message "race not reproduced on this run; see docs/labs/K4-concurrency.md". It never fails CI, and it never counts as a pass. On GIL builds the `racy` half is skipped with a reason. The full statistics come from the lab (§7), not from this test.
7. **Benchmark lab.**
   - *Docs:* `concepts/cpu-vs-io-bound.md`, `concepts/amdahls-law.md`.
   - *Code:* `labs/k4/cpu_io_bench.py` drives §7. Add `procfs.read_cpu_times` if it's missing.
8. **Lab report, code tour, reference comparison**, plus scanning the environment with `scan_gil.py`.

## 7. Lab design

- **Question:** for CPU-bound and IO-bound jobs, how does throughput scale with in-worker concurrency under each executor, on the GIL build versus the free-threaded build? What does the free-threaded build cost single-threaded?
- **Implementer's hypothesis:**
  - IO-bound (`labs.sleep`, `labs.async_sleep`): `threads` and `asyncio` scale almost linearly with concurrency on **both** builds, because sleeping releases the GIL.
  - CPU-bound (`labs.burn_cpu`): `threads` doesn't scale on 3.15 (the GIL), but scales on 3.15t up to the core count. `interpreters` scales on **both** builds (per-interpreter GIL), at the price of higher RSS. Single-thread `sync` on 3.15t is somewhat slower than on 3.15 (research §2.9 cites "about 8% on x86-64 Linux"; we measure our own).
  - Amdahl: the master's dispatch path is serial, so total throughput saturates eventually.
- **Independent variables:**
  - interpreter ∈ {3.15, 3.15t}
  - executor ∈ {sync, threads, interpreters, asyncio}
  - concurrency C ∈ {1, 2, 4, 8}
  - workload ∈ {`labs.burn_cpu` ms=20, `labs.sleep` ms=20, `labs.async_sleep` ms=20 (asyncio only)}
- **Process-parallel reference row:** `sync` with `master.workers = C` (that's Kiln's K1 model).
- **For in-worker rows:** `master.workers = 1`, `executor_concurrency = C`. That isolates in-process concurrency.
- **Dependent variables:**
  - throughput (jobs/s, from `kiln bench load`)
  - p50/p99 latency (ms)
  - worker CPU utilization (Δ(utime+stime)/Δwall from `/proc/<pid>/stat`, where 1.0 = one core)
  - worker RSS (MiB) at the end of the run
  - `gil_enabled` from the heartbeat (must be `false` on 3.15t, otherwise the run is invalid)
- **Controls:**
  - same machine, same commit, run from the ext4 snapshot (ADR-003)
  - `--jobs 2000 --concurrency 64` for load
  - logs at WARNING to cut logging overhead
  - nothing else heavy running (record the load average per spec/10)
- **Repeats and statistics:** per [spec/10](../spec/10-measurement-methodology.md): 3 warm-ups, ≥ 10 measured repeats per cell, variants interleaved round-robin (§3), a fresh server process per run, bootstrap CIs and ratio-of-medians comparisons with the interpretation rules (§4.3). Throughput uses spec/10 §8's measured window. CPU pinning is **not** used here, because pinning would cap exactly the parallelism we're measuring. Record `pinned_cpus: null`.
- **Procedure:** `./scripts/dev.sh lab k4-bench` runs `labs/k4/cpu_io_bench.py`. For each cell it starts `kiln serve examples.demo:app --labs --workers W --executor E` with `executor_concurrency` set via env `KILN_WORKER__EXECUTOR_CONCURRENCY=C`, waits for READY, then runs `kiln bench load --jobs 2000 --concurrency 64 --task <T> --args '<json>'`, samples worker CPU and RSS, stops the server, and writes the records.
  - **Note:** `kiln bench load` needs an `--args JSON` option. spec/05 §4 lists only `--task`. K1 should have added it. If not, step 7 adds `--args/--kwargs` (an additive change, and spec/05 gets updated).
- **Result tables** (headers only):
  - *Throughput (jobs/s), workload W:* `executor | C | 3.15 median [95% CI] | 3.15t median [95% CI] | ratio 3.15t/3.15 [CI]`
  - *Latency:* `executor | C | interp | p50 ms | p99 ms`
  - *CPU and memory:* `executor | C | interp | CPU util (cores) | RSS MiB | gil_enabled`
  - *Single-thread cost of free-threading:* `workload | 3.15 jobs/s | 3.15t jobs/s | ratio [CI]` (sync, W=1, C=1)
  - *Race demo:* `interp | impl | runs | runs with lost updates | median lost updates [min–max]`
  - *GIL re-enabling:* `module imported | build | gil before | gil after | gilwatch report | warning text`

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G4.1 | `concurrent.futures.InterpreterPoolExecutor` and `concurrent.interpreters` exist on 3.14+ and 3.15 | `uv run --python 3.14 python -c "from concurrent.futures import InterpreterPoolExecutor; import concurrent.interpreters as ci; print(ci.create)"` | if the names differ, use the real names, add an ADR + errata. If it's missing entirely on 3.14, gate the executor to 3.15 |
| G4.2 | `sys._is_gil_enabled()` exists (3.13+) and returns `False` at startup on 3.15t | `uv run --python 3.15t python -c "import sys; print(sys._is_gil_enabled())"` | if it's `True` at startup, find out why (`PYTHON_GIL` env? the site-packages import a legacy ext?), record it, and use `python -I` for the lab |
| G4.3 | importing a module without `Py_mod_gil` on 3.15t flips the GIL on and emits a RuntimeWarning naming the module | import `kiln_gil_labs.with_gil` with `-W always`, print `sys._is_gil_enabled()` before and after, capture stderr | if there's no warning, gilwatch relies on the finder only. Document it |
| G4.4 | importing `kiln_gil_labs.without_gil` keeps the GIL off | same as above | it indicates a build mistake. Inspect the slots and fix |
| G4.5 | `asyncio.run` works inside an `InterpreterPoolExecutor` worker | submit the shim with an async lab task | Decision D4-1 fallback (§5.5) + ADR |
| G4.6 | functions submitted to `InterpreterPoolExecutor` must be importable (a lambda fails) | submit `lambda: 1`, capture the error | if lambdas work (pickling by value?), record it. The shim design still stands, for import-cost reasons |
| G4.6b | the racy demo loses updates on 3.15t | `race_demo.py --impl racy` on 3.15t | increase the pressure using a defined search: threads ∈ {8, 16, 32} × iters ∈ {2e5, 1e6}. If still 0 lost updates, report honestly and explain (per-object locking? specialization?). The lab conclusion must reflect the observation |
| G4.7b | the installed setuptools can declare C ext-modules from `pyproject.toml` alone | `uv sync --group lab-k4` succeeds | add a 10-line `setup.py` with `ext_modules=[...]` next to `pyproject.toml` (fully commented) |
| G4.7 | uv's 3.15t ships the headers (`Python.h`) needed to build C extensions | `uv run --python 3.15t python -c "import sysconfig; print(sysconfig.get_paths()['include'])"`, then `ls` it | build a debug-free CPython 3.15t from source in `~/.local/cpython-3.15t` (documented), or as a last resort use an old pinned third-party extension known to lack the slot (that one is date-dependent; ADR) |
| G4.8 | the single-thread cost of free-threading is in the range the research cites | measure it (§7 table) | it's not a gate on the design. Just report what we measured next to the cited figure |
| G4.9 | `PYTHON_GIL=0` keeps the GIL off even after a legacy import | env var + import + check | record it, and drop that test |
| G4.10 | `ThreadPoolExecutor` thread naming via `thread_name_prefix` is visible in `threading.current_thread().name` | trivial check | adjust the test |

## 9. Acceptance criteria

- **AC-4.1** All four executors pass the shared executor contract test suite (`tests/worker/test_executor_contract.py`, parametrized over executors and skipping `interpreters` on < 3.14). The suite covers result, exception, serialization error, capacity, graceful shutdown and async task.
- **AC-4.2** The K1 load test (`kiln bench load --jobs 2000 --concurrency 64`) finishes with 0 errors for every executor on 3.15, and for `threads`/`asyncio`/`sync` on 3.13 and 3.15t.
- **AC-4.3** Asyncio cooperative timeout: an async task exceeding `timeout_s` ends `TIMED_OUT` **without** a worker kill. An uncooperative task gets hard-killed at `timeout_s + cancel_grace_s` (`tests/integration/test_asyncio_timeouts.py`).
- **AC-4.4** On 3.15t, gilwatch names `kiln_gil_labs.with_gil` as the culprit, with the correct import chain, and doesn't report `kiln_gil_labs.without_gil` (`tests/worker/test_gilwatch.py`).
- **AC-4.5** `WorkerCounters` is exact under the threads executor on 3.15t (2,000 jobs, C=8).
- **AC-4.6** The race demo results (racy vs locked × 3.15 vs 3.15t) are published to `results/published/K4/race/`, and the lab report explains them, whatever they turned out to be.
- **AC-4.7** The benchmark matrix in §7 is complete (or each missing cell says `not measured` with a reason) and published to `results/published/K4/matrix/`.
- **AC-4.8** `worker.executor = "interpreters"` on 3.13 fails config validation with exit code 2 and the exact message from §5.5.
- **AC-4.9** The Docs deliverables below exist and `./scripts/dev.sh docs-check` passes.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/threads.md` | what a thread is vs a process; shared memory; the OS scheduler; `threading.Thread`; daemon threads; why the master has none (ADR-006) |
| Concept | `docs/concepts/gil.md` | what the GIL protects (refcounts, interpreter state); the switch interval; release on I/O; why CPU threads don't scale; history |
| Concept | `docs/concepts/free-threading.md` | the `t` build; `Py_GIL_DISABLED`; `sys._is_gil_enabled()`; what re-enables the GIL; `PYTHON_GIL`; costs; biased refcounting (a short intro); the gilwatch design |
| Concept | `docs/concepts/subinterpreters.md` | PEP 684 per-interpreter GIL; PEP 734 `concurrent.interpreters`; what's shared and what isn't; how objects move (pickle/share); re-import cost; extension compatibility |
| Concept | `docs/concepts/data-races.md` | read-modify-write; lost updates; why races are timing-dependent; Barrier for reproduction; locks; "the GIL makes races rare, not impossible" |
| Concept | `docs/concepts/thread-safety-of-builtins.md` | per-object locks on the FT build; single op vs compound op; sharing an iterator isn't safe (research §2.9); `queue.Queue` as the safe hand-off |
| Concept | `docs/concepts/c-extensions.md` | what a `.so` is; `PyInit_*`; single- vs multi-phase init; module slots; `Py_mod_gil`; building with setuptools; ABI tags in filenames (sets up K8) |
| Concept | `docs/concepts/cpu-vs-io-bound.md` | definitions with examples; how to tell (CPU utilization); which model fits which; cooperative cancellation (or a separate primer) |
| Concept | `docs/concepts/amdahls-law.md` | the formula with a worked example from our measurements; serial fraction = the master's dispatch path |
| Python | `docs/python/threading.md` | `Thread`, `start`/`join`, `current_thread`, `Barrier`, `Event` |
| Python | `docs/python/concurrent-futures.md` | executors, `submit`, `Future`, `result()`, callbacks, `shutdown(wait=)` |
| Python | `docs/python/async-await-basics.md` | `async def`, `await`, coroutine objects, "async is not parallel" |
| Python | `docs/python/asyncio-run.md` | `asyncio.run`, the loop lifecycle, `run_coroutine_threadsafe`, `to_thread`, `asyncio.timeout` |
| Python | `docs/python/locks.md` | `threading.Lock`/`RLock`, `with lock:`, deadlock basics (link SquadStack) |
| C notes | `labs/k4/gil_reenabler/README.md` | line-by-line C walkthrough |
| Tour | `docs/tours/K4.md` | ≥ 5 stops: base protocol → ChannelWriter → threads → asyncio_exec → interpreters shim → gilwatch → race demo |
| Lab | `docs/labs/K4-concurrency.md` | per the lab template; all tables in §7 |
| Glossary | `docs/glossary.md` | the terms in the final report of this spec pass + any new ones |

## 11. Reference comparison assignment

Read, **after** our version works:
1. **pytest-run-parallel:** how it runs each test in N threads, and how it detects and warns that the GIL was re-enabled. *Questions:* does it name the culprit module like gilwatch does? How does it decide a test is "thread-unsafe"? What would it say about `RacyStats`?
2. **PyVFT / threadcheck:** how a pure-Python race detector works (instrumentation? `sys.settrace`/`sys.monitoring`? lockset or happens-before?). *Question:* would it catch `RacyStats.record` without any lost update actually happening? Why is that difference (detecting the *possibility* versus observing the *effect*) important?
3. **CPython `Lib/concurrent/futures/interpreter.py`:** how `InterpreterPoolExecutor` transfers the callable and arguments. *Question:* does it pickle, share, or both? Does that confirm our shim design?
4. **Celery's `--pool` options** (prefork, threads, solo, gevent/eventlet): map each one to a Kiln executor. *Question:* which Celery pool is Kiln's `sync` + several workers?

## 12. Risks and pitfalls

- **Benchmarks run on the wrong build.** A 3.15t run where the GIL was silently re-enabled (by some dev dependency) is worthless. Every record stores `gil_enabled` from heartbeats, and the lab script **rejects** runs where it's `true` on 3.15t.
- **Flaky tests from races.** Only the one spec/09 §8 race test may depend on a race happening, and it uses `xfail(strict=False)`. Every other test asserts deterministic properties only (for example, that the locked version is exact).
- **`asyncio.run` inside threads leaks loops** if exceptions escape. `asyncio.run` closes the loop for us, so don't replace it with a hand-made loop.
- **Subinterpreter memory.** C=8 interpreters × re-imported app can be large. Check against ADR-023's 3 GiB budget before running the matrix (measure C=8 once first).
- **The default `to_thread` pool size** (`min(32, os.cpu_count()+4)`) would silently exceed capacity. We replace it (§5.4).
- **Interleaved frames** if any code path bypasses `ChannelWriter`. Grep for `sendall` in `worker/` and make sure only `ChannelWriter` calls it.
- **WSL CPU count (14)** is higher than C=8, so CPU scaling stays visible. Note SMT/hyper-threading in the lab report (`lscpu`).

## 13. Checkpoint quiz (seed questions)

1. Four threads each run `labs.burn_cpu` for 1 s on 3.15. Roughly how long does the batch take, and why? On 3.15t?
2. Why does `time.sleep` in a thread *not* block other threads on the GIL build?
3. `counter += 1` from 8 threads, 1,000,000 times each, on 3.15t: what can the final value be? Why might it come out exact on the GIL build anyway, and why isn't that a guarantee?
4. On the free-threaded build, can `d[k] = d.get(k, 0) + 1` corrupt the dict? Can it lose updates? Explain the difference.
5. What does a C extension have to declare to keep the GIL off, and what happens if it doesn't?
6. Why does our interpreters executor send a *function name and JSON strings* instead of the function object?
7. A job in a `threads` worker times out. What happens to the other 7 jobs in that worker, and why can't Kiln do better?
8. Why does the asyncio executor give *sync* tasks no cooperative timeout?
9. Using Amdahl's law: if 10% of each job's time is spent in the serial master path, what is the maximum speedup from adding workers?
10. Which executor would you pick for (a) calling 50 slow HTTP APIs, (b) resizing images in pure Python on 3.15, (c) the same on 3.15t? Justify each.

## 14. Stretch / later

- `start.worker_python`: spawn workers on a different interpreter than the master (§5.10(b)).
- A `sys.monitoring`-based (PEP 669) lockset race detector for `RacyStats`, compared with PyVFT.
- `InterpreterPoolExecutor` with shareable `memoryview` payloads for large binary args.
- Thread-safety stress mode for Kiln's own test suite (in the spirit of pytest-run-parallel).
