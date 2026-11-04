# CPU-bound vs I/O-bound work (and cooperative cancellation)

> **Milestone:** K4 · **Read before:** [gil](gil.md), [threads](threads.md), [event-loops](event-loops.md) · **Reading time:** ~11 min
> **Terms introduced:** **CPU-bound**, **I/O-bound**, **CPU utilization**, **cooperative cancellation**, **`asyncio.timeout`**, **`CancelledError`**

## One-sentence summary

A job is **CPU-bound** when it spends its time computing and **I/O-bound** when it spends its time waiting; the difference decides which concurrency model helps, and how (or whether) a running job can be stopped.

## Why you should care

K4 gives each Kiln worker four ways to run jobs: `sync`, `threads`, `interpreters` and `asyncio`. The right one depends almost entirely on this distinction: threads and asyncio shine for waiting, processes and subinterpreters (or the free-threaded build) for computing. And the asyncio executor can stop a timed-out job *cooperatively* only if the job is the waiting kind; otherwise Kiln has to kill the whole worker.

## The mental model

**Analogy:** a chef (a CPU core). A **CPU-bound** job is chopping: the chef's hands are busy the whole time, and a second chef is the only way to chop faster. An **I/O-bound** job is waiting for the oven: while one dish bakes, the same chef can start ten more. Waiting is cheap to overlap; computing isn't.

**Where the analogy fails:** real jobs mix both. A web request might wait 50 ms for a database and then spend 5 ms building JSON. Measure, don't guess.

```
               CPU-bound (labs.burn_cpu)          I/O-bound (labs.sleep, labs.async_sleep)
 CPU use       ~1 core per running job            ~0: the thread is asleep, waiting
 threads (GIL) no speed-up (one runs at a time)   near-linear speed-up (waiting releases the GIL)
 threads (3.15t) speed-up up to the core count    near-linear speed-up
 asyncio       no speed-up (one thread)           excellent: thousands of waits on one thread
 interpreters  speed-up (a GIL per interpreter)   works, but heavier than threads
 processes     speed-up (Kiln's K1 workers)       works, most memory
```

## See it for yourself

The [gil](gil.md) primer measured the core of it: four CPU-bound threads gave a 1.0× speed-up on 3.15 and 3.4× on 3.15t, while four 0.2 s sleeps took 0.20 s in total on both. The K4 lab repeats this with Kiln's own executors and tasks.

