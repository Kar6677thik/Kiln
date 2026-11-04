# Lab K4: Threads, asyncio, subinterpreters and processes, with and without the GIL

> **Milestone:** [K4](../milestones/K4-concurrency-backends.md) · **Incidents:** research §2.9 (free-threading, subinterpreters, GIL re-enabling) and §2.7 (SquadStack: thread-unsafe code in a thread pool) · **Status:** final, except experiment (c), which is **not measured** (no C compiler) · **Commit:** the final commit of `milestone/k4-concurrency` (measured on top of `62e1217`) · **Run:** 2026-11-04, CPython 3.15.1 and 3.15.1t (3.13.17 for the race demo and AC-4.2)

## 1. The incident

For 30 years one rule shaped Python servers: only one thread runs Python code at a time, the **GIL** ([concepts/gil.md](../concepts/gil.md)). Threads were good for waiting and useless for computing, which is why gunicorn, Celery and Kiln run *processes*. Two things are changing that. Python 3.14 made the **free-threaded build** official (we use it as `3.15t`), where threads really run in parallel. **Subinterpreters** (PEP 684/734) give each interpreter in one process its own GIL. Both come with traps:
- importing one C extension that hasn't declared `Py_mod_gil` silently turns the GIL back on ([concepts/free-threading.md](../concepts/free-threading.md));
- code that was "accidentally safe" under the GIL starts losing updates ([concepts/data-races.md](../concepts/data-races.md)). SquadStack's production deadlocks (research §2.7) are the classic story of thread-unsafe code in a pool.

K4 gives each Kiln worker four ways to run several jobs: `sync` (one at a time, so several *processes*), `threads`, `asyncio` and `interpreters`. This lab measures which one wins for computing and for waiting, on both builds. It also plants a data race and measures it.

## 2. Your prediction

> ✍️ **Learner: fill this in BEFORE reading further.**
> (a) One worker with 8 threads runs 400 jobs that each compute for 20 ms. Roughly how many jobs per second on 3.15? On 3.15t? What about 8 worker *processes*, or 8 subinterpreters? And for 400 jobs that each *sleep* 20 ms?
> (b) Eight threads each call `stats.record("t")` 200,000 times on an unlocked counter (`count += 1`) and an unlocked dict (`d[k] = d.get(k, 0) + 1`). How many updates are lost on 3.15? On 3.15t? With a lock?
> (c) What happens when a free-threaded Python imports a C extension that doesn't declare `Py_mod_gil`?
>
> _(your answer here)_

### Implementer's hypothesis

- **(a) IO-bound** (`labs.sleep`, `labs.async_sleep`): every executor scales almost linearly with C on both builds, because waiting releases the GIL. **CPU-bound** (`labs.burn_cpu`): `threads` and `asyncio` stay at one core on 3.15 (the GIL) and scale on 3.15t. `interpreters` and processes scale on both builds, and `interpreters` costs more memory. Single-threaded, 3.15t is somewhat slower than 3.15 (research §2.9 cites about 8% on x86-64 Linux).
- **(b)** On 3.15t, `racy` loses updates in most runs. On 3.15, "rarely or never" (K4 §5.7). `locked` is always exact.
- **(c)** The GIL turns on for the whole process, with one `RuntimeWarning`. gilwatch names the module and the import chain.

## 3. Verified facts

