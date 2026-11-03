# Glossary

> Every technical term used in Kiln's docs, in plain English. Each entry has a definition, an example, and where to learn more.
> Missing a word? Add it to [`questions.md`](questions.md). Implementers keep this file complete ([standards/documentation.md §7](standards/documentation.md#7-glossary-faq-questions)).

---

## #
- **9P.** The network-style file protocol WSL uses to show Windows drives under `/mnt/c`. It's much slower than Linux's own filesystem. *See:* [spec/07](spec/07-environment.md).

## A
- **abi3 / abi3t.** abi3 is CPython's *stable ABI*: one compiled extension works across many Python versions (GIL builds only). abi3t (PEP 803, 3.15) is the stable ABI for free-threaded builds. *See:* K8.
- **ADR (Architecture Decision Record).** A short note that records one design decision and why it was made. *Example:* ADR-009 says jobs carry JSON, not pickle. *See:* [decisions.md](decisions.md).
- **Amdahl's law.** If part of a job can't run in parallel, that part caps the speedup. With 10% serial work, you can never get more than 10× faster, however many cores you add. *See:* K4.
- **App / app spec.** The user's object that holds registered tasks, and the `"module:attribute"` string that tells Kiln where to find it. *Example:* `examples.demo:app`. *See:* [spec/03 §1](spec/03-jobs-and-tasks.md#1-vocabulary-these-words-have-exact-meanings-in-kiln).
- **Arbiter.** Another name for the **master** process (gunicorn's term). It "arbitrates", meaning it decides what every worker does.
- **ast / symtable.** Stdlib modules that turn Python source into a tree of syntax nodes (`ast`) and tell you which names are local, global or free in each scope (`symtable`). The K2 fork-checker is built on them.
- **asyncio.** Python's standard library for running many waiting tasks on one thread using an **event loop**, `async def` and `await`. *See:* K5.
- **At-least-once / at-most-once / exactly-once.** How many times a job's function might run. At-most-once: 0 or 1 times. At-least-once: 1 or more. Exactly-once: impossible in general. *See:* [spec/03 §5](spec/03-jobs-and-tasks.md#5-delivery-semantics-how-many-times-can-my-function-run).
- **atexit.** A Python module that runs registered functions when the interpreter shuts down normally, in reverse order of registration. *See:* K3.
- **Atomic write.** Writing a file so readers see either the old version or the complete new one, never half of it: write to a temp file, then `os.replace()` it over the target.
- **Attempt.** One try at running a job on a worker. A retried job has several.

## B
- **Backoff.** Waiting longer and longer between retries (0.1 s, 0.2 s, 0.4 s…) so a failing thing isn't hammered. *See:* [spec/04 §5.1](spec/04-supervisor.md#51-keeping-the-worker-count).
- **Backpressure.** Pushing back on a sender when you're overloaded, for example replying "429 Too Many Requests", instead of letting work pile up without limit. *See:* K5.
- **Biased reference counting.** The free-threaded build's refcount scheme. The thread that owns an object updates its count cheaply, and other threads use a slower shared counter. *See:* K3, K4.
- **Big-endian (network byte order).** Writing a multi-byte number with the most significant byte first. The number 258 as 4 bytes is `00 00 01 02`. *See:* [spec/02 §2](spec/02-protocol.md#2-kp1-frame-format).
- **Blocking call.** A function call that waits (for time, the disk or the network) and doesn't return until it's done. Deadly in a single-threaded event loop.
- **Bootstrap (statistics).** Estimating how uncertain a measurement is by resampling your own data many times. *See:* K9, [spec/10](spec/10-measurement-methodology.md).
- **Branch coverage.** The share of `if`/`else` paths your tests actually took, which is stricter than line coverage. *See:* [spec/09](spec/09-testing-strategy.md).
- **Build backend.** The tool that turns a project into an installable wheel. Kiln uses `uv_build`. *See:* [spec/08](spec/08-repo-layout-and-tooling.md).
- **Bytes vs. str.** `bytes` is raw 8-bit data. `str` is text made of Unicode characters. Converting between them is **encoding** and **decoding** (Kiln uses UTF-8).

## C
- **Capacity / slot.** How many jobs a worker can run at the same time. Each one is a "slot".
- **Catastrophic backtracking.** A regular expression like `(a+)+$` whose matching time doubles with each extra character on certain inputs. Kiln uses it as a fallback way to hold the GIL (`labs.hold_gil`).
- **cgroup (control group).** A Linux feature that groups processes and limits their resources, such as memory. When a cgroup exceeds `memory.max`, the kernel's **OOM killer** kills a process in it. *See:* K1 lab.
- **Channel.** In Kiln, the private socketpair connection between the master and one worker.
- **Client.** Any program that connects to Kiln to submit jobs or ask about them, such as the `kiln` CLI or the gateway.
- **Closed-loop vs. open-loop load.** A closed-loop load generator waits for each reply before sending the next request. An open-loop one sends on a fixed schedule whatever happens. Only open loop shows the true latency during a stall. *See:* coordinated omission.
- **Code coverage.** The percentage of code lines your tests executed. Kiln targets at least 85%.
- **Confidence interval (CI).** A range that likely contains the true value. "Median 12 ms, 95% CI [11.4, 12.7]". *See:* K9.
- **Cooperative cancellation.** Asking code to stop, which only takes effect when that code reaches a point where it checks, such as an `await` in asyncio. You can't force it.
- **Coordinated omission.** A benchmarking trap: if the load generator pauses while the server is stalled, it never records the requests it *would* have sent, so latency looks far better than it was. *See:* [spec/10 §6](spec/10-measurement-methodology.md).
- **Copy-on-read.** Instagram's name for the problem in K3: in CPython, merely *reading* an object changes its refcount (a write), so shared copy-on-write pages get copied anyway.
- **Copy-on-write (CoW).** After `fork()`, parent and child share the same memory pages until one of them *writes* to a page. Only then does the kernel copy that page. *See:* K3.
- **Counter / gauge / histogram.** Metric types. A counter only goes up (jobs submitted). A gauge goes up and down (queue depth). A histogram counts observations per range, or *bucket* (latencies). *See:* [spec/06 §4](spec/06-observability.md).
- **CPython.** The standard implementation of Python, written in C. It's what `python` usually means.
- **Crash loop.** A process that fails right after starting, gets restarted, and fails again, over and over. *See:* [spec/04 §5.1](spec/04-supervisor.md#51-keeping-the-worker-count).
- **CRLF / LF.** Line endings. Windows uses carriage return plus line feed (`\r\n`) and Unix uses `\n`. Shell scripts with CRLF break, so Kiln forces LF with `.gitattributes`.

## D
- **Daemon.** A long-running background process (a server).
- **Data race.** Two threads touch the same data at the same time and at least one of them writes, so the result depends on timing and updates can be lost. *See:* K4.
- **Deadline.** A point in (monotonic) time by which something must happen, such as "the worker must be ready by t=30 s".
- **Debug op.** An opt-in diagnostic request (tracemalloc snapshot, GC stats, stack dump…) that the master forwards to a worker. *See:* [spec/06 §5](spec/06-observability.md).
- **Decorator.** A function that wraps or registers another function, written with `@name` above a `def`. *Example:* `@app.task`. *See:* `python/decorators.md` (K0).
- **Dependency group (PEP 735).** A named set of extra dependencies in `pyproject.toml`, such as `dev` or `lab-k6`, that is installed only when you ask for it.
- **Diff flamegraph.** A flamegraph that compares two profiles and colours what grew or shrank. *See:* K6.
- **Dispatch.** The master choosing a free worker slot for the next queued job and sending it a `run` message.
- **Drift.** Measurements slowly trending during a run because the machine's condition changed (heat, background updates). Interleaving variants protects against it.

## E
- **epoll.** The Linux system call family behind `selectors.DefaultSelector`. It efficiently waits for many sockets at once.
- **Event loop.** A loop that waits for "something happened" events (data arrived, a timer expired) and runs the code for each one. Kiln's master has a hand-written one; the gateway uses asyncio's. *See:* [spec/01 §4](spec/01-architecture.md#4-the-masters-event-loop-the-heart-of-kiln).
- **Event name.** The stable snake_case identifier on every Kiln log line (`event=worker_died`). Tests and labs search for these. *See:* [spec/06 §2](spec/06-observability.md).
- **Executor.** The part of a worker that actually runs job functions: sync, threads, interpreters or asyncio. *See:* K4.
- **Exit code / exit status.** The small number a process returns when it ends normally (0 means success). It's different from being *killed by a signal*. *See:* [spec/04 §4.1](spec/04-supervisor.md#41-death-classification-the-k1-oom-detection-requirement).
- **ExperimentRecord.** Kiln's JSON format for one measurement run. Every lab writes these. *See:* [spec/10](spec/10-measurement-methodology.md).
- **ext4.** Linux's native filesystem, used inside WSL. Fast, unlike `/mnt/c` (9P).

## F
- **Fault injection.** Making things fail on purpose (crash a worker, kill a connection) to test how the system reacts. Kiln's `labs.crash` task does this.
- **fd (file descriptor).** A small integer a process uses to refer to an open file, socket or pipe. 0, 1 and 2 are stdin, stdout and stderr. *See:* `concepts/files-and-file-descriptors.md` (K0).
- **FFI boundary cost.** The overhead paid on *every* call between Python and native code (converting arguments and results). Many tiny calls can cancel out a native speedup. *See:* K8.
- **FIFO.** First in, first out: a queue where the oldest item leaves first.
- **Finalization.** What the interpreter does at shutdown: running atexit handlers, a final GC, tearing down modules. *See:* K3.
- **Fixture (pytest).** A reusable piece of test setup and teardown that pytest injects into tests by name, such as a running Kiln server that is always cleaned up.
- **Flaky test.** A test that sometimes passes and sometimes fails with no code change, usually because of timing. Kiln's policy is in [spec/09](spec/09-testing-strategy.md).
- **Flamegraph.** A picture of profiler samples: each box is a function, and its width shows how much time (or memory) was spent there, including its callees. *See:* K6.
- **fork.** The Unix system call that creates a new process by *copying* the current one. Both processes continue from the same line. *See:* K1 `concepts/fork.md`.
- **Fork-assumption checker (forkcheck) ★.** Kiln's static analysis tool that finds code which only works with the `fork` start method. *See:* K2.
- **Forkserver.** A start method in which a small, clean helper process forks new workers on request. *See:* [spec/04 §6](spec/04-supervisor.md#6-start-methods-k2).
- **Frame (protocol).** One complete message on the wire: a 4-byte length plus the JSON payload. *See:* [spec/02](spec/02-protocol.md).
- **Free-threading (3.13t / 3.15t).** A CPython build without the GIL, so threads can run Python code truly in parallel. *See:* K4.

## G
- **Garbage collector (GC).** The part of CPython that finds and frees groups of objects that refer to each other (**reference cycles**) but are no longer used. Plain refcounting can't free those. *See:* K3, K7.
- **Gated module.** A Kiln module that needs a newer Python (3.14+ or 3.15+) and is only imported behind a version check.
- **Gateway.** Kiln's optional HTTP front-door process (K5).
- **`gc.freeze()`.** Moves every currently tracked object into a "permanent generation" that the GC never scans again. Instagram's fix. *See:* K3.
- **Generation (worker).** A counter that increases on every reload. Workers from the old generation retire.
- **Generational hypothesis.** The observation that most objects die young. The generational GC exploits it by scanning new objects more often than old ones. *See:* K7.
- **GIL (Global Interpreter Lock).** A lock in standard CPython that lets only one thread run Python bytecode at a time. *See:* K4.
- **Graceful (shutdown / reload).** Stopping or replacing workers *after* they finish their current jobs, instead of killing them mid-job.

## H
- **Heartbeat.** A small "I'm alive" message a worker sends every second. If they stop, the master assumes the worker is hung.
- **High-cardinality label.** A metric label with unbounded values, such as a job id. A classic mistake, because it creates one time series per value.
- **Hypothesis (lab).** Your prediction, with reasons, before an experiment.
- **Hypothesis (library).** A Python testing library that generates many random inputs to find cases that break your code (property-based testing).

## I
- **Idempotent.** Running it twice has the same effect as running it once. Required for safe retries.
- **Immortal objects (PEP 683).** Objects whose reference count never changes (for example `None`), so touching them never writes to their memory.
- **Import boundary.** A rule about which Kiln packages may import which (for example, `master` never imports `worker`), enforced by a test. *See:* [spec/01 §5](spec/01-architecture.md).
- **In-flight.** Sent to a worker but not finished yet.
- **Incremental GC.** A collector that does its work in small slices to keep pauses short. Python 3.14.0–3.14.4 shipped one, and it was reverted because of peak memory blowups. *See:* K7.
- **Injection.** When data inserted into some output (SQL, HTML, a shell command) changes that output's *structure*, for example `<script>` inside a page. t-strings exist to prevent it. *See:* K9.
- **Interleaving (round-robin).** Running experiment variants alternately (A B C A B C…) so slow drift affects all of them equally.
- **Invariant.** A statement that must always be true, such as "a terminal job never changes state".
- **IPC (inter-process communication).** Any way separate processes exchange data: pipes, sockets, shared memory, signals.

## J
- **Jitter.** A small random offset added to a limit or timer so many processes don't all act at the same moment (for example, every worker restarting together).
- **Job.** One request to run a task with specific arguments. It has an id and a state. *See:* [spec/03](spec/03-jobs-and-tasks.md).
- **JobView.** The JSON shape of a job that Kiln shows to clients. *See:* [spec/02 §3.5](spec/02-protocol.md#35-jobview-how-a-job-is-shown-to-clients).
- **JSON.** A text format for data: objects `{}`, arrays `[]`, strings, numbers, true/false/null.

## K
- **KP/1.** Kiln Protocol version 1: length-prefixed JSON frames. *See:* [spec/02](spec/02-protocol.md).

## L
- **Latency, p50 / p99.** How long one request takes. p99 is the time that 99% of requests beat (the slow tail).
- **Lazy import (PEP 810, 3.15).** `lazy import x` delays actually importing `x` until it's first used.
- **Listener.** The socket a server uses to accept new connections.
- **Lockfile.** A file (`uv.lock`) that pins the exact version of every dependency so installs are reproducible.
- **Log level.** The severity tag on a log line: DEBUG, INFO, WARNING, ERROR, CRITICAL.
- **Loop lag.** The delay between when an event-loop callback should run and when it actually runs. A big lag means something blocked the loop. *See:* K5.

## M
- **Manifest.** The list of task names and their options that a worker reports to the master in `ready`.
- **Master.** Kiln's boss process. It owns the queue and supervises workers.
- **Median / percentile.** The median is the middle value of sorted measurements, which isn't thrown off by outliers. The p-th percentile is the value that p% of measurements are below.
- **memory.max / memory.events.** cgroup v2 files. `memory.max` sets the group's memory limit. `memory.events` counts what happened, including `oom_kill`. *See:* [spec/04 §4.2](spec/04-supervisor.md).
- **Monotonic clock.** A clock that only moves forward (`time.monotonic()`), used for measuring durations and deadlines.
- **__mp_main__.** The module name `multiprocessing` gives your main script when it re-imports it in a spawned child. That's why code under `if __name__ == "__main__":` doesn't run again there. *See:* K2.
- **Multi-phase init.** The modern way a C extension module initializes itself, using a list of *slots* (such as `Py_mod_gil`) instead of one init function. *See:* K4.

## N
- **Non-blocking I/O.** Socket operations that return immediately, with whatever data is available or "nothing yet", instead of waiting.

## O
- **OOM killer.** The Linux kernel component that kills a process with `SIGKILL` when memory runs out (system-wide, or in a cgroup). The victim gets no warning and can't catch it.
- **oom_score_adj.** A per-process number (−1000 to 1000) that biases the OOM killer's choice. Kiln workers raise theirs to 500 so the master survives ([ADR-029](decisions.md#adr-029)).
- **Orphan.** A process whose parent died. Linux re-parents it to pid 1 (init), so the original parent can no longer reap it.

## P
- **PEP.** Python Enhancement Proposal, a design document for a Python change. *Example:* PEP 750 (t-strings).
- **PEP 442.** The Python 3.4 change that lets the GC collect reference cycles even when objects in them have `__del__` methods. *See:* ADR-016.
- **PEP 446.** File descriptors created by Python are non-inheritable by default, so child processes don't get them unless you ask. *See:* K2.
- **Permanent generation.** Where `gc.freeze()` puts objects: the GC never scans them again. *See:* K3.
- **pickle.** Python's built-in way to turn objects into bytes and back. Unsafe on untrusted data. Required by `multiprocessing` spawn/forkserver.
- **pid.** Process id, the number the OS uses to identify a process.
- **Pidfd.** A file descriptor that refers to a process and becomes readable when that process exits. Unlike a pid, it can't be confused with a later process that reuses the same number. *See:* [spec/04 §6](spec/04-supervisor.md).
- **PIPE_BUF.** The size (4096 bytes on Linux) below which a single write to a pipe is guaranteed not to interleave with other writers. It matters for multi-process logging.
- **Pipelining.** Sending several requests without waiting for each reply, matched up later by an id (`req_id`).
- **Port 0.** Asking the kernel to pick any free port when binding a socket. Tests use it so they never collide.
- **Practical significance.** Whether a real, measured difference is big enough to matter. A 0.1% speedup can be statistically real and practically irrelevant.
- **Precision / recall.** For a checker: precision is the share of its findings that are real problems; recall is the share of real problems it found. *See:* K2.
- **Preload.** Importing the app in the master *before* forking, so workers share its memory via copy-on-write.
- **Private_Dirty / Shared_Clean.** Lines in `/proc/<pid>/smaps_rollup`. Private_Dirty is memory written by and owned only by this process. Shared_Clean is pages shared with others and never modified. *See:* K3.
- **Process.** A running program with its own memory, file descriptors and pid. *See:* `concepts/what-is-a-process.md` (K0).
- **Process group.** A set of processes that receive terminal signals (like Ctrl-C's SIGINT) together.
- **Processor (t-string).** A function that turns a t-string `Template` into final output, for example by HTML-escaping every interpolated value. *See:* K9.
- **Prometheus text format.** A simple plain-text format for metrics (`name{label="x"} 42`) that monitoring tools scrape from `/metrics`. *See:* [spec/06 §4](spec/06-observability.md).
- **Property-based testing.** Testing by stating a rule ("decoding an encoded frame gives back the original") and letting a library such as Hypothesis generate hundreds of inputs. When something fails it *shrinks* the input to the smallest failing example.
- **Protocol (networking).** An agreement about what bytes mean.
- **PSS / RSS / USS.** Memory measurements. **RSS** counts every page a process uses, shared ones included. **PSS** divides each shared page among the processes that share it. **USS** counts only pages private to the process. *See:* K3 `concepts/memory-metrics.md`.
- **ptrace / ptrace_scope.** ptrace is the Linux facility that lets one process inspect another (debuggers, profilers, `asyncio ps`). The Yama setting `ptrace_scope` restricts it, and the value 1 means "only your own children". *See:* [spec/07](spec/07-environment.md).
- **Py_mod_gil.** The slot a C extension uses to declare "I'm safe without the GIL". Without it, importing the extension on a free-threaded build turns the GIL back on. *See:* K4, ADR-015.
- **PyDLL.** The `ctypes` loader whose calls keep the GIL held (normal `CDLL` calls release it). Used by `labs.hold_gil`.
- **python-build-standalone.** The relocatable, prebuilt CPython builds that `uv python install` downloads.

## Q
- **Queue.** Jobs waiting for a worker, in FIFO order.

## R
- **Ratio of medians.** The median of variant B divided by the median of A. If its confidence interval includes 1.0, we report "no detectable difference". *See:* [spec/10](spec/10-measurement-methodology.md).
- **Read-modify-write.** Updating a value by reading it, changing it and writing it back (`count += 1`). It isn't atomic, so another thread can sneak in between the steps. *See:* K4.
- **Reap / reaping.** A parent calling `waitpid()` to collect a dead child's exit status, which removes the **zombie**.
- **Recycling (worker).** Retiring a worker on purpose after N jobs or X MB, to contain slow leaks.
- **Reference count (refcount).** How many references point at an object. CPython frees an object when this reaches 0. Changing it is a *write* to the object's memory, which breaks copy-on-write.
- **Reference cycle.** Objects that refer to each other in a loop, so their refcounts never reach 0 on their own.
- **Release build.** Native code compiled with optimizations on. Benchmarks of Rust code must use release builds. *See:* K8.
- **Reload.** Replacing all workers with new ones (on SIGHUP) without dropping jobs.
- **Remote attach (PEP 768).** Running a debugger or code inside an already-running Python process at a safe point (`python -m pdb -p PID`, `sys.remote_exec`). *See:* K6.
- **`req_id`.** A client-chosen number in each KP/1 request, echoed in the response.
- **Request smuggling.** An attack where two HTTP servers disagree on where one request ends and the next begins. A reason Kiln's parser rejects ambiguous input. *See:* K5.
- **Resurrection.** A `__del__` method that stores `self` somewhere, so an object that was about to die stays alive. One of K6's planted leaks.
- **RuleBasedStateMachine.** Hypothesis's tool for testing stateful objects: it generates random sequences of operations and checks the invariants after each one. Used for Kiln's JobTable.
- **Run / variant / experiment.** An experiment compares variants (for example gc_mode=default vs freeze). Each variant is measured over several runs, and every run produces one ExperimentRecord.

## S
- **SafeHTML.** Kiln's marker type for HTML that is already escaped and must not be escaped again by the t-string processor. *See:* K9.
- **Sans-IO.** Designing a protocol parser that only turns bytes into events and never touches sockets itself, which makes it easy to test. *See:* K5.
- **SCM_RIGHTS.** The Unix-socket feature that sends an open file descriptor to another process (`socket.send_fds`). *See:* K2.
- **Selector / `selectors`.** The stdlib module for waiting on many sockets at once. *See:* K1.
- **Self-pipe trick.** Making a signal wake an event loop by writing a byte to a pipe or socket the loop is watching. Python's `signal.set_wakeup_fd` does this.
- **Signal.** A tiny asynchronous notification sent to a process: SIGINT (Ctrl-C), SIGTERM (please stop), SIGKILL (die now, can't be caught), SIGHUP, SIGCHLD (a child changed state)… *See:* K1 `concepts/signals.md`.
- **Snippet test.** A test that runs every code snippet in the primers so the docs can't go stale.
- **Socket.** An endpoint for sending and receiving bytes, over a network (TCP) or locally (Unix domain sockets).
- **socketpair.** Two already-connected local sockets. Kiln creates one per worker.
- **Spawn.** A start method that launches a completely fresh Python interpreter.
- **src layout.** Keeping the package under `src/` so that tests run against the *installed* package, not whatever happens to be in the current folder.
- **Start method.** How a new worker process gets created: fork, spawn or forkserver. *See:* K2.
- **State machine.** A model with a fixed set of states and allowed transitions between them. *See:* [spec/03 §4](spec/03-jobs-and-tasks.md#4-the-job-state-machine).
- **Static analysis.** Examining code without running it, by reading its syntax tree. *See:* K2.
- **Stdlib (standard library).** The modules that ship with Python itself.
- **Structured log.** A log where each line is one event made of machine-readable fields (`key=value` or JSON), not free-form prose. *See:* [spec/06](spec/06-observability.md).
- **Subinterpreter.** A separate Python interpreter inside the same process, with its own modules and globals. *See:* K4.
- **Syscall (system call).** A request from a program to the operating system kernel, such as `fork`, `read`, `waitpid` or `kill`.
- **systemd-run scope.** A one-off cgroup created by `systemd-run --scope`, used to run Kiln under a memory limit for the OOM lab. *See:* [spec/07](spec/07-environment.md).

## T
- **t-string (PEP 750, 3.14).** `t"Hello {name}"` produces a `Template` that keeps the fixed text and the values separate, so they can be escaped safely. *See:* K9.
- **Task.** A named Python function registered with `@app.task`.
- **TCP.** The network protocol that gives a reliable, ordered **stream** of bytes (with no message boundaries).
- **Teach-back.** Checking your understanding by explaining a concept in your own words. K10's final exercise.
- **Template / Interpolation.** What a t-string produces: a `Template` holding the fixed text parts plus `Interpolation` objects for each `{value}`. *See:* K9.
- **Terminal state.** A job state that never changes again: SUCCEEDED, FAILED, TIMED_OUT, LOST, CANCELLED.
- **Test pyramid.** Many fast unit tests, fewer integration tests, and only a few slow end-to-end tests. *See:* [spec/09](spec/09-testing-strategy.md).
- **Throughput.** How many jobs per second the system completes.
- **TOML.** A config file format: `key = value` under `[section]` headers. Read with the stdlib `tomllib`.
- **TTL (time to live).** How long something is kept before it's deleted automatically.

## U
- **UTF-8.** The standard way to encode text as bytes.
- **uv.** A fast tool that installs Python versions, creates virtual environments and manages dependencies.

## V
- **Verification gate.** A step where we check a version-specific claim against the real interpreter before relying on it ([ADR-020](decisions.md#adr-020)).
- **Virtual environment (venv).** A folder with its own Python interpreter link and installed packages, isolated from the rest of the system.
- **VmHWM / ru_maxrss.** Peak resident memory of a process. VmHWM is in `/proc/<pid>/status`. `ru_maxrss` comes from `resource.getrusage`, and it is in **KiB** on Linux. *See:* K7.

## W
- **Wait status.** The integer `waitpid` returns, which packs "exited with code N" or "killed by signal S" into one number. *See:* [spec/04 §4.1](spec/04-supervisor.md).
- **waitpid.** The syscall a parent uses to learn how a child process ended, and to reap it.
- **Wakeup fd.** A file descriptor that Python writes a byte to whenever a signal arrives, which wakes the event loop (`signal.set_wakeup_fd`).
- **Watchdog.** A separate thread or timer that notices when something (like the event loop) stopped responding, and records what it was stuck on. *See:* K5.
- **Weak reference.** A reference that doesn't keep its target alive (`weakref.ref`). It is useful for caches that shouldn't cause leaks. *See:* K6.
- **Wheel / wheel tag.** A wheel is Python's prebuilt package format (`.whl`). Its filename tag records the Python version, ABI and platform it works on.
- **Worker.** A Kiln child process that runs jobs.
- **WSL2.** Windows Subsystem for Linux: a real Linux kernel running inside Windows. Kiln runs there.
- **.wslconfig.** The Windows file (`%UserProfile%\.wslconfig`) that sets WSL's memory and CPU limits.

## Z
- **Zombie.** A dead child process whose exit status hasn't been collected (**reaped**) by its parent yet.