**How to tell which kind a job is: CPU utilization.** Divide the CPU time a process used (user + system, from `/proc/<pid>/stat` or the shell's `time`) by the wall-clock time. About 1.0 per busy thread means CPU-bound; close to 0 means it mostly waited. For example, a one-second sleep:

```bash
(wsl) $ time python -c "import time; time.sleep(1)"
```

```text
real	0m1.013s
user	0m0.005s
sys	0m0.005s
```

(3.15.1.) One second of wall-clock time, a hundredth of a second of CPU: utilization about 0.01, as I/O-bound as it gets.

## How it really works

- **CPU-bound** code keeps a core busy: arithmetic, parsing, compression, image resizing. In pure Python on the GIL build, only one thread at a time can do it ([gil](gil.md)), so parallelism needs processes, subinterpreters or the free-threaded build.
- **I/O-bound** code mostly waits: sockets, disks, `sleep`, other services. A waiting thread releases the GIL, and an asyncio task gives control back to the event loop at each `await`, so thousands of waits overlap cheaply.
- **Which model fits:** threads or asyncio for waiting (asyncio is lighter per task, but every library on the path must be async); processes, subinterpreters or 3.15t threads for computing.

### Cooperative cancellation

Stopping a job that's already running is a separate problem, and it's where the two kinds differ most.

- **A thread can't be cancelled at all.** Python has no safe way to stop another thread. The only option is to kill the whole process. Kiln's master does exactly that when a job exceeds `timeout_s`, and every other job running in that worker is lost as collateral (spec/04 §5.3).
- **An asyncio task can be cancelled, cooperatively.** `asyncio.timeout(s)` arranges for a **`CancelledError`** to be raised *inside* the task at its next `await` after the deadline. The task unwinds (running its `finally` blocks), and the `async with` block turns the cancellation into `TimeoutError`.
- **But only at an `await`**, and only if the task cooperates. [`snippets/cpu-vs-io-bound_1.py`](snippets/cpu-vs-io-bound_1.py) gives three tasks a 0.1 s timeout:

```text
polite    -> TimeoutError         after 0.1 s
stubborn  -> ignored the cancel   after 0.4 s
blocker   -> finished             after 0.5 s
```

(3.15.1; the same on 3.15.1t.)

  - `polite` awaits `asyncio.sleep(1)`: cancelled at that await after 0.1 s.
  - `stubborn` catches `CancelledError` and keeps working: the timeout can't stop code that refuses (a bug, but a common one).
  - `blocker` calls `time.sleep(0.5)` without awaiting: there's no `await` to deliver the cancellation at, and the whole event loop is blocked meanwhile. It simply finishes.
- **Kiln's two-stage rule.** The `asyncio` executor runs async tasks under `asyncio.timeout(timeout_s)`. If the task cooperates, the worker reports `timed_out: true` and keeps running; the master records a timeout *without* killing the worker. As a safety net, the master's own timer for asyncio workers fires at **`timeout_s + jobs.cancel_grace_s`**: a stubborn or blocking task gets the worker hard-killed then. Sync tasks under `asyncio` run in a thread via `asyncio.to_thread`, and cancelling that `await` wouldn't stop the thread, so they get no cooperative timeout at all, only the hard kill.

## How Kiln uses it

- `labs.burn_cpu` (CPU-bound), `labs.sleep` and `labs.async_sleep` (I/O-bound): the K4 lab's workloads.
- `labs.block_loop`: an `async def` that calls `time.sleep` without awaiting, the uncooperative case.
- `src/kiln/worker/executors/asyncio_exec.py`: `asyncio.timeout` for async tasks, `asyncio.to_thread` for sync ones.
- The master: the per-job timer at `timeout_s` (or `timeout_s + jobs.cancel_grace_s` for asyncio workers), then SIGKILL.

## Common misconceptions

- ❌ **Myth:** "async makes code run in parallel."
  ✅ **Reality:** asyncio runs everything on one thread. It overlaps *waiting*, not computing; a CPU-bound task blocks every other task on the loop.
- ❌ **Myth:** "a timeout stops the job."
  ✅ **Reality:** `asyncio.timeout` only *asks*, at the next `await`. Threads can't be asked at all. Only killing the process is certain.
- ❌ **Myth:** "catching all exceptions is always safe."
  ✅ **Reality:** `except BaseException:` (or `except asyncio.CancelledError:` without re-raising) swallows cancellation, and the `stubborn` task above shows the result.

## Check yourself

1. A job in a `threads` worker times out. What happens to the other 7 jobs in that worker, and why can't Kiln do better?
   <details><summary>Answer</summary>

   They're lost as collateral (`worker_lost`) when the master kills the worker. Python can't stop a single thread, so ending the process is the only way to stop the runaway job.

   </details>
2. Why does the asyncio executor give *sync* tasks no cooperative timeout?
   <details><summary>Answer</summary>

   A sync task runs in a thread through `asyncio.to_thread`. Cancelling the `await` would return control to the loop, but the thread would keep running the task, using a slot the master believes is free. So sync tasks rely only on the master's hard kill.

   </details>
3. Predict: `async with asyncio.timeout(0.1): await asyncio.to_thread(time.sleep, 1)`. What happens after 0.1 s, and when does the thread stop?
   <details><summary>Answer</summary>

   The `await` is cancelled after 0.1 s and `TimeoutError` is raised in the coroutine, but the thread keeps sleeping until its full second is up: threads can't be cancelled.

   </details>

## Go deeper

- [`asyncio.timeout`](https://docs.python.org/3.15/library/asyncio-task.html#timeouts) and [task cancellation](https://docs.python.org/3.15/library/asyncio-task.html#task-cancellation).
- `man 5 proc` (`/proc/<pid>/stat`, fields `utime` and `stime`): how CPU utilization is measured.
- [python/async-await-basics](../python/async-await-basics.md), [python/asyncio-run](../python/asyncio-run.md), [amdahls-law](amdahls-law.md).