| # | Claim | How checked | Observed | ✅/❌ |
|---|---|---|---|---|
| G4.1 | `InterpreterPoolExecutor` and `concurrent.interpreters` exist on 3.14+ | import on 3.14, 3.15, 3.15t | present on all three | ✅ |
| G4.2 | 3.15t starts with the GIL off | `sys._is_gil_enabled()` | `False` (and `Py_GIL_DISABLED` = 1); 3.14/3.15: `True` / 0 | ✅ |
| G4.3 | importing a module without `Py_mod_gil` on 3.15t flips the GIL and warns | `./scripts/dev.sh lab k4-gil` | **not measured**: no C compiler to build `kiln-gil-labs` (`sudo apt install build-essential` needed) | — |
| G4.4 | `kiln_gil_labs.without_gil` keeps the GIL off | same | **not measured** (same reason) | — |
| G4.5 | `asyncio.run` works inside an `InterpreterPoolExecutor` worker | `test_executor_contract.py::test_async_task_runs[interpreters]` | passes on 3.14, 3.15, 3.15t → decision D4-1 stands | ✅ |
| G4.6 | a lambda can't be submitted to `InterpreterPoolExecutor` | `submit(lambda: 1)` | ❌ it returns 1: stateless functions are copied as code ([errata E-24](../research/errata.md#e-24-lambdas-can-be-submitted-to-interpreterpoolexecutor)); the shim design stands for other reasons | ❌ |
| G4.6b | the racy demo loses updates on 3.15t | `lab k4-race`, 20 trials | 20/20 trials, median 196,820 lost `count` updates | ✅ |
| G4.7 | uv's 3.15t ships `Python.h` | `sysconfig.get_paths()["include"]` | present for 3.14, 3.15, 3.15t; the compiler itself is missing | ✅ (headers) |
| G4.7b | setuptools builds ext-modules from `pyproject.toml` alone | `sync --group lab-k4` | **not measured** (no compiler) | — |
| G4.8 | the single-thread cost of free-threading is in the cited range | table (a), `sync` C=1, CPU-bound | 3.15t / 3.15 = **0.87** [0.79, 0.96] (about 13% slower); research cites about 8% | ⚠️ measured, larger than cited |
| G4.9 | `PYTHON_GIL=0` keeps the GIL off after a legacy import | `lab k4-gil` | **not measured** (no compiler) | — |
| G4.10 | `thread_name_prefix` shows in `current_thread().name` | `ThreadPoolExecutor(thread_name_prefix="kiln-job")` | `kiln-job_0` | ✅ |
| new | `_tracemalloc` loads in subinterpreters | the first `interpreters` test run | ❌ `ImportError: module _tracemalloc does not support loading in subinterpreters` ([E-25](../research/errata.md#e-25-some-stdlib-c-modules-refuse-to-load-in-subinterpreters-_tracemalloc)) | ❌→fixed |
| new | `labs.burn_cpu` measures its own work | the first 3.15t smoke run | ❌ it counted the whole process's CPU: 8 threads = 1/8 of the work each ([E-26](../research/errata.md#e-26-cpu-time-in-labsburn_cpu-must-be-the-threads-not-the-processs)) | ❌→fixed |
| new | on 3.15 the racy demo "rarely or never" loses updates | `lab k4-race` | ❌ `count += 1` never did, but the dict line lost updates in 20/20 trials ([E-27](../research/errata.md#e-27-on-the-gil-build-where-a-race-can-happen-depends-on-cpythons-switch-points)) | ❌→explained |

## 4. Setup

- **Machine:** WSL2 Debian 13, kernel 6.6.87.2, Intel Core Ultra 5 225H, 14 CPUs in WSL, 1 thread per core (no SMT), 7.2 GiB RAM. WSL doesn't expose core types or frequencies (`lscpu` shows no MHz). Intel describes this chip as a hybrid of performance and efficiency cores, which isn't verified from inside WSL.
- **Interpreters:** CPython 3.15.1 (GIL) and 3.15.1t (free-threaded), plus 3.13.17 for the race demo and AC-4.2.
- **Code location:** the benchmark runs from an ext4 copy (`~/kiln-bench`, ADR-003; records say `source=ext4-snapshot`).
- **Commands:**

```bash
(wsl) $ KILN_PY=3.15  ./scripts/dev.sh lab k4-bench --repeats 5 --warmups 1   # (a), ~25 min
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh lab k4-bench --repeats 5 --warmups 1
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh lab k4-race --impl racy --repeats 20    # (b); also locked, 3.15, 3.13
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh lab k4-gil                               # (c): needs build-essential
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh run labs/k4/scan_gil.py                  # informational
```

## 5. Method

| Experiment | Varied | Measured | Fixed | Repeats |
|---|---|---|---|---|
| (a) matrix | interpreter {3.15, 3.15t} × executor {sync, threads, interpreters, asyncio} × C {1, 2, 4, 8} × workload {CPU, IO, async IO} | throughput (steady window, spec/10 §8), latency p50/p99, worker CPU utilization (Δ utime+stime / Δ wall from `/proc/<pid>/stat`), worker RSS, `gil_enabled` | 400 jobs per run, closed loop with 64 clients, logs at WARNING; for `sync` the row uses C worker *processes* (Kiln's K1 model), for the others 1 worker with C slots | 1 warm-up + 5 measured rounds, every cell once per round in a seeded shuffle (seed 20261104) |
| (b) race | build {3.13, 3.15, 3.15t} × impl {racy, locked} | lost `count` updates, lost dict updates | 8 threads × 200,000 `record()` calls, released together by a `Barrier` | 20 trials |
| (c) GIL | module imported | GIL before/after, gilwatch report, CPython's warning | fresh process per case | **not measured** |

- **Workloads:** CPU = `labs.burn_cpu(work=1100)` (a fixed amount of work, ≈ 20 ms on 3.15; [E-26](../research/errata.md#e-26-cpu-time-in-labsburn_cpu-must-be-the-threads-not-the-processs)); IO = `labs.sleep(ms=20)`; async IO = `labs.async_sleep(ms=20)`, asyncio only.
- **Fewer repeats than spec/10 asks** (5 instead of ≥ 10, 400 jobs instead of 2,000; ADR-037 §10). The full size would have taken about 10 hours. The CV of throughput is ≤ 7.8% in every cell; cells above 5% are marked † below, and their CIs are correspondingly wider.
- **Statistics:** median of the 5 runs, 95% bootstrap CI of the median (2,000 resamples), and ratio of medians 3.15t/3.15 with a bootstrap CI (spec/10 §4.3). A ratio CI that contains 1.00 means "no difference shown".
- **Validity check:** every one of the 360 runs completed 400/400 jobs, and no 3.15t run reported `gil_enabled` (any such run would have been rejected).

## 6. Results: before

"Before" is the GIL world Kiln was built for in K1: one job per worker, and processes for parallelism. Here is CPU-bound work on 3.15 with 8 slots ([records](../../results/published/K4/matrix/)):

| executor (C = 8) | jobs/s | CPU used (cores) |
|---|---|---|
| sync (8 processes) | 273.2 | 7.86 |
| threads (1 process) | **47.1** | **1.01** |
| asyncio (1 process) | **46.9** | **1.01** |

Eight threads did exactly what one does: the GIL let one of them run at a time, and the other seven waited.

## 7. The fix

Three ways to use more cores from one worker, measured in §8:
1. **The free-threaded build** (3.15t): same code, no GIL.
2. **Subinterpreters** (`interpreters` executor): one GIL per interpreter, on any 3.14+ build.
3. **Processes** (K1's model), which never had the problem.

Building K4 also needed three fixes, found by the measurements and tests (ADR-037):
1. **`burn_cpu`** counted the whole process's CPU time instead of its own thread's (E-26).
2. **`tracemalloc`** is now imported lazily, because the `_tracemalloc` C module refuses to load in subinterpreters (E-25).
3. **Worker counters** are updated under `WorkerState.lock`. The race lab (b) shows what happens without it.

## 8. Results: after

### (a) Throughput, jobs/s ([records](../../results/published/K4/matrix/); median [95% CI]; † = CV > 5%)

**CPU-bound** (`burn_cpu work=1100`):

| executor | C | 3.15 | 3.15t | ratio 3.15t / 3.15 [CI] |
|---|---|---|---|---|
| sync (C processes) | 1 | 46.9 [42.7, 50.4] † | 40.9 [39.1, 44.9] † | 0.87 [0.79, 0.96] |
| sync (C processes) | 2 | 95.4 [82.0, 98.5] † | 78.4 [77.6, 90.6] † | 0.82 [0.80, 1.04] |
| sync (C processes) | 4 | 177.5 [170.0, 179.7] | 162.5 [152.7, 165.6] | 0.92 [0.86, 0.96] |
| sync (C processes) | 8 | 273.2 [264.8, 284.4] | 251.6 [245.0, 253.3] | 0.92 [0.88, 0.95] |
| threads | 1 | 46.6 [43.8, 48.4] | 42.8 [41.7, 45.2] | 0.92 [0.88, 1.00] |
| threads | 2 | 47.1 [45.5, 50.8] | 88.8 [83.0, 91.6] | **1.89** [1.73, 1.96] |
| threads | 4 | 47.2 [43.4, 49.1] † | 159.2 [148.3, 166.7] | **3.37** [3.11, 3.70] |
| threads | 8 | 47.1 [42.5, 48.9] † | 251.9 [244.9, 259.3] | **5.35** [5.14, 5.93] |
| interpreters | 1 | 47.9 [45.1, 49.7] | 41.9 [38.5, 42.3] | 0.87 [0.80, 0.93] |
| interpreters | 2 | 95.0 [80.7, 96.1] † | 82.5 [73.6, 86.1] † | 0.87 [0.77, 1.02] |
| interpreters | 4 | 175.5 [164.0, 181.3] | 154.4 [150.1, 159.1] | 0.88 [0.85, 0.94] |
| interpreters | 8 | 271.2 [253.6, 279.3] | 247.6 [227.0, 248.7] | 0.91 [0.84, 0.98] |
| asyncio | 1 | 46.3 [44.5, 48.3] | 42.7 [36.6, 44.9] † | 0.92 [0.79, 0.97] |
| asyncio | 2 | 48.5 [45.8, 49.0] | 90.2 [78.8, 90.9] † | **1.86** [1.62, 1.97] |
| asyncio | 4 | 45.5 [42.1, 48.8] † | 164.2 [152.1, 169.9] | **3.61** [3.26, 3.90] |
| asyncio | 8 | 46.9 [46.1, 49.3] | 253.8 [248.5, 256.6] | **5.41** [5.13, 5.51] |

**IO-bound** (`sleep ms=20`; and `async_sleep ms=20` under asyncio):

| executor | C=1 3.15 / 3.15t | C=2 | C=4 | C=8 3.15 / 3.15t |
|---|---|---|---|---|
| sync (C processes) | 46.9 / 46.1 | 91.6 / 91.0 | 182.3 / 180.8 | 372.8 / 362.6 |
| threads | 45.5 / 45.0 | 94.4 / 89.2 | 187.3 / 175.1 | 359.3 / 339.3 |
| interpreters | 44.4 / 40.2 | 93.9 / 81.9 | 172.7 / 165.8 | 373.2 / 332.4 † |
| asyncio (sync task → `to_thread`) | 44.3 / 44.3 | 92.9 / 86.8 | 177.1 / 170.0 | 349.1 † / 333.1 |
| asyncio (`async_sleep`) | 44.8 / 44.4 | 91.5 / 89.3 | 181.6 / 173.3 | 361.8 / 333.8 |

### (a) CPU, memory and latency (medians; C = 1 and C = 8)

| workload | executor | C | interp | CPU (cores) | worker RSS MiB (sum) | p50 / p99 latency ms |
|---|---|---|---|---|---|---|
| CPU | sync | 8 | 3.15 / 3.15t | 7.86 / 7.86 | 188.1 / 242.2 | 232 / 246 · 253 / 270 |
| CPU | threads | 8 | 3.15 / 3.15t | **1.01 / 7.80** | 24.2 / 34.3 | 1,361 / 1,498 · 253 / 273 |
| CPU | interpreters | 8 | 3.15 / 3.15t | 7.78 / 6.52 | 124.9 / 165.0 | 235 / 327 · 259 / 732 |
| CPU | asyncio | 8 | 3.15 / 3.15t | **1.01 / 7.83** | 25.3 / 34.8 | 1,351 / 1,509 · 252 / 268 |
| CPU | any | 1 | 3.15 / 3.15t | 0.97–0.98 | 23.6–39.2 / 30.3–50.6 | ≈ 1,330–1,520 |
| IO | threads | 8 | 3.15 / 3.15t | 0.13 / 0.16 | 24.1 / 34.2 | 177 / 187 · 188 / 205 |
| IO | interpreters | 8 | 3.15 / 3.15t | 0.74 / 1.10 | 125.1 / 165.2 | 171 / 270 · 196 / 644 |
| IO | asyncio (`async_sleep`) | 8 | 3.15 / 3.15t | 0.05 / 0.08 | 24.7 / 32.6 | 176 / 184 · 192 / 198 |

(The latencies are mostly *queueing*: 64 clients share C slots, so a job waits for about 64 / C turns. At C = 1 that is about 64 × 21 ms ≈ 1.35 s, which is Little's law; see [concepts/amdahls-law.md](../concepts/amdahls-law.md).)

### (b) The planted race ([records](../../results/published/K4/race/); 8 threads × 200,000 records = 1,600,000 expected, 20 trials)

| interp | impl | trials with lost updates | lost `count` updates, median [min–max] | lost dict updates, median [min–max] | time (median) |
|---|---|---|---|---|---|
| 3.15t | racy | 20/20 | 196,820 [163,852 – 275,820] | 1,206,278 [1,175,511 – 1,236,598] | 0.60 s |
| 3.15t | locked | 0/20 | 0 | 0 | 1.29 s |
| 3.15 | racy | 20/20 | **0** [0 – 0] | 730,512 [198,991 – 1,305,164] | 0.08 s |
| 3.15 | locked | 0/20 | 0 | 0 | 0.22 s |
| 3.13 | racy | 20/20 | **0** [0 – 0] | 927,224 [453,207 – 1,252,988] | 0.13 s |
| 3.13 | locked | 0/20 | 0 | 0 | 0.32 s |

### (c) GIL re-enabling

**Not measured.** The two test extensions (`labs/k4/gil_reenabler/`) are written and documented, but building them needs a C compiler, and installing `build-essential` needs the learner's `sudo` password. gilwatch's logic is tested on every build with a simulated flip (`tests/unit/test_gilwatch.py`, 4 tests); the real-extension test (`tests/integration/test_gil_reenabler.py`) skips with that reason. To complete this section: `sudo apt install build-essential`, `KILN_PY=3.15t ./scripts/dev.sh sync --group lab-k4`, then `KILN_PY=3.15t ./scripts/dev.sh lab k4-gil`.

**Informational environment scan** (`labs/k4/scan_gil.py` on 3.15.1t, 2026-11-04; changes month to month): all 19 top-level packages in the 3.15t dev environment keep the GIL off, including the compiled ones (`mypy`/`mypyc`, `msgpack`, `coverage`).

### Acceptance load test (AC-4.2; [records](../../results/published/K4/acceptance/))

`kiln bench load --jobs 2000 --concurrency 64 --task labs.echo`, 2 workers: 2,000/2,000 succeeded with 0 failed, 0 lost and 0 submit errors for `sync`, `threads`, `asyncio` and `interpreters` on 3.15, and for `sync`, `threads` and `asyncio` on 3.13 and 3.15t. Throughput was 7,200–10,600 jobs/s: trivial jobs measure the master (K1's ceiling), not the executors.

## 9. Analysis

- **(a) CPU-bound: the hypothesis held exactly.** On 3.15, `threads` and `asyncio` delivered 47 jobs/s whatever C was, using 1.01 cores: the GIL in one number. On 3.15t the same code scaled to 252–254 jobs/s at C = 8 using 7.8 cores, 5.4× the GIL build. Processes and subinterpreters scaled on both builds (271–273 jobs/s at C = 8 on 3.15).
- **Nothing reached 8× at C = 8.** The best was 5.8× (`sync` and `interpreters` on 3.15: 273 vs 47). Every executor flattens alike from C = 4 to C = 8, so this is the machine, not Python. Likely causes, not verified from inside WSL: a hybrid CPU whose extra cores are slower, lower clock speeds with more cores busy, and the master and the load generator also needing CPU (Amdahl's serial part). [Amdahl's law](../concepts/amdahls-law.md) with s ≈ 0.07 gives 1/(0.07 + 0.93/8) ≈ 5.4, close to what we see.
- **(a) IO-bound: every executor scaled almost linearly on both builds** (44–47 jobs/s at C = 1, 332–373 at C = 8), as predicted. Waiting releases the GIL, so the build hardly matters: the 3.15t/3.15 ratios are 0.87–1.00. `asyncio` with `async_sleep` used the least CPU of all (0.05–0.08 cores at C = 8). One thread with an event loop is the cheapest way to wait.
- **The single-thread cost of free-threading is about 8–13% here.** `sync` C = 1 CPU-bound: 0.87 [0.79, 0.96]; at C = 4 and 8 (tighter CIs): 0.92. The research's "about 8%" sits inside our range, but our C = 1 point estimate is larger, and the CIs are wide because of only 5 runs.
- **Subinterpreters cost memory and tail latency.** At C = 8, the `interpreters` worker used 125 MiB (3.15) / 165 MiB (3.15t), against 24 / 34 MiB for `threads`: each subinterpreter imports Kiln and the app again. Its p99 was the worst in the table on 3.15t (732 ms CPU-bound, 644 ms IO-bound), and it reached only 6.52 cores at C = 8 CPU-bound on 3.15t. Our guess, not measured: the cost of moving each job and result across interpreters (pickling the call), and memory bandwidth.
- **(b) The race: 3.15t lost updates in every trial, as predicted.** But the GIL build surprised. `count += 1` lost **nothing** in 40 trials on 3.13 and 3.15, yet the dict line lost 0.2–1.3 *million* of 1.6 million updates in every trial. The reason is in CPython's source (E-27): the interpreter only switches threads at a few points (backward jumps, `RESUME`, and the end of most calls). There is no such point inside `self.count += 1`, but there is one right after `d.get(...)` returns, before the store. A thread paused there writes back a value one whole 5 ms time slice stale, erasing everything the other threads did meanwhile. **"The GIL makes races rare" is true per instruction, not per line of code.** `locked` was exact in all 60 trials, on every build, at a cost (3.15t: 1.29 s instead of 0.60 s, because 8 threads queue for one lock).
- **(c)** is the one prediction we couldn't test (§8 (c)).

## 10. Reference comparison

Read after building. The sources are CPython 3.15's `Lib/concurrent/futures/interpreter.py` and `Lib/concurrent/interpreters/` (installed by uv), `Python/bytecodes.c` (fetched 2026-11-04), and the documentation of pytest-run-parallel, PyVFT/threadcheck and Celery.

1. **pytest-run-parallel** (its README, fetched 2026-11-04) "runs many instances of the same test in a thread pool", optionally with a `Barrier` so threads start each iteration together, the same trick as our race demo. For the GIL it relies on CPython's own "RuntimeWarning generated when the GIL is enabled at runtime" (`--ignore-gil-enabled` silences it); the README doesn't say it names the module or the import chain, which is what gilwatch adds. A test counts as thread-unsafe by marker (`pytest.mark.thread_unsafe`), by using listed fixtures or functions (`capsys`, `monkeypatch`, …), or by calling a function with `__thread_safe__ = False` up to two levels down. `RacyStats` would be caught only by a test that checks the totals *and* happens to lose an update during the run.
2. **threadcheck** (PyPI, fetched 2026-11-04) combines static AST analysis (it looks for shared mutable state and whether locks guard it) with dynamic analysis: it instruments code "via AST transformation at import time" and tracks accesses with **vector clocks**, flagging conflicting accesses with no happens-before relationship. Such a tool would flag `RacyStats.record` from a *single* clean run, because two threads write the same attribute with no lock in between, whether or not an update was lost. That's the difference between detecting the *possibility* of a race and observing its *effect*, and the GIL build's `count += 1` shows why it matters: it never lost an update in 40 trials, yet it is just as unprotected. **PyVFT:** not read. Its PyPI page didn't load on 2026-11-04.
3. **`InterpreterPoolExecutor`'s transfer:** `Interpreter.call` passes *shareable* objects directly and copies *stateless* functions (no closure, no globals) as code, and falls back to pickle for everything else (E-24). Our shim (a module-level function with string arguments) takes the cheapest path every time, and it never depends on what a task's arguments happen to be. That confirms the design, for a different reason than the spec gave.
4. **Celery's `--pool` options:** `prefork` is Kiln's `sync` with several workers (processes); `threads` is our `threads`; `solo` is `sync` with one worker; `gevent`/`eventlet` (green threads via monkeypatching) have no Kiln equivalent. Our `asyncio` is the closest in spirit (one thread, many waits), but cooperative by `await` instead of by patching. Celery has no subinterpreter pool.

## 11. Limitations and threats to validity

- **5 measured repeats and 400 jobs per run** (spec/10 asks for ≥ 10 and the spec for 2,000; ADR-037 §10). CVs are 0.8–7.8%; some ratio CIs that touch 1.00 (`sync` C = 2) can't distinguish the builds.
- **Closed loop with 64 clients:** latencies are dominated by queueing, so they compare executors under the *same* overload, not their service time.
- **The load generator and the master share the machine** with the workers; at C = 8 they compete for cores.
- **No CPU pinning** (on purpose: pinning would cap the parallelism we measure) and an unknown core layout (WSL hides it).
- **(c) is not measured** (no compiler), and so are G4.3, G4.4, G4.7b and G4.9.
- **One race configuration** (8 threads × 200,000). The GIL-build dict losses depend on how many time slices each thread needs: with 100,000 calls per thread the docs' snippet saw none (each thread finished within one slice).

## 12. What you should now be able to explain

- [ ] Why 8 threads computing on 3.15 give 1.01 cores, and on 3.15t 7.8 (*Concurrency, GIL*)
- [ ] Why every executor helps IO-bound work on both builds (*Concurrency*)
- [ ] What subinterpreters cost (memory per interpreter, re-imports, transfer) and buy (parallelism on the GIL build) (*Concurrency, import system*)
- [ ] Why `count += 1` never lost an update on the GIL build while `d[k] = d.get(k, 0) + 1` lost most of them (*Concurrency, runtime internals*)
- [ ] Why the asyncio executor can time out an async task without killing the worker, and why not a sync one (*asyncio*)
- [ ] How Amdahl's law explains a 5.4–5.8× ceiling at 8 slots (*Performance*)
- [ ] What `Py_mod_gil` declares, and what gilwatch would report for a legacy extension (*Native code*)

## 13. Checkpoint quiz

1. Four threads each run `labs.burn_cpu` for 1 s of work on 3.15. How long does the batch take, and why? On 3.15t?
   <details><summary>Answer</summary>

   About 4 s on 3.15: the GIL lets one thread run Python at a time, so the four take turns (table (a): `threads` stays at 47 jobs/s and 1.0 cores). About 1 s on 3.15t, given at least 4 free cores: the threads run in parallel (4 cores used). Slightly more, because the free-threaded build is about 8–13% slower per thread.

   </details>
2. Why did `asyncio` with plain `labs.sleep` jobs scale just as well as with `async_sleep`?
   <details><summary>Answer</summary>

   A plain function under the asyncio executor runs on the `to_thread` pool, sized to C. Sleeping in a thread releases the GIL, so C threads sleep in parallel on any build. `async_sleep` reaches the same throughput with less CPU, because one event loop waits for all of them.

   </details>
3. On 3.15, why can `self.count += 1` from 8 threads come out exact, while that is no guarantee?
   <details><summary>Answer</summary>

   CPython (GIL build) switches threads only at certain instructions: backward jumps, `RESUME`, and the end of most calls. `+= 1` on an attribute contains none of them, so no switch can split it. That's an implementation detail, not a promise: it differs by version and build, and on 3.15t the same line lost about 200,000 updates per trial.

   </details>
4. Using Amdahl's law: if about 7% of the work can't run in parallel, what's the best speed-up with 8 cores, and with unlimited cores?
   <details><summary>Answer</summary>

   1 / (0.07 + 0.93/8) ≈ 5.4 with 8 cores, and 1 / 0.07 ≈ 14 with unlimited cores. Our measured 5.4–5.8× at C = 8 fits.

   </details>
5. Which executor would you pick for (a) calling 50 slow HTTP APIs, (b) resizing images in pure Python on 3.15, (c) the same on 3.15t?
   <details><summary>Answer</summary>

   (a) `asyncio`: waiting is what it does best, at the lowest CPU and memory. (b) Processes (`sync` with more workers) or `interpreters`: only they get past the GIL there. Processes give the most isolation; `interpreters` saves process overhead but re-imports per interpreter. (c) `threads`: the simplest, with real parallelism and the least memory, provided the code is thread-safe (lab (b)).

   </details>
