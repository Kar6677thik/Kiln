# Threads

> **Milestone:** K4 · **Read before:** [what-is-a-process](what-is-a-process.md), [fork](fork.md), [fork-and-threads](fork-and-threads.md) · **Reading time:** ~10 min
> **Terms introduced:** **thread**, **OS scheduler**, **context switch**, **daemon thread**, **thread pool**

## One-sentence summary

A **thread** is a second (third, fourth…) line of execution *inside the same process*: threads share all of the process's memory and files, which makes handing data between them cheap and makes them dangerous for exactly the same reason.

## Why you should care

Until K4, every Kiln worker ran one job at a time on its single main thread (plus a tiny heartbeat thread from K1). K4 lets a worker run several jobs at once, and three of the four ways it does that (`threads`, `asyncio`, `interpreters`) use threads inside the worker. Once two threads touch the same object, bugs appear that no amount of single-threaded testing finds: SquadStack's Celery workers deadlocked in production because some of their code wasn't thread-safe (research [§2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources)). This primer is the base for [gil](gil.md), [data-races](data-races.md) and [thread-safety-of-builtins](thread-safety-of-builtins.md).

## The mental model

**Analogy:** a process is a workshop: the workbench (memory), the tool cabinet (open files, sockets) and the address on the door (the pid). A thread is a worker standing at the bench. Several workers in one workshop can pass parts to each other instantly, because everything is on the same bench. They can also grab the same part at the same moment.

**Where the analogy fails:** real workers see each other and wait politely. Threads don't see anything: the **OS scheduler** decides, many times a second, which thread runs on which CPU core, and it can pause a thread between *any* two machine instructions (a **context switch**). A thread never knows it was paused.

```
 one process (pid 4242)
 ┌─────────────────────────────────────────────────────────┐
 │  memory: every object, every module, every global        │  ← shared by ALL threads
 │  open fds: sockets, files                                │  ← shared by ALL threads
 │                                                          │
 │  MainThread        worker-1          worker-2            │
 │  [own stack]       [own stack]       [own stack]         │  ← private to each thread
 │  [own registers]   [own registers]   [own registers]     │
 └─────────────────────────────────────────────────────────┘
        ▲                  ▲                  ▲
        └──── the OS scheduler puts each on a CPU core, pauses it, resumes it ────┘

 fork() makes a SECOND process: its own copy of memory, one thread only.
```

## See it for yourself

[`snippets/threads_1.py`](snippets/threads_1.py) starts two named threads that append to one list, then forks a child that appends to *its* list:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/threads_1.py
```

```text
main thread is 'MainThread'
t1: thread name='worker-1', same pid as main
t2: thread name='worker-2', same pid as main
the parent's list has 2 entries: the child's append went to its own copy
```

(Same output on 3.15.1 and 3.15.1t.)

- The two threads ran in the **same process** (same pid) and both appends landed in the **same list**: that's shared memory.
- The forked child also appended, but to its *copy* of the list ([fork](fork.md)), so the parent still sees two entries. Threads share; processes copy.

## How it really works

- **Creation.** `threading.Thread(target=f).start()` asks the OS for a new thread (on Linux, `clone(2)` with flags that share the address space, file table and signal handlers: `man 2 clone`). The new thread gets its own **stack** (for local variables and call frames) and its own CPU register set; everything else is shared.
- **Scheduling.** The Linux scheduler treats each thread as a separate schedulable task. On a machine with 14 cores (this one: Intel Core Ultra 5 225H, one thread per core, no SMT), up to 14 threads can execute at the same instant, unless something inside the program prevents it. In CPython that something is the [GIL](gil.md).
- **Joining.** `t.join()` waits until the thread's function returns. A thread can't be killed from outside in Python: there is no `thread.kill()`. The only ways to stop one are to ask it (an `Event`, a flag it checks) or to end the whole process. That's why a Kiln job that runs past its timeout costs the *whole worker* ([cpu-vs-io-bound](cpu-vs-io-bound.md#cooperative-cancellation)).
- **Daemon threads.** A **daemon thread** (`Thread(daemon=True)`) doesn't keep the process alive: when the main thread finishes, the interpreter exits without waiting for daemon threads. Kiln's heartbeat thread is a daemon for that reason.
- **Thread pools.** Creating a thread costs microseconds and some memory. A **thread pool** creates a fixed number of threads once and feeds them work items from a queue (`concurrent.futures.ThreadPoolExecutor`, [python/concurrent-futures](../python/concurrent-futures.md)).
- **Threads and fork don't mix.** `fork()` copies only the calling thread; any lock another thread held is copied *locked* forever ([fork-and-threads](fork-and-threads.md)). That's why Kiln's master has no threads at all (ADR-006), and why workers start their threads *after* they were forked.

## How Kiln uses it

- `src/kiln/worker/heartbeat.py` (K1): one daemon thread that sends a heartbeat every interval, even while the main thread runs a long job.
- `src/kiln/worker/executors/threads.py` (K4): a `ThreadPoolExecutor` with `worker.executor_concurrency` threads named `kiln-job_N`; each runs one job at a time.
- `src/kiln/worker/executors/asyncio_exec.py` (K4): one event-loop thread `kiln-loop` plus a `kiln-tothread` pool for sync tasks.
- `worker/channel.py`: because several threads now send `result` messages on one socket, every send takes a lock (K4 spec §5.2).
- `worker/main.py` `WorkerCounters`: the job counters are updated under a lock ([data-races](data-races.md)).
- The master: **no threads** (ADR-006), so it can fork safely.

## Common misconceptions

- ❌ **Myth:** threads make a Python program faster.
  ✅ **Reality:** only when the threads mostly *wait* (I/O), or on the free-threaded build. With the GIL, CPU-bound threads take turns ([gil](gil.md)).
- ❌ **Myth:** a thread can be stopped from outside, like a process.
  ✅ **Reality:** Python has no safe way to kill a thread. It must stop itself; otherwise, end the process.
- ❌ **Myth:** each thread has its own copy of global variables.
  ✅ **Reality:** globals, module state and every object are shared. Only local variables (on each thread's stack) are private, and `threading.local()` gives per-thread storage explicitly.

## Check yourself

1. Two threads in one process and two forked processes each append to "the same" list. Which pair ends up with one list holding both items?
   <details><summary>Answer</summary>

   The threads: they share one address space, so there is only one list. Each forked process has its own copy, so each copy gets one item.

   </details>
2. Why does Kiln forbid threads in the master, but allow them in workers?
   <details><summary>Answer</summary>

   The master forks workers. `fork()` copies only the forking thread; a lock held by any other thread at that moment would stay locked forever in the child, which can deadlock it ([fork-and-threads](fork-and-threads.md)). Workers never fork, so threads are safe there.

   </details>
3. Predict: the main thread starts a non-daemon thread that sleeps 10 s, then the main thread's code ends. When does the process exit? What if the thread were a daemon?
   <details><summary>Answer</summary>

   Non-daemon: after about 10 s, because the interpreter waits for non-daemon threads at shutdown. Daemon: immediately; the daemon thread is abandoned mid-sleep.

   </details>

## Go deeper

- [`threading` docs](https://docs.python.org/3.15/library/threading.html): every class used in Kiln (`Thread`, `Lock`, `Event`, `Barrier`).
- `man 2 clone` and `man 7 pthreads`: how Linux creates threads and what they share.
- [python/threading](../python/threading.md) and [python/concurrent-futures](../python/concurrent-futures.md): the Python APIs, with REPL sessions.
