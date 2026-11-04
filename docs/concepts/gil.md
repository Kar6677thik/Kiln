# The GIL (Global Interpreter Lock)

> **Milestone:** K4 · **Read before:** [threads](threads.md), [refcounting](refcounting.md) · **Reading time:** ~11 min
> **Terms introduced:** **GIL (Global Interpreter Lock)**, **switch interval**, **CPU-bound**, **I/O-bound**, **releasing the GIL**

## One-sentence summary

On a normal CPython build, one lock (the **GIL**) must be held to run Python bytecode, so only one thread runs Python code at a time; threads still help when they *wait*, because waiting releases the lock.

## Why you should care

K4 benchmarks Kiln's executors on CPU-heavy and waiting-heavy jobs. Without the GIL in your head, the results look random: four threads finish waiting jobs four times faster but finish arithmetic jobs no faster at all. The GIL is also why Kiln's K1 design used *processes* for parallelism, and why the free-threaded build ([free-threading](free-threading.md), research [§2.9](../research/python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver)) is such a big change.

## The mental model

**Analogy:** a kitchen with one chef's knife. Any number of cooks (threads) can work in the kitchen, but only the one holding the knife can chop (run Python code). A cook who is waiting for water to boil (I/O, `time.sleep`) puts the knife down so someone else can chop. Every ~5 ms, whoever has the knife is asked to pass it on.

**Where the analogy fails:** the "ask to pass it on" isn't checked at every instant. CPython only checks at certain points in the bytecode (function calls, loop back-edges), which matters for [data-races](data-races.md). And C code can put the knife down while it works (big `hashlib` hashes, `zlib`, many NumPy operations), so not every CPU-heavy call blocks other threads.

```
 GIL build, 4 CPU-bound threads:        GIL build, 4 sleeping threads:
 core 0 ███░░░███░░░███░░░   T1          T1 ─sleep 0.2s─────────┐
 core 1 ░░░███░░░███░░░███   T2          T2 ─sleep 0.2s─────────┤ all wait at once:
 core 2 (idle)                           T3 ─sleep 0.2s─────────┤ 0.2 s in total
 core 3 (idle)                           T4 ─sleep 0.2s─────────┘
        └ only the GIL holder runs Python; the others wait for it
 free-threaded build, 4 CPU-bound threads: every core runs its own thread at the same time
```

## See it for yourself

[`snippets/gil_1.py`](snippets/gil_1.py) times a pure-Python loop on 1 thread, then the same loop on 4 threads at once (4 times the work), then 4 threads that each sleep 0.2 s:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/gil_1.py                 # 3.15 (GIL)
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh run docs/concepts/snippets/gil_1.py   # free-threaded
```

```text
build: GIL, GIL enabled now: True
switch interval: 0.005 s
CPU work, 1 thread : 0.14 s
CPU work, 4 threads: 0.58 s  (4x the work; 1.0x speed-up)
4 threads each sleeping 0.2 s: 0.20 s in total
```

```text
build: free-threaded, GIL enabled now: False
switch interval: 0.005 s
CPU work, 1 thread : 0.14 s
CPU work, 4 threads: 0.16 s  (4x the work; 3.4x speed-up)
4 threads each sleeping 0.2 s: 0.20 s in total
```

(3.15.1 and 3.15.1t, on a 14-core machine.)

- **CPU-bound** work (the loop) on 4 threads took 4× as long as on 1 thread with the GIL: **no speed-up**. The threads took turns.
- The same 4 threads on the free-threaded build ran in parallel: **3.4× faster** (not 4×: starting threads and sharing memory cost something).
- **I/O-bound** work (sleeping) overlapped perfectly on *both* builds: 4 × 0.2 s of sleeping took 0.2 s, because a sleeping thread **releases the GIL**.
- The **switch interval** is 5 ms: how long a thread may hold the GIL before another waiting thread asks for it.

## How it really works

- **What the GIL protects.** CPython's internals were written assuming one thread at a time: reference counts ([refcounting](refcounting.md)) are plain integer increments, the memory allocator and many internal caches have no locks of their own. One big lock around "running bytecode" made all of that safe and kept single-threaded code fast. The lock lives in the interpreter's eval loop (`Python/ceval_gil.c`).
- **Who holds it.** A thread must hold the GIL to execute bytecode or touch Python objects. Blocking calls in the standard library release it around the system call: `time.sleep`, `socket.recv`, `file.read`, `select`. The C pattern is `Py_BEGIN_ALLOW_THREADS ... Py_END_ALLOW_THREADS`.
- **The switch interval.** A waiting thread sets a "please drop the GIL" request after `sys.getswitchinterval()` (5 ms by default). The running thread notices that request only at **eval-breaker** check points: function entry and loop back-edges. That's why `x += 1` is never split by a GIL handover, but a function call between a read and a write can be ([data-races](data-races.md)).
- **Why CPU threads don't scale.** Pure-Python arithmetic never blocks, so it only gives up the GIL when asked, and then another thread does the same. At most one core runs Python at a time. Processes (Kiln's K1 model) or subinterpreters with their own GIL ([subinterpreters](subinterpreters.md)) get around it; so does the free-threaded build.
- **A short history.** The GIL dates from 1992. Attempts to remove it (Greg Stein's 1999 patch, Larry Hastings' "Gilectomy") made single-threaded code much slower. PEP 703 (accepted 2023) took a different route: biased reference counting and per-object locks ([free-threading](free-threading.md)). It shipped as an experimental build in 3.13 and became officially supported in 3.14 (PEP 779).

## How Kiln uses it

- `src/kiln/worker/executors/threads.py`: K4's lab measures exactly the effect above. `threads` should scale for `labs.sleep` on every build, and for `labs.burn_cpu` only on 3.15t.
- `src/kiln/worker/heartbeat.py`: the heartbeat thread needs the GIL for a moment every interval. K1's `labs.hold_gil` holds the GIL inside C code so the heartbeat starves, which is how a hung worker is detected.
- Every `ready` and `heartbeat` message carries `gil_enabled` (`sys._is_gil_enabled()`), so the master and the K4 lab know which situation a worker is in.

## Common misconceptions

- ❌ **Myth:** "Python can't run threads in parallel, so threads are useless."
  ✅ **Reality:** threads that wait (network, disk, sleep) overlap perfectly; above, 4 sleeps took the time of one. Only CPU-bound *Python* code is serialized.
- ❌ **Myth:** "with the GIL my code is thread-safe."
  ✅ **Reality:** the GIL protects CPython's internals, not your multi-step logic. A read-modify-write that contains a GIL handover point can lose updates on a GIL build too ([data-races](data-races.md)).
- ❌ **Myth:** "every C call holds the GIL."
  ✅ **Reality:** well-written C extensions release it during long work; that's why some NumPy-heavy threaded code does scale.

## Check yourself

1. Four threads each run `labs.burn_cpu` for 1 s on 3.15. Roughly how long does the batch take? On 3.15t?
   <details><summary>Answer</summary>

   About 4 s on 3.15: the threads take turns holding the GIL, so the work is serialized. About 1 s on 3.15t (a bit more), because each thread runs on its own core.

   </details>
2. Why does `time.sleep` in one thread not stop the other threads on the GIL build?
   <details><summary>Answer</summary>

   `time.sleep` releases the GIL before asking the OS to sleep and takes it back afterwards, so other threads can run Python code meanwhile.

   </details>
3. Predict: the snippet's CPU test with 8 threads on 3.15. Time compared with 1 thread?
   <details><summary>Answer</summary>

   About 8×: 8 times the work, still one core's worth of Python execution at a time.

   </details>

## Go deeper

- [Python docs: `sys.setswitchinterval`](https://docs.python.org/3.15/library/sys.html#sys.setswitchinterval).
- [PEP 703](https://peps.python.org/pep-0703/): making the GIL optional, including why earlier attempts failed.
- David Beazley, "Understanding the Python GIL" (2010 talk): the classic explanation of the old GIL's behaviour.
- `Python/ceval_gil.c` in the CPython source: the GIL itself.
