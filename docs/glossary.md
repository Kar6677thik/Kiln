# Glossary

> Every technical term used in Kiln's docs, in plain English. Each entry has a definition, an example, and where to learn more.
> Missing a word? Add it to [`questions.md`](questions.md). Implementers keep this file complete ([standards/documentation.md §7](standards/documentation.md#7-glossary-faq-questions)).

---

## #
- **`*args` / `**kwargs`.** Parameters that collect extra positional arguments into a tuple and extra keyword arguments into a dict. *See:* [python/functions.md](python/functions.md).
- **--strict.** mypy's strictest mode: everything must be annotated and checked (ADR-019). *See:* [python/type-hints.md](python/type-hints.md).
- **128+N convention.** When a shell command is killed by signal N, `$?` shows 128+N, so SIGKILL (9) shows as 137. *See:* [concepts/exit-status.md](concepts/exit-status.md).
- **9P.** The network-style file protocol WSL uses to show Windows drives under `/mnt/c`. It's much slower than Linux's own filesystem. *See:* [spec/07](spec/07-environment.md).

## A
- **abi3 / abi3t.** abi3 is CPython's *stable ABI*: one compiled extension works across many Python versions (GIL builds only). abi3t (PEP 803, 3.15) is the stable ABI for free-threaded builds. *See:* K8.
- **Absolute path / relative path.** An absolute path starts at the root (`/home/...`); a relative one is relative to the current folder (`docs/README.md`). *See:* [python/pathlib.md](python/pathlib.md).
- **Abstract syntax tree (AST) / node.** The tree `ast.parse` builds from source code: each **node** is one piece of syntax (a call, an assignment, a name). *Example:* `os.fork()` is a `Call` node whose `func` is an `Attribute` node. *See:* [concepts/ast-and-symtable.md](concepts/ast-and-symtable.md)
- **accept.** The call that takes one waiting connection from a listening socket and returns a new socket for it. *See:* [concepts/sockets-and-socketpairs.md](concepts/sockets-and-socketpairs.md).
- **ADR (Architecture Decision Record).** A short note that records one design decision and why it was made. *Example:* ADR-009 says jobs carry JSON, not pickle. *See:* [decisions.md](decisions.md).
- **Amdahl's law.** If part of a job can't run in parallel, that part caps the speedup. With 10% serial work, you can never get more than 10× faster, however many cores you add. *See:* K4.
- **Ancillary data.** A side channel of a Unix-socket message, separate from the normal bytes. SCM_RIGHTS uses it to carry open fds. *See:* [concepts/fd-passing.md](concepts/fd-passing.md)
- **`__annotate__` scope.** An extra, hidden function scope Python 3.14+ creates to hold lazily evaluated annotations (PEP 649). Tools must find scopes by name, not by position, because it shifts the order. *See:* [python/symtable-module.md](python/symtable-module.md)
- **Anonymous memory / file-backed memory.** Anonymous memory has no file behind it (the heap, where Python objects live, and stacks); file-backed memory is a mapped file (the interpreter binary, `.so` libraries). `Pss_Anon` vs `Pss_File` in smaps_rollup. *See:* [concepts/smaps.md](concepts/smaps.md)
- **App / app spec.** The user's object that holds registered tasks, and the `"module:attribute"` string that tells Kiln where to find it. *Example:* `examples.demo:app`. *See:* [spec/03 §1](spec/03-jobs-and-tasks.md#1-vocabulary-these-words-have-exact-meanings-in-kiln).
- **Arbiter.** Another name for the **master** process (gunicorn's term). It "arbitrates", meaning it decides what every worker does.
- **argparse.** The standard-library module that parses command-line arguments and writes `--help` text. *See:* [python/argparse.md](python/argparse.md).
- **Assertion.** An `assert` statement: a condition that must be true, or the test fails. *See:* [python/pytest-basics.md](python/pytest-basics.md).
- **ast / symtable.** Stdlib modules that turn Python source into a tree of syntax nodes (`ast`) and tell you which names are local, global or free in each scope (`symtable`). The K2 fork-checker is built on them.
- **`ast.NodeVisitor`.** A base class that walks a tree and calls your `visit_Call`, `visit_FunctionDef`, … method for each node of that type. *See:* [python/ast-module.md](python/ast-module.md)
- **`ast.parse` / `ast.dump` / `ast.walk` / `ast.unparse`.** Source to tree; tree to readable text; every node of a tree (in no particular order); tree back to source. *See:* [python/ast-module.md](python/ast-module.md)
- **async def / await.** `async def` defines a coroutine function; `await` pauses it until something is ready, letting other coroutines run meanwhile. *See:* [python/asyncio-first-look.md](python/asyncio-first-look.md).
- **asyncio.** Python's standard library for running many waiting tasks on one thread using an **event loop**, `async def` and `await`. *See:* K5.
- **asyncio streams.** asyncio's reader/writer pairs for sockets (`open_connection`, `start_server`). *See:* [python/asyncio-first-look.md](python/asyncio-first-look.md).
- **asyncio.gather.** Runs several coroutines concurrently and returns their results in order. *See:* [python/asyncio-first-look.md](python/asyncio-first-look.md).
- **asyncio.run.** Starts an event loop, runs one coroutine to completion, and closes the loop. *See:* [python/asyncio-first-look.md](python/asyncio-first-look.md).
- **At-least-once / at-most-once / exactly-once.** How many times a job's function might run. At-most-once: 0 or 1 times. At-least-once: 1 or more. Exactly-once: impossible in general. *See:* [spec/03 §5](spec/03-jobs-and-tasks.md#5-delivery-semantics-how-many-times-can-my-function-run).
- **atexit.** A Python module that runs registered functions when the interpreter shuts down normally, in reverse order of registration. *See:* K3.
- **Atexit handler / exit handler.** A function registered with `atexit.register` that runs when the interpreter exits normally, in LIFO order. *See:* [python/atexit.md](python/atexit.md)
- **`atexit.register`.** Registers an exit handler. *See:* [python/atexit.md](python/atexit.md)
- **Atomic write.** Writing a file so readers see either the old version or the complete new one, never half of it: write to a temp file, then `os.replace()` it over the target.
- **Attempt.** One try at running a job on a worker. A retried job has several.
- **Attribute.** A value stored on an object, read with a dot: `job.state`. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Attribute lookup at call time.** `mod.f()` finds `f` on `mod` when the call happens, so a patched `mod.f` is seen; `from mod import f` saved the old object. *See:* [python/monkeypatching.md](python/monkeypatching.md)
- **AttributeError.** The error raised when you read an attribute an object doesn't have. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Audit hook.** A callback added with `sys.addaudithook` that CPython calls on runtime events (imports, `open`…). Rejected for gcwatch: it fires *before* an import runs. *See:* [concepts/global-interpreter-state.md](concepts/global-interpreter-state.md)

## B
- **Backlog.** How many connected clients may wait for accept() before new ones are refused (the number given to listen()). *See:* [concepts/sockets-and-socketpairs.md](concepts/sockets-and-socketpairs.md).
- **Backoff.** Waiting longer and longer between retries (0.1 s, 0.2 s, 0.4 s…) so a failing thing isn't hammered. *See:* [spec/04 §5.1](spec/04-supervisor.md#51-keeping-the-worker-count).
- **Backpressure.** Pushing back on a sender when you're overloaded, for example replying "429 Too Many Requests", instead of letting work pile up without limit. *See:* K5.
- **basicConfig.** `logging.basicConfig(...)`: a one-call logging setup for simple programs. *See:* [python/logging-basics.md](python/logging-basics.md).
- **Biased reference counting.** The free-threaded build's refcount scheme. The thread that owns an object updates its count cheaply, and other threads use a slower shared counter. *See:* K3, K4.
- **Big-endian (network byte order).** Writing a multi-byte number with the most significant byte first. The number 258 as 4 bytes is `00 00 01 02`. *See:* [spec/02 §2](spec/02-protocol.md#2-kp1-frame-format).
- **Binary mode.** Opening a file with "rb" to read raw bytes; `tomllib.load` requires it. *See:* [python/tomllib.md](python/tomllib.md).
- **Blocking call.** A function call that waits (for time, the disk or the network) and doesn't return until it's done. Deadly in a single-threaded event loop.
- **BlockingIOError.** Python's exception for the kernel's EAGAIN: a non-blocking socket call that can't proceed right now. *See:* [concepts/non-blocking-io-and-selectors.md](concepts/non-blocking-io-and-selectors.md).
- **bool.** `True` or `False`. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Bootstrap (statistics).** Estimating how uncertain a measurement is by resampling your own data many times. *See:* K9, [spec/10](spec/10-measurement-methodology.md).
- **Branch.** A movable name pointing at a commit. Kiln uses one branch per milestone (ADR-025). *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **Branch coverage.** The share of `if`/`else` paths your tests actually took, which is stricter than line coverage. *See:* [spec/09](spec/09-testing-strategy.md).
- **Buffer.** A place where data collects before it is used, such as received bytes waiting for the rest of a frame. *See:* [python/bytearray-and-memoryview.md](python/bytearray-and-memoryview.md).
- **Build backend.** The tool that turns a project into an installable wheel. Kiln uses `uv_build`. *See:* [spec/08](spec/08-repo-layout-and-tooling.md).
- **Byte.** One number from 0 to 255; the unit computers store and send data in. *See:* [python/bytes-vs-str.md](python/bytes-vs-str.md).
- **Byte order / endianness.** Which byte of a multi-byte number comes first: big-endian (most significant first, "network order") or little-endian. *See:* [python/struct.md](python/struct.md).
- **Byte stream.** A continuous flow of bytes with no message boundaries, which is what TCP and stream sockets give you. *See:* [concepts/framing.md](concepts/framing.md).
- **bytearray.** A changeable (mutable) sequence of bytes that can grow; useful as a buffer for data arriving from a socket. *See:* [python/bytes-vs-str.md](python/bytes-vs-str.md).
- **Bytes / str (bytes vs. str).** `bytes` is raw 8-bit data. `str` is text made of Unicode characters. Converting between them is **encoding** and **decoding** (Kiln uses UTF-8).

## C
- **Callable.** The type of anything you can call, written like `Callable[[int], str]`. *See:* [python/typing-protocol.md](python/typing-protocol.md).
- **Callback.** A function handed to other code to be called later, when something happens ("call this when the job finishes"). *See:* [concepts/event-loops.md](concepts/event-loops.md).
- **Capacity / slot.** How many jobs a worker can run at the same time. Each one is a "slot".
- **Catastrophic backtracking.** A regular expression like `(a+)+$` whose matching time doubles with each extra character on certain inputs. Kiln uses it as a fallback way to hold the GIL (`labs.hold_gil`).
- **cgroup (control group).** A Linux feature that groups processes and limits their resources, such as memory. When a cgroup exceeds `memory.max`, the kernel's **OOM killer** kills a process in it. *See:* K1 lab.
- **Channel.** In Kiln, the private socketpair connection between the master and one worker.
- **ChildProcessError.** Raised by `os.waitpid` when the process has no children to wait for (ECHILD). *See:* [python/os-module.md](python/os-module.md).
- **Class / instance.** A class is a blueprint (`class Dog:`); an instance is one object built from it (`rex = Dog()`). *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Clean page / dirty page.** Clean: unmodified since it was loaded or shared. Dirty: modified in memory. `Private_Dirty` growing in a worker means copy-on-write copies are happening. *See:* [concepts/smaps.md](concepts/smaps.md)
- **Client.** Any program that connects to Kiln to submit jobs or ask about them, such as the `kiln` CLI or the gateway.
- **Closed-loop vs. open-loop load.** A closed-loop load generator waits for each reply before sending the next request. An open-loop one sends on a fixed schedule whatever happens. Only open loop shows the true latency during a stall. *See:* coordinated omission.
- **Code coverage.** The percentage of code lines your tests executed. Kiln targets at least 85%.
- **Command.** A program the shell starts for you, with its arguments, such as `ls -l`. *See:* [concepts/terminal-and-wsl.md](concepts/terminal-and-wsl.md).
- **Command-line argument.** A word typed after a program's name, such as `doctor` in `kiln doctor`. Python sees them in `sys.argv`. *See:* [python/argparse.md](python/argparse.md).
- **Commit.** A saved snapshot of the project with a message explaining the change. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **Comprehension.** A one-line recipe that builds a list, dict or set from another collection: `[x * 2 for x in nums]`. *See:* [python/comprehensions.md](python/comprehensions.md).
- **Confidence interval (CI).** A range that likely contains the true value. "Median 12 ms, 95% CI [11.4, 12.7]". *See:* K9.
- **Connection refused.** The error when nothing is listening on the port you connect to (ConnectionRefusedError). *See:* [concepts/sockets-and-socketpairs.md](concepts/sockets-and-socketpairs.md).
- **ConnectionRefusedError.** Python's error when nothing listens on the target port. *See:* [python/socket-module.md](python/socket-module.md).
- **Context manager.** An object that does setup when a `with` block starts and guaranteed cleanup when it ends, even after an error. *See:* [python/context-managers.md](python/context-managers.md).
- **contextlib.contextmanager.** A decorator that turns a function with one `yield` into a context manager. *See:* [python/context-managers.md](python/context-managers.md).
- **Cooperative cancellation.** Asking code to stop, which only takes effect when that code reaches a point where it checks, such as an `await` in asyncio. You can't force it.
- **Coordinated omission.** A benchmarking trap: if the load generator pauses while the server is stalled, it never records the requests it *would* have sent, so latency looks far better than it was. *See:* [spec/10 §6](spec/10-measurement-methodology.md).
- **Copy (of bytes).** Duplicating data in memory; slicing `bytes` copies, slicing a memoryview doesn't. *See:* [python/bytearray-and-memoryview.md](python/bytearray-and-memoryview.md).
- **Copy-on-read.** Instagram's name for the problem in K3: in CPython, merely *reading* an object changes its refcount (a write), so shared copy-on-write pages get copied anyway.
- **Copy-on-write (CoW).** After `fork()`, parent and child share the same memory pages until one of them *writes* to a page. Only then does the kernel copy that page. *See:* K3.
- **copyreg.** A stdlib module for registering a `__reduce__`-style recipe for a class you don't own. *See:* [python/pickle-and-reduce.md](python/pickle-and-reduce.md)
- **Core dump.** A file with a crashed process's memory, written for some fatal signals (if enabled), for debugging later. *See:* [concepts/exit-status.md](concepts/exit-status.md).
- **Coroutine / coroutine object.** A function call that can pause and resume. Calling an `async def` function returns a coroutine object, which only runs when awaited or driven by an event loop. *See:* [python/asyncio-first-look.md](python/asyncio-first-look.md).
- **Counter / gauge / histogram.** Metric types. A counter only goes up (jobs submitted). A gauge goes up and down (queue depth). A histogram counts observations per range, or *bucket* (latencies). *See:* [spec/06 §4](spec/06-observability.md).
- **CPython.** The standard implementation of Python, written in C. It's what `python` usually means.
- **Crash loop.** A process that fails right after starting, gets restarted, and fails again, over and over. *See:* [spec/04 §5.1](spec/04-supervisor.md#51-keeping-the-worker-count).
- **CRLF / LF.** Line endings. Windows uses carriage return plus line feed (`\r\n`) and Unix uses `\n`. Shell scripts with CRLF break, so Kiln forces LF with `.gitattributes`.
- **`ctypes` / `ctypes.CDLL` / `ctypes.PyDLL` / `ctypes.pythonapi`.** The stdlib library for calling C functions. `CDLL` calls release the GIL; `PyDLL` calls keep it; `pythonapi` is a `PyDLL` for the running interpreter's own C API (`PyGC_Enable`). *See:* [python/ctypes-basics.md](python/ctypes-basics.md)
- **Custom exception.** Your own error type: a class that inherits from `Exception`. *See:* [python/exceptions.md](python/exceptions.md).
- **Cycle collector.** CPython's garbage collector: it finds groups of objects that only reference each other (cycles), which reference counting can never free. *See:* [concepts/garbage-collector.md](concepts/garbage-collector.md)

## D
- **Daemon.** A long-running background process (a server).
- **Daemon thread.** A thread that doesn't keep the program alive: when the main thread ends, daemon threads stop too. *See:* [python/threading-basics.md](python/threading-basics.md).
- **Data race.** Two threads touch the same data at the same time and at least one of them writes, so the result depends on timing and updates can be lost. *See:* K4.
- **Dataclass.** A class whose `__init__`, `__repr__` and `__eq__` are written for you from its annotated fields (`@dataclass`). *See:* [python/dataclasses.md](python/dataclasses.md).
- **Deadline.** A point in (monotonic) time by which something must happen, such as "the worker must be ready by t=30 s".
- **Deadlock.** Waiting forever for something that can never happen, such as a lock that no thread will ever release. *See:* [concepts/fork-and-threads.md](concepts/fork-and-threads.md).
- **Deallocation.** Freeing an object, which CPython does the moment its reference count reaches 0. *See:* [concepts/refcounting.md](concepts/refcounting.md)
- **Debug op.** An opt-in diagnostic request (tracemalloc snapshot, GC stats, stack dump…) that the master forwards to a worker. *See:* [spec/06 §5](spec/06-observability.md).
- **Decoder buffer.** Bytes received but not yet turned into complete messages, kept until the rest arrives. *See:* [concepts/framing.md](concepts/framing.md).
- **Decorator.** A function that wraps or registers another function, written with `@name` above a `def`. *Example:* `@app.task`. *See:* `python/decorators.md` (K0).
- **Decorator factory.** A function called with options that returns the actual decorator: `@app.task(timeout_s=10)`. *See:* [python/decorators.md](python/decorators.md).
- **Default action.** What the kernel does with a signal when no handler is installed, often terminating the process. *See:* [concepts/signals.md](concepts/signals.md).
- **Default value.** The value a parameter gets when the caller leaves it out: `def f(x=1)`. *See:* [python/functions.md](python/functions.md).
- **default_factory.** `field(default_factory=list)` gives each new dataclass instance its own fresh default value. *See:* [python/dataclasses.md](python/dataclasses.md).
- **Delivery semantics.** How many times a job's function may run when things crash: at most once, at least once, or (with the task's help) exactly once. *See:* [concepts/delivery-semantics.md](concepts/delivery-semantics.md).
- **Dependency group (PEP 735).** A named set of extra dependencies in `pyproject.toml`, such as `dev` or `lab-k6`, that is installed only when you ask for it.
- **deque.** A double-ended queue (`collections.deque`): fast to add or remove at both ends. Kiln's job queue is one. *See:* [python/collections-deque.md](python/collections-deque.md).
- **Detection layer.** One of gcwatch's mechanisms: A the shim, B the import flip, C the poll and collection sentinel, D gdb by hand. *See:* [concepts/global-interpreter-state.md](concepts/global-interpreter-state.md)
- **dict.** A key → value lookup table: `{"workers": 4}`. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **__dict__.** The hidden dictionary that normally holds an object's attributes; `__slots__` removes it. *See:* [python/slots-and-frozen-dataclasses.md](python/slots-and-frozen-dataclasses.md).
- **Diff.** A line-by-line description of a change: lines starting with `-` were removed, `+` were added. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **Diff flamegraph.** A flamegraph that compares two profiles and colours what grew or shrank. *See:* K6.
- **Dispatch.** The master choosing a free worker slot for the next queued job and sending it a `run` message.
- **Drift.** Measurements slowly trending during a run because the machine's condition changed (heat, background updates). Interleaving variants protects against it.
- **Duck typing.** "If it has the right methods, it works": Python cares about what an object can do, not its class. *See:* [python/typing-protocol.md](python/typing-protocol.md).

## E
- **EAGAIN.** The kernel's "try again later" error (errno 11) from a non-blocking call that would otherwise have to wait. *See:* [concepts/non-blocking-io-and-selectors.md](concepts/non-blocking-io-and-selectors.md).
- **Encoding / decoding.** Turning text into bytes (`.encode()`) and bytes back into text (`.decode()`), following rules such as UTF-8. *See:* [python/bytes-vs-str.md](python/bytes-vs-str.md).
- **__enter__ / __exit__.** The two methods that make an object a context manager: setup and cleanup. *See:* [python/context-managers.md](python/context-managers.md).
- **Enum / member.** An enum is a fixed set of named values; each member has a `.name` and a `.value` (`Status.PASS`). *See:* [python/enum.md](python/enum.md).
- **Environment variable.** A named text setting a process inherits from its parent, like `HOME` or `KILN_WORKER__MAX_JOBS`. *See:* [python/os-module.md](python/os-module.md).
- **EOF (end of file).** For a socket, `recv()` returning `b""`: the other side closed the connection. *See:* [python/socket-module.md](python/socket-module.md).
- **epoll.** The Linux system call family behind `selectors.DefaultSelector`. It efficiently waits for many sockets at once.
- **__eq__.** The method behind `==`; it decides when two objects count as equal. *See:* [python/dataclasses.md](python/dataclasses.md).
- **Event.** Something that happened which a program must react to: a socket became readable, a timer expired, a signal arrived. (In Kiln's logs, an `event=` name.) *See:* [concepts/event-loops.md](concepts/event-loops.md).
- **Event (threading).** `threading.Event`: a shared flag one thread can set and others can wait for. *See:* [python/threading-basics.md](python/threading-basics.md).
- **Event loop.** A loop that waits for "something happened" events (data arrived, a timer expired) and runs the code for each one. Kiln's master has a hand-written one; the gateway uses asyncio's. *See:* [spec/01 §4](spec/01-architecture.md#4-the-masters-event-loop-the-heart-of-kiln).
- **Event name.** The stable snake_case identifier on every Kiln log line (`event=worker_died`). Tests and labs search for these. *See:* [spec/06 §2](spec/06-observability.md).
- **EVENT_READ / EVENT_WRITE.** The selector flags for "recv won't block" and "send won't block". *See:* [python/selectors-module.md](python/selectors-module.md).
- **Everything is a file.** The Unix idea that files, sockets, pipes, terminals and `/proc` entries all use the same read/write/close interface. *See:* [concepts/files-and-file-descriptors.md](concepts/files-and-file-descriptors.md).
- **Exception.** An object Python raises to signal that something went wrong; it stops normal flow until caught. *See:* [python/exceptions.md](python/exceptions.md).
- **Exception chaining.** `raise NewError(...) from original` records the original error as the cause. *See:* [python/exceptions.md](python/exceptions.md).
- **Executor.** The part of a worker that actually runs job functions: sync, threads, interpreters or asyncio. *See:* K4.
- **Exit code / exit status.** The small number a process returns when it ends normally (0 means success). It's different from being *killed by a signal*. *See:* [spec/04 §4.1](spec/04-supervisor.md#41-death-classification-the-k1-oom-detection-requirement).
- **ExperimentRecord.** Kiln's JSON format for one measurement run. Every lab writes these. *See:* [spec/10](spec/10-measurement-methodology.md).
- **ext4.** Linux's native filesystem, used inside WSL. Fast, unlike `/mnt/c` (9P).

## F
- **f-string.** A string literal starting with `f` whose `{...}` parts are replaced by values: `f"{n} jobs"`. *See:* [python/f-strings.md](python/f-strings.md).
- **False positive / false negative.** A false positive is a warning about code that's actually fine; a false negative is a real problem that wasn't reported. Precision and recall measure them. *See:* [concepts/static-vs-dynamic-analysis.md](concepts/static-vs-dynamic-analysis.md)
- **Falsifying example.** The smallest input Hypothesis found that breaks a property, reported after shrinking. *See:* [python/hypothesis-basics.md](python/hypothesis-basics.md).
- **Fault injection.** Making things fail on purpose (crash a worker, kill a connection) to test how the system reacts. Kiln's `labs.crash` task does this.
- **fd (file descriptor).** A small integer a process uses to refer to an open file, socket or pipe. 0, 1 and 2 are stdin, stdout and stderr. *See:* `concepts/files-and-file-descriptors.md` (K0).
- **fd FIFO.** Kiln's forkserver keeps the fds it receives over SCM_RIGHTS in a first-in-first-out list; each `spawn_worker` frame takes the oldest one. *See:* [concepts/fd-passing.md](concepts/fd-passing.md)
- **FFI boundary cost.** The overhead paid on *every* call between Python and native code (converting arguments and results). Many tiny calls can cancel out a native speedup. *See:* K8.
- **Field (dataclass).** One annotated name in a dataclass, which becomes a constructor parameter and an attribute. *See:* [python/dataclasses.md](python/dataclasses.md).
- **FIFO.** First in, first out: a queue where the oldest item leaves first.
- **file descriptor (fd).** A small integer a process uses to refer to an open file, socket or pipe. 0, 1 and 2 are stdin, stdout and stderr. *See:* [concepts/files-and-file-descriptors.md](concepts/files-and-file-descriptors.md).
- **File object.** Python's wrapper around a file descriptor, returned by `open()`. It has methods like `.read()`, `.write()` and `.fileno()`. *See:* [concepts/files-and-file-descriptors.md](concepts/files-and-file-descriptors.md).
- **Finalization.** What the interpreter does at shutdown: running atexit handlers, a final GC, tearing down modules. *See:* K3.
- **Finder / loader.** The two halves of an import: a finder locates the module (for example a `.py` file on `sys.path`), and a loader executes it to create the module object. *See:* [concepts/import-system.md](concepts/import-system.md)
- **First-class function.** Functions are values: they can be stored in variables, passed to other functions and returned. *See:* [python/decorators.md](python/decorators.md).
- **Fixture (pytest).** A reusable piece of test setup and teardown that pytest injects into tests by name, such as a running Kiln server that is always cleaned up.
- **Flaky test.** A test that sometimes passes and sometimes fails with no code change, usually because of timing. Kiln's policy is in [spec/09](spec/09-testing-strategy.md).
- **Flamegraph.** A picture of profiler samples: each box is a function, and its width shows how much time (or memory) was spent there, including its callees. *See:* K6.
- **fork.** The Unix system call that creates a new process by *copying* the current one. Both processes continue from the same line. *See:* K1 `concepts/fork.md`.
- **Fork-assumption checker (forkcheck) ★.** Kiln's static analysis tool that finds code which only works with the `fork` start method. *See:* K2.
- **Fork-safety.** Whether code still works correctly in a forked child, which has only one thread and copies of every lock. *See:* [concepts/fork-and-threads.md](concepts/fork-and-threads.md).
- **Forkserver.** A start method in which a small, clean helper process forks new workers on request. *See:* [spec/04 §6](spec/04-supervisor.md#6-start-methods-k2).
- **Format spec.** The part after `:` in a replacement field that controls layout, such as `{x:.2f}` or `{name:<10}`. *See:* [python/f-strings.md](python/f-strings.md).
- **Format string (struct).** A layout description for struct, such as `"!I"`: network byte order, one unsigned 32-bit int. *See:* [python/struct.md](python/struct.md).
- **Frame (protocol).** One complete message on the wire: a 4-byte length plus the JSON payload. *See:* [spec/02](spec/02-protocol.md).
- **Free variable.** A name a function reads from an *enclosing* function, not from its own scope or the module. It's what makes a function a closure. *See:* [concepts/ast-and-symtable.md](concepts/ast-and-symtable.md)
- **Free-threading (3.13t / 3.15t).** A CPython build without the GIL, so threads can run Python code truly in parallel. *See:* K4.
- **Freeze recipe.** From the `gc` docs: `gc.disable()` early in the parent, `gc.freeze()` right before `fork()`, `gc.enable()` early in the child. Kiln's `worker.gc_mode = freeze`. *See:* [concepts/gc-freeze.md](concepts/gc-freeze.md)
- **Frozen dataclass.** A dataclass made read-only with `frozen=True`; assigning to a field raises an error. *See:* [python/dataclasses.md](python/dataclasses.md).
- **FrozenInstanceError.** Raised when you assign to a field of a frozen dataclass. *See:* [python/slots-and-frozen-dataclasses.md](python/slots-and-frozen-dataclasses.md).
- **Function.** A named, reusable block of code defined with `def`. *See:* [python/functions.md](python/functions.md).
- **functools.partial / partial object.** `partial(f, 1, x=2)` builds a callable that calls `f(1, ..., x=2)`. The result keeps `.func`, `.args` and `.keywords`, and is picklable if `f` is. *See:* [python/functools-partial.md](python/functools-partial.md)
- **functools.wraps.** A helper that copies a function's name and docstring onto its wrapper. *See:* [python/decorators.md](python/decorators.md).

## G
- **Garbage collector (GC).** The part of CPython that finds and frees groups of objects that refer to each other (**reference cycles**) but are no longer used. Plain refcounting can't free those. *See:* K3, K7.
- **Gated module.** A Kiln module that needs a newer Python (3.14+ or 3.15+) and is only imported behind a version check.
- **Gateway.** Kiln's optional HTTP front-door process (K5).
- **`gc.collect` / `gc.set_threshold` / `gc.callbacks` / `gc.get_stats`.** Run a collection now / set the automatic-collection triggers (0 = never) / hooks called when a collection starts and stops / per-generation statistics. *See:* [python/gc-module.md](python/gc-module.md)
- **`gc.freeze()`.** Moves every currently tracked object into a "permanent generation" that the GC never scans again. Instagram's fix. *See:* K3.
- **`gc_refs`.** The per-object scratch counter the collector writes into each scanned object's GC header during a collection: the "copy-on-read" write that breaks sharing. *See:* [concepts/garbage-collector.md](concepts/garbage-collector.md)
- **Generation (worker).** A counter that increases on every reload. Workers from the old generation retire.
- **Generational hypothesis.** The observation that most objects die young. The generational GC exploits it by scanning new objects more often than old ones. *See:* K7.
- **Generator expression.** A comprehension in `(...)` that produces values one at a time, on demand, and can only be used once. *See:* [python/comprehensions.md](python/comprehensions.md).
- **Generic type.** A container type that names its item type: `list[int]`, `dict[str, Job]`. *See:* [python/type-hints.md](python/type-hints.md).
- **getattr.** `getattr(obj, "name")` reads an attribute whose name is a string. *See:* [python/importlib.md](python/importlib.md).
- **`get_context` / `set_start_method`.** `get_context("spawn")` returns a multiprocessing API bound to one start method; `set_start_method` changes the default for the whole program (once). *See:* [python/multiprocessing-basics.md](python/multiprocessing-basics.md)
- **GIL (Global Interpreter Lock).** A lock in standard CPython that lets only one thread run Python bytecode at a time. *See:* K4.
- **git.** The version-control tool that records snapshots (commits) of a project's files over time. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **@given.** Hypothesis's decorator that runs a test many times with generated inputs. *See:* [python/hypothesis-basics.md](python/hypothesis-basics.md).
- **`global`.** A statement that lets a function *assign* to a module-level variable (`global GRAPH`). Reading one never needs it. *See:* [python/closures.md](python/closures.md).
- **Global interpreter state.** Process-wide settings any library can change without telling anyone: the GC switch, signal handlers, `sys.setrecursionlimit`, the locale… *See:* [concepts/global-interpreter-state.md](concepts/global-interpreter-state.md)
- **Graceful (shutdown / reload).** Stopping or replacing workers *after* they finish their current jobs, instead of killing them mid-job.

## H
- **Handler / formatter (logging).** A handler decides where log lines go (stderr, a file); a formatter decides what they look like. *See:* [python/logging-basics.md](python/logging-basics.md).
- **Hashable.** Usable as a dict key or set member: its hash never changes (frozen dataclasses are hashable). *See:* [python/slots-and-frozen-dataclasses.md](python/slots-and-frozen-dataclasses.md).
- **Heartbeat.** A small "I'm alive" message a worker sends every second. If they stop, the master assumes the worker is hung.
- **Hex string.** A number written in base-16 digits (0–9, a–f); Kiln's job ids are 32 hex characters. *See:* [python/uuid.md](python/uuid.md).
- **High-cardinality label.** A metric label with unbounded values, such as a job id. A classic mistake, because it creates one time series per value.
- **Hypothesis (lab).** Your prediction, with reasons, before an experiment.
- **Hypothesis (library).** A Python testing library that generates many random inputs to find cases that break your code (property-based testing).

## I
- **Idempotency key.** A unique id (such as the job id) a task stores with its side effect, so a repeated run can notice "already done" and skip it. *See:* [concepts/delivery-semantics.md](concepts/delivery-semantics.md).
- **Idempotent.** Running it twice has the same effect as running it once. Required for safe retries.
- **Identity (is) / equality (==).** `a is b` asks "the same object?"; `a == b` asks "equal values?". *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Immortal object.** An object whose reference count never changes (PEP 683): None, True/False, small ints, many string literals. Reading it never writes its page. *See:* [concepts/immortal-objects.md](concepts/immortal-objects.md)
- **Immortal objects (PEP 683).** Objects whose reference count never changes (for example `None`), so touching them never writes to their memory.
- **import.** The statement that loads a module and gives you a name to use it: `import json`. *See:* [python/modules-and-imports.md](python/modules-and-imports.md).
- **Import boundary.** A rule about which Kiln packages may import which (for example, `master` never imports `worker`), enforced by a test. *See:* [spec/01 §5](spec/01-architecture.md).
- **Import hook / meta path finder.** Code that runs around imports, installed in `sys.meta_path`. A meta path finder is any object with a `find_spec` method. *See:* [python/import-hooks-meta-path.md](python/import-hooks-meta-path.md)
- **Import-time side effect.** Work a module does merely by being imported: opening files, starting threads, connecting to a database. Under fork it happens once in the parent; under spawn it happens again in every child. *See:* [concepts/import-system.md](concepts/import-system.md)
- **importlib.import_module.** Imports a module whose name is in a string; `load_app` uses it for "examples.demo:app". *See:* [python/importlib.md](python/importlib.md).
- **In-flight.** Sent to a worker but not finished yet.
- **Incref / decref.** Incrementing or decrementing an object's reference count. It happens on almost every use of an object, which is why *reading* a Python object writes to its memory page. *See:* [concepts/refcounting.md](concepts/refcounting.md)
- **Incremental GC.** A collector that does its work in small slices to keep pauses short. Python 3.14.0–3.14.4 shipped one, and it was reverted because of peak memory blowups. *See:* K7.
- **Index.** The position number of an item in a sequence, starting at 0: `argv[0]` is the first item. *See:* [python/slicing.md](python/slicing.md).
- **IndexError.** The error raised when you ask for an index that doesn't exist, such as `[1, 2][5]`. *See:* [python/slicing.md](python/slicing.md).
- **Inheritable fd.** A file descriptor that stays open in a program started with exec. Python makes new fds *non*-inheritable by default (PEP 446); `os.set_inheritable(fd, True)` opts one in. *See:* [concepts/fd-passing.md](concepts/fd-passing.md)
- **Inheritance / subclass.** A subclass starts from a parent class's attributes and methods and can add or change them: `class Status(StrEnum)`. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Inherited file descriptor.** After fork(), the child's fds point at the same open files and sockets as the parent's, which is why a worker must close the master's listener. *See:* [concepts/fork.md](concepts/fork.md).
- **__init__.** The method Python runs to set up a new instance right after creating it. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **__init__.py.** The file that makes a folder a package; it runs on the package's first import. *See:* [python/modules-and-imports.md](python/modules-and-imports.md).
- **Injection.** When data inserted into some output (SQL, HTML, a shell command) changes that output's *structure*, for example `<script>` inside a page. t-strings exist to prevent it. *See:* K9.
- **inspect.iscoroutinefunction.** True if a function was defined with `async def`. *See:* [python/inspect.md](python/inspect.md).
- **inspect.signature.** Describes a function's parameters; `.bind()` checks whether a call's arguments would fit. *See:* [python/inspect.md](python/inspect.md).
- **int / float.** Whole numbers (`42`) and approximate numbers with a fractional part (`0.1`). *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Interleaving (round-robin).** Running experiment variants alternately (A B C A B C…) so slow drift affects all of them equally.
- **Interning.** Keeping one shared copy of equal strings (`sys.intern`). Whether an interned string is immortal depends on the version and on how it was created. *See:* [concepts/immortal-objects.md](concepts/immortal-objects.md)
- **Interpreter.** The program that reads and runs Python code (`python3.15`). *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).
- **Interpreter finalization / final collection / module teardown.** What happens after the atexit handlers on a normal exit: stdio is flushed, a last garbage collection runs, and module globals are freed. `os._exit` skips all of it. *See:* [concepts/finalization-and-atexit.md](concepts/finalization-and-atexit.md)
- **Introspection.** Code examining code while it runs: its type, attributes, parameters. *See:* [python/inspect.md](python/inspect.md).
- **Invariant.** A statement that must always be true, such as "a terminal job never changes state".
- **IPC (inter-process communication).** Any way separate processes exchange data: pipes, sockets, shared memory, signals.
- **isinstance.** `isinstance(x, T)` asks "is x an instance of T or of a subclass of T?". *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Iterable.** Anything you can loop over with `for`: lists, strings, dicts, files, ranges... *See:* [python/comprehensions.md](python/comprehensions.md).

## J
- **Jitter.** A small random offset added to a limit or timer so many processes don't all act at the same moment (for example, every worker restarting together).
- **Job.** One request to run a task with specific arguments. It has an id and a state. *See:* [spec/03](spec/03-jobs-and-tasks.md).
- **JobView.** The JSON shape of a job that Kiln shows to clients. *See:* [spec/02 §3.5](spec/02-protocol.md#35-jobview-how-a-job-is-shown-to-clients).
- **JSON.** A text format for data: objects `{}`, arrays `[]`, strings, numbers, true/false/null.
- **JSONDecodeError.** The error `json.loads` raises on malformed JSON text. *See:* [python/json.md](python/json.md).

## K
- **Kernel.** The core of the operating system: it runs processes, manages memory and hardware, and enforces isolation. *See:* [concepts/what-is-a-process.md](concepts/what-is-a-process.md).
- **Key function.** A function passed as `key=` (for example to `sorted`) that computes what to compare: `key=len` sorts by length. *See:* [python/lambda.md](python/lambda.md).
- **Keyword argument.** An argument passed by name: `f(timeout=5)`. *See:* [python/functions.md](python/functions.md).
- **Keyword-only parameter.** A parameter after a bare `*` in a `def`; callers must pass it by name. *See:* [python/functions.md](python/functions.md).
- **KP/1.** Kiln Protocol version 1: length-prefixed JSON frames. *See:* [spec/02](spec/02-protocol.md).

## L
- **Lambda / anonymous function.** `lambda x: x * 2`: a one-expression function with no name of its own. *See:* [python/lambda.md](python/lambda.md).
- **Late binding.** A closure reads an outer variable's value when it's *called*, not when it's created. The classic bug: lambdas made in a loop all see the last value. *See:* [python/closures.md](python/closures.md)
- **Latency, p50 / p99.** How long one request takes. p99 is the time that 99% of requests beat (the slow tail).
- **Lazy deletion.** Marking an item as cancelled and skipping it when it comes up, instead of removing it right away (Kiln's TimerHeap does this). *See:* [python/heapq.md](python/heapq.md).
- **Lazy evaluation.** Computing a value only when it is actually needed. *See:* [python/comprehensions.md](python/comprehensions.md).
- **Lazy import (PEP 810, 3.15).** `lazy import x` delays actually importing `x` until it's first used.
- **Length prefix.** A fixed-size number sent before each message saying how many bytes it has. KP/1 uses 4 big-endian bytes. *See:* [concepts/framing.md](concepts/framing.md).
- **LIFO order.** Last in, first out: the handler registered last runs first. Registering Kiln's exit trick *first* makes it run *last*. *See:* [python/atexit.md](python/atexit.md)
- **Line endings.** The invisible characters ending each line of text: LF (`\n`) on Linux, CRLF (`\r\n`) on Windows. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **List / dict / set comprehension.** Comprehensions written in `[...]`, `{k: v ...}` or `{...}`, building a list, dict or set. *See:* [python/comprehensions.md](python/comprehensions.md).
- **list / tuple.** Ordered collections: a list can be changed (`[1, 2]`), a tuple cannot (`(1, 2)`). *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **List comprehension / dict comprehension / set comprehension.** Comprehensions written in `[...]`, `{k: v ...}` or `{...}`, building a list, dict or set. *See:* [python/comprehensions.md](python/comprehensions.md).
- **listen.** Turns a socket into a listening socket that accepts incoming connections. *See:* [concepts/sockets-and-socketpairs.md](concepts/sockets-and-socketpairs.md).
- **Listener.** The socket a server uses to accept new connections.
- **Listening socket.** A socket that waits for and accepts new connections. Each accepted connection gets its own new socket. *See:* [concepts/what-is-a-server.md](concepts/what-is-a-server.md).
- **Little-endian.** Least significant byte first; how x86 CPUs store numbers in memory. *See:* [python/struct.md](python/struct.md).
- **localhost (127.0.0.1).** The machine's own address. Traffic sent there never leaves the computer. *See:* [concepts/what-is-a-server.md](concepts/what-is-a-server.md).
- **Lock.** A turn-taking device: only one thread at a time may hold it (`threading.Lock`). Kiln uses one so two threads never interleave frame bytes. *See:* [python/threading-basics.md](python/threading-basics.md).
- **Lockfile.** A file (`uv.lock`) that pins the exact version of every dependency so installs are reproducible.
- **Log level.** The severity tag on a log line: DEBUG, INFO, WARNING, ERROR, CRITICAL.
- **Logging / logger.** Logging is a program writing a running diary of events; a logger is a named source of those messages (`logging.getLogger(__name__)`). *See:* [python/logging-basics.md](python/logging-basics.md).
- **Loop lag.** The delay between when an event-loop callback should run and when it actually runs. A big lag means something blocked the loop. *See:* K5.

## M
- **Magic string.** A bare string used as a code ("pass"); a typo in one fails silently. Enums avoid this. *See:* [python/enum.md](python/enum.md).
- **Main module / `__main__` guard / main guard.** The main module is the file run as the program; its `__name__` is `"__main__"`. The guard `if __name__ == "__main__":` stops code from running when the file is imported, including when spawn re-imports it in a child. *See:* [concepts/main-module.md](concepts/main-module.md), [python/if-name-main.md](python/if-name-main.md)
- **Manifest.** The list of task names and their options that a worker reports to the master in `ready`.
- **Marker (pytest).** A label on a test (`@pytest.mark.slow`) used to select, skip or configure tests. *See:* [python/pytest-basics.md](python/pytest-basics.md).
- **Master.** Kiln's boss process. It owns the queue and supervises workers.
- **Median / percentile.** The median is the middle value of sorted measurements, which isn't thrown off by outliers. The p-th percentile is the value that p% of measurements are below.
- **Memory hole.** A freed slot inside a page that still holds live objects. If the parent collects garbage before forking, children fill those holes with new objects and dirty the shared page. *See:* [concepts/gc-freeze.md](concepts/gc-freeze.md)
- **Memory isolation.** Each process has its own private memory; one process can't read or change another's by accident. *See:* [concepts/what-is-a-process.md](concepts/what-is-a-process.md).
- **memory.max / memory.events.** cgroup v2 files. `memory.max` sets the group's memory limit. `memory.events` counts what happened, including `oom_kill`. *See:* [spec/04 §4.2](spec/04-supervisor.md).
- **memoryview.** A zero-copy window onto existing bytes; slicing it doesn't duplicate data. *See:* [python/bytearray-and-memoryview.md](python/bytearray-and-memoryview.md).
- **Merge.** Combining the commits of one branch into another. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **Method.** A function defined inside a class, called on an instance: `path.read_text()`. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Min-heap / heap.** A list kept in a special order so that the smallest item is always at index 0. `heapq` maintains it; Kiln uses one for deadlines. *See:* [python/heapq.md](python/heapq.md).
- **Minor fault.** A page fault the kernel handles without touching the disk: mapping a zero page, or copying a shared page on write (copy-on-write). *See:* [concepts/virtual-memory-and-pages.md](concepts/virtual-memory-and-pages.md)
- **Module.** A `.py` file whose code other code can import. *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).
- **Module cache (`sys.modules`).** The dict of modules a process has already imported. A second `import x` finds it there, so a module's top-level code runs only once per process. *See:* [concepts/import-system.md](concepts/import-system.md)
- **Module spec / `exec_module`.** A module spec records a module's name, origin and loader; the loader's `exec_module` runs the module's code. gcwatch wraps `exec_module`. *See:* [python/import-hooks-meta-path.md](python/import-hooks-meta-path.md)
- **ModuleNotFoundError.** The error raised when an imported name can't be found on `sys.path`. *See:* [python/modules-and-imports.md](python/modules-and-imports.md).
- **Monkeypatching.** Replacing a module's or class's attribute at runtime. It works for everyone who looks the attribute up *later*, not for code that saved the old one. *See:* [python/monkeypatching.md](python/monkeypatching.md)
- **Monotonic clock.** A clock that only moves forward (`time.monotonic()`), used for measuring durations and deadlines.
- **Mount.** Attaching a filesystem at a folder. In WSL, your Windows C: drive is mounted at `/mnt/c`. *See:* [concepts/terminal-and-wsl.md](concepts/terminal-and-wsl.md).
- **__mp_main__.** The module name `multiprocessing` gives your main script when it re-imports it in a spawned child. That's why code under `if __name__ == "__main__":` doesn't run again there. *See:* [concepts/main-module.md](concepts/main-module.md).
- **Multi-phase init.** The modern way a C extension module initializes itself, using a list of *slots* (such as `Py_mod_gil`) instead of one init function. *See:* K4.
- **multiprocessing.** The stdlib package for running Python code in other processes. *See:* [python/multiprocessing-basics.md](python/multiprocessing-basics.md)
- **Mutable / immutable.** A mutable object can be changed in place (list, dict); an immutable one cannot (int, str, tuple). *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Mutable default.** A list or dict used as a default value: it is created once and shared by every call, a classic bug. *See:* [python/functions.md](python/functions.md).

## N
- **__name__.** A variable every module has: `"__main__"` when the file is run as the program, its module name when imported. *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).
- **Nested function / closure.** A `def` inside another function. If it uses the outer function's variables it's a closure: a function plus the variables it keeps alive. *See:* [python/closures.md](python/closures.md)
- **Non-blocking I/O.** Socket operations that return immediately, with whatever data is available or "nothing yet", instead of waiting.
- **None.** Python's "no value" value. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **`nonlocal`.** Lets a nested function *assign* to a variable of the enclosing function, instead of creating a new local one. *See:* [python/closures.md](python/closures.md)

## O
- **Object.** A value in memory that knows its own type: `42`, `"hi"`, a list, a function. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **OOM killer.** The Linux kernel component that kills a process with `SIGKILL` when memory runs out (system-wide, or in a cgroup). The victim gets no warning and can't catch it.
- **oom_kill counter.** The `oom_kill N` line of a cgroup's `memory.events`: how many processes the kernel OOM-killed in that group. Kiln compares it before and after a SIGKILL to confirm an OOM kill. *See:* [concepts/cgroups-and-oom-killer.md](concepts/cgroups-and-oom-killer.md).
- **oom_score_adj.** A per-process number (−1000 to 1000) that biases the OOM killer's choice. Kiln workers raise theirs to 500 so the master survives ([ADR-029](decisions.md#adr-029)).
- **Option (command line).** A named argument such as `--json`, usually optional and with a default. *See:* [python/argparse.md](python/argparse.md).
- **Orphan.** A process whose parent died. Linux re-parents it to pid 1 (init), so the original parent can no longer reap it.
- **os._exit.** Ends the process immediately: no `finally` blocks, no atexit handlers, no buffer flushing. The correct way out of a forked child (ADR-028). *See:* [python/os-module.md](python/os-module.md).
- **os.fork.** Copies the running process; returns 0 in the child and the child's pid in the parent. *See:* [python/os-module.md](python/os-module.md).
- **os.waitpid.** Collects a child's wait status (and reaps it). *See:* [python/os-module.md](python/os-module.md).
- **OSError.** The error raised when the operating system refuses something: a missing file, no permission, a busy port. *See:* [python/exceptions.md](python/exceptions.md).
- **Output buffer.** Bytes queued in the program because the kernel didn't accept them all yet; sent later when the socket is writable. *See:* [concepts/non-blocking-io-and-selectors.md](concepts/non-blocking-io-and-selectors.md).

## P
- **Package.** A folder of modules with an `__init__.py`; `kiln` is a package. *See:* [python/modules-and-imports.md](python/modules-and-imports.md).
- **Page.** The unit of memory the kernel manages: 4 KiB on x86-64. *See:* [concepts/memory-metrics.md](concepts/memory-metrics.md).
- **Page fault.** The CPU's trap when a program touches memory with no RAM page behind it yet (or a shared page it must copy); the kernel then supplies one. *See:* [concepts/memory-metrics.md](concepts/memory-metrics.md).
- **Page table.** The per-process map from virtual pages to physical page frames. `fork` copies the page table, not the pages. *See:* [concepts/virtual-memory-and-pages.md](concepts/virtual-memory-and-pages.md)
- **Parameter / argument.** A parameter is the name in the `def`; an argument is the value passed in a call. *See:* [python/functions.md](python/functions.md).
- **parametrize.** `@pytest.mark.parametrize` runs one test function once per row of a table of inputs. *See:* [python/pytest-basics.md](python/pytest-basics.md).
- **Parent process / child process.** The process that started another one is its parent; the started one is its child. Kiln's master is the parent of its workers. *See:* [concepts/what-is-a-process.md](concepts/what-is-a-process.md).
- **Parser.** In argparse, the object that knows which arguments a command accepts. *See:* [python/argparse.md](python/argparse.md).
- **Partial read.** A recv() that returns only part of a message (or parts of two). The decoder must cope. *See:* [concepts/framing.md](concepts/framing.md).
- **Path / Path object.** A path is a file or folder's address; a pathlib `Path` object wraps one with methods (`.read_text()`, `/` joining). *See:* [python/pathlib.md](python/pathlib.md).
- **PEP.** Python Enhancement Proposal, a design document for a Python change. *Example:* PEP 750 (t-strings).
- **PEP 442.** The Python 3.4 change that lets the GC collect reference cycles even when objects in them have `__del__` methods. *See:* ADR-016.
- **PEP 446.** File descriptors created by Python are non-inheritable by default, so child processes don't get them unless you ask. *See:* K2.
- **perf_counter.** The highest-resolution monotonic clock (`time.perf_counter()`), for timing short operations. *See:* [python/time-monotonic.md](python/time-monotonic.md).
- **Permanent generation.** Where `gc.freeze()` puts objects: the GC never scans them again. *See:* K3.
- **pickle.** Python's built-in way to turn objects into bytes and back. Unsafe on untrusted data. Required by `multiprocessing` spawn/forkserver.
- **Pickle by reference.** pickle stores functions and classes as "module + qualified name", not as code, so the receiving process must be able to import them under that name. *See:* [concepts/pickling.md](concepts/pickling.md)
- **Pickle protocol.** The version of pickle's byte format. Protocol 5 is the default and the highest on Kiln's Python versions. *See:* [python/pickle-and-reduce.md](python/pickle-and-reduce.md)
- **`pickle.dumps` / `pickle.loads`.** Object to bytes, and bytes back to an object. *See:* [python/pickle-and-reduce.md](python/pickle-and-reduce.md)
- **Pickling / unpickling / picklable.** Turning an object into bytes with `pickle`, and back again. An object is picklable if it survives that round trip; lambdas and nested functions don't. *See:* [concepts/pickling.md](concepts/pickling.md)
- **pid.** Process id, the number the OS uses to identify a process.
- **Pid reuse.** After a process is reaped, the kernel may give its pid to a new, unrelated process. That's why signalling a remembered pid can hit the wrong process, and why Kiln uses pidfds. *See:* [concepts/reparenting-and-orphans.md](concepts/reparenting-and-orphans.md)
- **Pidfd.** A file descriptor that refers to a process and becomes readable when that process exits. Unlike a pid, it can't be confused with a later process that reuses the same number. *See:* [spec/04 §6](spec/04-supervisor.md).
- **Pipe.** A one-way channel of bytes between processes: what one writes, the other reads. `a | b` in the shell connects a's stdout to b's stdin. *See:* [concepts/files-and-file-descriptors.md](concepts/files-and-file-descriptors.md).
- **PIPE_BUF.** The size (4096 bytes on Linux) below which a single write to a pipe is guaranteed not to interleave with other writers. It matters for multi-process logging.
- **Pipelining.** Sending several requests without waiting for each reply, matched up later by an id (`req_id`).
- **`Pool`.** A `multiprocessing` set of worker processes that run the function calls you hand it. *See:* [python/multiprocessing-basics.md](python/multiprocessing-basics.md)
- **Port.** A number (0–65535) that picks which program on a machine a network connection is for. Kiln uses 7340 and 7341. *See:* [concepts/what-is-a-server.md](concepts/what-is-a-server.md).
- **Port 0.** Asking the kernel to pick any free port when binding a socket. Tests use it so they never collide.
- **Positional argument.** A command-line argument recognised by its position, with no `--name` in front. *See:* [python/argparse.md](python/argparse.md).
- **PosixPath.** The kind of `Path` object you get on Linux. *See:* [python/pathlib.md](python/pathlib.md).
- **Practical significance.** Whether a real, measured difference is big enough to matter. A 0.1% speedup can be statistically real and practically irrelevant.
- **Precision / recall.** For a checker: precision is the share of its findings that are real problems; recall is the share of real problems it found. *See:* K2.
- **Preload.** Importing the app in the master *before* forking, so workers share its memory via copy-on-write.
- **Priority queue.** A queue that always hands out the most urgent item next; a heap is the usual way to build one. *See:* [python/heapq.md](python/heapq.md).
- **Private_Dirty / Shared_Clean.** Lines in `/proc/<pid>/smaps_rollup`. Private_Dirty is memory written by and owned only by this process. Shared_Clean is pages shared with others and never modified. *See:* K3.
- **/proc.** A folder of pseudo-files that the Linux kernel generates on demand to describe processes and the system (`/proc/<pid>/status`, `/proc/meminfo`). *See:* [concepts/what-is-a-process.md](concepts/what-is-a-process.md).
- **Process.** A running program with its own memory, file descriptors and pid. *See:* `concepts/what-is-a-process.md` (K0).
- **Process group.** A set of processes that receive terminal signals (like Ctrl-C's SIGINT) together.
- **Processor (t-string).** A function that turns a t-string `Template` into final output, for example by HTML-escaping every interpolated value. *See:* K9.
- **Program.** Instructions stored in a file on disk. A program does nothing until it is run as a process. *See:* [concepts/what-is-a-process.md](concepts/what-is-a-process.md).
- **Prometheus text format.** A simple plain-text format for metrics (`name{label="x"} 42`) that monitoring tools scrape from `/metrics`. *See:* [spec/06 §4](spec/06-observability.md).
- **Property (testing).** A rule that must hold for every input, such as "decoding an encoded frame returns the original". *See:* [python/hypothesis-basics.md](python/hypothesis-basics.md).
- **Property-based testing.** Testing by stating a rule ("decoding an encoded frame gives back the original") and letting a library such as Hypothesis generate hundreds of inputs. When something fails it *shrinks* the input to the smallest failing example.
- **Protocol (networking).** An agreement about what bytes mean.
- **PSS / RSS / USS.** Memory measurements. **RSS** counts every page a process uses, shared ones included. **PSS** divides each shared page among the processes that share it. **USS** counts only pages private to the process. *See:* K3 `concepts/memory-metrics.md`.
- **ptrace / ptrace_scope.** ptrace is the Linux facility that lets one process inspect another (debuggers, profilers, `asyncio ps`). The Yama setting `ptrace_scope` restricts it, and the value 1 means "only your own children". *See:* [spec/07](spec/07-environment.md).
- **Py_mod_gil.** The slot a C extension uses to declare "I'm safe without the GIL". Without it, importing the extension on a free-threaded build turns the GIL back on. *See:* K4, ADR-015.
- **PyDLL.** The `ctypes` loader whose calls keep the GIL held (normal `CDLL` calls release it). Used by `labs.hold_gil`.
- **python -m.** Runs a module by its import name: `python -m kiln`, `python -m pytest`. *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).
- **python-build-standalone.** The relocatable, prebuilt CPython builds that `uv python install` downloads.

## Q
- **Qualified name (qualname) / `__qualname__`.** The dotted path to a definition inside its module, for example `outer.<locals>.inner` for a nested function. The `<locals>` part is why such a function can't be pickled. *See:* [python/closures.md](python/closures.md)
- **Queue.** Jobs waiting for a worker, in FIFO order.
- **Quoting.** Wrapping text in `"..."` or `'...'` in the shell so spaces and special characters aren't split or expanded. Essential for paths like `Karthik Reddy`. *See:* [concepts/terminal-and-wsl.md](concepts/terminal-and-wsl.md).

## R
- **Race condition.** A result that depends on the exact timing of threads or processes; usually a bug. *See:* [python/threading-basics.md](python/threading-basics.md).
- **raise.** The statement that starts an exception: `raise ValueError("bad")`. *See:* [python/exceptions.md](python/exceptions.md).
- **Ratio of medians.** The median of variant B divided by the median of A. If its confidence interval includes 1.0, we report "no detectable difference". *See:* [spec/10](spec/10-measurement-methodology.md).
- **Read-modify-write.** Updating a value by reading it, changing it and writing it back (`count += 1`). It isn't atomic, so another thread can sneak in between the steps. *See:* K4.
- **Readiness.** "This operation won't wait right now." A readable socket has *some* data, not necessarily a whole message. *See:* [concepts/non-blocking-io-and-selectors.md](concepts/non-blocking-io-and-selectors.md).
- **Reap / reaping.** A parent calling `waitpid()` to collect a dead child's exit status, which removes the **zombie**.
- **Recycling (worker).** Retiring a worker on purpose after N jobs or X MB, to contain slow leaks.
- **Redirection.** Pointing a program's standard streams at a file or another program with `>`, `2>` or `2>&1` in the shell. *See:* [concepts/files-and-file-descriptors.md](concepts/files-and-file-descriptors.md).
- **`__reduce__`.** A method that tells pickle how to rebuild an object: it returns a `(callable, args)` recipe. *See:* [python/pickle-and-reduce.md](python/pickle-and-reduce.md)
- **Reference count (refcount).** How many references point at an object. CPython frees an object when this reaches 0. Changing it is a *write* to the object's memory, which breaks copy-on-write.
- **Reference cycle.** Objects that refer to each other in a loop, so their refcounts never reach 0 on their own.
- **register / unregister.** Start or stop watching a socket with a selector. *See:* [python/selectors-module.md](python/selectors-module.md).
- **Release build.** Native code compiled with optimizations on. Benchmarks of Rust code must use release builds. *See:* K8.
- **Reload.** Replacing all workers with new ones (on SIGHUP) without dropping jobs.
- **Remote attach (PEP 768).** Running a debugger or code inside an already-running Python process at a safe point (`python -m pdb -p PID`, `sys.remote_exec`). *See:* K6.
- **Reparenting / subreaper.** When a process's parent dies, the kernel gives the orphan a new parent: the nearest ancestor marked as a subreaper, or pid 1. On WSL that's the `Relay(...)` process. *See:* [concepts/reparenting-and-orphans.md](concepts/reparenting-and-orphans.md)
- **REPL.** Read–Eval–Print Loop: the interactive `>>>` prompt where Python runs each line as you type it. *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).
- **Replacement field.** One `{...}` inside an f-string. *See:* [python/f-strings.md](python/f-strings.md).
- **Repository.** A project folder plus its full history, stored in the hidden `.git/` folder. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **__repr__ / repr.** How an object shows itself to developers (`repr(x)`, `{x!r}`); for strings it includes the quotes. *See:* [python/dataclasses.md](python/dataclasses.md).
- **`req_id`.** A client-chosen number in each KP/1 request, echoed in the response.
- **Request / response.** One message asking for something, and the one message that answers it. *See:* [concepts/what-is-a-server.md](concepts/what-is-a-server.md).
- **Request smuggling.** An attack where two HTTP servers disagree on where one request ends and the next begins. A reason Kiln's parser rejects ambiguous input. *See:* K5.
- **Resident memory.** The part of a process's memory currently held in RAM (its RSS). *See:* [concepts/memory-metrics.md](concepts/memory-metrics.md).
- **Resource leak.** Something (a file, socket, lock) that was opened or taken and never released. *See:* [python/context-managers.md](python/context-managers.md).
- **Resurrection.** A `__del__` method that stores `self` somewhere, so an object that was about to die stays alive. One of K6's planted leaks.
- **Return annotation.** The `-> T` part of a `def`, saying what type the function returns. *See:* [python/type-hints.md](python/type-hints.md).
- **Return value.** What a function hands back with `return`; `None` if there is no `return`. *See:* [python/functions.md](python/functions.md).
- **RuleBasedStateMachine.** Hypothesis's tool for testing stateful objects: it generates random sequences of operations and checks the invariants after each one. Used for Kiln's JobTable.
- **Run / variant / experiment.** An experiment compares variants (for example gc_mode=default vs freeze). Each variant is measured over several runs, and every run produces one ExperimentRecord.
- **runpy / `run_path` / `run_module`.** The stdlib module behind `python -m`: `run_module` runs a module by import name and `run_path` runs a file, each as a fresh module with a chosen `__name__`. forkcheck's `--confirm` uses it. *See:* [python/runpy.md](python/runpy.md)

## S
- **SafeHTML.** Kiln's marker type for HTML that is already escaped and must not be escaped again by the t-string processor. *See:* K9.
- **Sans-IO.** Designing a protocol parser that only turns bytes into events and never touches sockets itself, which makes it easy to test. *See:* K5.
- **SCM_RIGHTS.** The Unix-socket feature that sends an open file descriptor to another process (`socket.send_fds`). *See:* [concepts/fd-passing.md](concepts/fd-passing.md).
- **Scope.** A region where a name lives: the module, a function, or a class body. A name assigned inside a function is local to that function's scope. *See:* [concepts/ast-and-symtable.md](concepts/ast-and-symtable.md)
- **Script.** A `.py` file you run as a program: `python file.py`. *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).
- **Selector / `selectors`.** The stdlib module for waiting on many sockets at once. *See:* K1.
- **SelectorKey.** What a selector returns for each ready socket: the file object, its fd, the events, and your `data`. *See:* [python/selectors-module.md](python/selectors-module.md).
- **self.** The name a method uses for the instance it was called on. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Self-pipe trick.** Making a signal wake an event loop by writing a byte to a pipe or socket the loop is watching. Python's `signal.set_wakeup_fd` does this.
- **send / recv.** send() writes some bytes and returns how many it wrote; recv(n) reads up to n bytes. *See:* [python/socket-module.md](python/socket-module.md).
- **sendall.** Keeps calling send() until every byte is written (for blocking sockets). *See:* [python/socket-module.md](python/socket-module.md).
- **Serialize / deserialize.** Turn values into text or bytes for storage or sending, and back again. *See:* [python/json.md](python/json.md).
- **Server.** A long-running process that waits for requests and answers them. *See:* [concepts/what-is-a-server.md](concepts/what-is-a-server.md).
- **set.** An unordered collection with no duplicates: `{1, 2, 3}`. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Shared page / private page.** A page mapped by more than one process vs. by only this one. After `fork` everything starts shared; each write makes a private copy. *See:* [concepts/copy-on-write.md](concepts/copy-on-write.md)
- **Shell.** The program inside the terminal that reads your commands and runs them, such as bash or zsh. *See:* [concepts/terminal-and-wsl.md](concepts/terminal-and-wsl.md).
- **Shim.** A wrapper put in place of a function so its calls can be observed; gcwatch's layer A replaces `gc.enable` with one. *See:* [python/monkeypatching.md](python/monkeypatching.md)
- **Shrinking.** Hypothesis simplifying a failing input step by step to the smallest one that still fails. *See:* [python/hypothesis-basics.md](python/hypothesis-basics.md).
- **Side effect.** A change a function makes outside itself: an email sent, a row written, a file deleted. *See:* [concepts/delivery-semantics.md](concepts/delivery-semantics.md).
- **SIG_IGN / SIG_DFL.** The special handlers meaning "ignore this signal" and "use the default action". Workers set SIGINT to SIG_IGN (ADR-024). *See:* [python/signal-module.md](python/signal-module.md).
- **SIGCHLD.** The signal a parent receives when a child process exits or stops; Kiln's master reaps when it arrives. *See:* [concepts/zombies-and-reaping.md](concepts/zombies-and-reaping.md).
- **Signal.** A tiny asynchronous notification sent to a process: SIGINT (Ctrl-C), SIGTERM (please stop), SIGKILL (die now, can't be caught), SIGHUP, SIGCHLD (a child changed state)… *See:* K1 `concepts/signals.md`.
- **Signal disposition.** What a process does when a given signal arrives: the default action, ignore it (SIG_IGN), or run a handler. *See:* [concepts/fork.md](concepts/fork.md).
- **Signal handler.** A function the program installs to run when a signal arrives. Kiln's handlers only record the signal. *See:* [concepts/signals.md](concepts/signals.md).
- **Signals enum.** `signal.Signals`: named signal numbers, so `Signals(9).name` is "SIGKILL". *See:* [python/signal-module.md](python/signal-module.md).
- **Slicing.** Taking part of a sequence with `[start:stop]`: `argv[1:]` is everything after the first item. *See:* [python/slicing.md](python/slicing.md).
- **slots / __slots__.** A fixed attribute layout for a class, with no per-object `__dict__`: smaller objects, and typos raise errors. `@dataclass(slots=True)` sets it up. *See:* [python/slots-and-frozen-dataclasses.md](python/slots-and-frozen-dataclasses.md).
- **Small int cache.** Preallocated, immortal int objects: −5…256 on 3.13 and 3.14, −5…1024 on 3.15 (measured, gate G3.6). *See:* [concepts/immortal-objects.md](concepts/immortal-objects.md)
- **smaps_rollup.** `/proc/<pid>/smaps_rollup`: the kernel's summed memory accounting over all of a process's mappings (Rss, Pss, Shared/Private, Clean/Dirty, Swap). Read by `kiln mem`. *See:* [concepts/smaps.md](concepts/smaps.md)
- **Snippet test.** A test that runs every code snippet in the primers so the docs can't go stale.
- **SO_REUSEADDR.** A socket option that lets a restarted server bind its port again right away. *See:* [python/socket-module.md](python/socket-module.md).
- **Socket.** An endpoint for sending and receiving bytes, over a network (TCP) or locally (Unix domain sockets).
- **Socket object.** Python's handle on an operating-system socket, an fd that carries bytes. *See:* [python/socket-module.md](python/socket-module.md).
- **socketpair.** Two already-connected local sockets. Kiln creates one per worker.
- **Soundness / completeness.** A sound checker never misses a real problem (no false negatives); a complete one never raises a false alarm (no false positives). Useful checkers are usually neither. *See:* [concepts/static-vs-dynamic-analysis.md](concepts/static-vs-dynamic-analysis.md)
- **Spawn.** A start method that launches a completely fresh Python interpreter.
- **src layout.** Keeping the package under `src/` so that tests run against the *installed* package, not whatever happens to be in the current folder.
- **Staging area (index).** Git's list of changes that will go into the next commit (`git add` puts them there). *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **Start method.** How a new worker process gets created: fork, spawn or forkserver. *See:* K2.
- **State.** One of a fixed set of conditions something can be in, such as QUEUED or RUNNING. *See:* [concepts/state-machines.md](concepts/state-machines.md).
- **State machine.** A model with a fixed set of states and allowed transitions between them. *See:* [spec/03 §4](spec/03-jobs-and-tasks.md#4-the-job-state-machine).
- **Stateful testing.** Testing by generating random *sequences of operations* and checking invariants after each step (RuleBasedStateMachine). *See:* [python/hypothesis-basics.md](python/hypothesis-basics.md).
- **Static analysis.** Examining code without running it, by reading its syntax tree. *See:* K2.
- **Static analysis / dynamic analysis.** Static analysis inspects code without running it (Kiln's `forkcheck`); dynamic analysis runs it and watches (forkcheck's `--confirm`). *See:* [concepts/static-vs-dynamic-analysis.md](concepts/static-vs-dynamic-analysis.md)
- **stdin / stdout / stderr.** A process's three standard streams: fd 0 (input), fd 1 (normal output) and fd 2 (errors and logs). *See:* [concepts/files-and-file-descriptors.md](concepts/files-and-file-descriptors.md).
- **Stdlib (standard library).** The modules that ship with Python itself.
- **str.** Python's text type: a sequence of characters. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Strategy (Hypothesis).** A recipe for generating test inputs, such as `st.integers()` or `st.text()`. *See:* [python/hypothesis-basics.md](python/hypothesis-basics.md).
- **StrEnum / IntEnum.** Enums whose members are also plain strings or integers. *See:* [python/enum.md](python/enum.md).
- **struct.** The stdlib module that converts numbers to and from raw bytes. *See:* [python/struct.md](python/struct.md).
- **Structural typing / Protocol.** Types that match by shape (methods and attributes), not inheritance; `typing.Protocol` lets mypy check them. *See:* [python/typing-protocol.md](python/typing-protocol.md).
- **Structured log.** A log where each line is one event made of machine-readable fields (`key=value` or JSON), not free-form prose. *See:* [spec/06](spec/06-observability.md).
- **Subcommand.** A word that picks an action of a bigger command, like `doctor` in `kiln doctor`. *See:* [python/argparse.md](python/argparse.md).
- **Subinterpreter.** A separate Python interpreter inside the same process, with its own modules and globals. *See:* K4.
- **super().** Calls the parent class's version of a method from inside a subclass. *See:* [python/classes-and-objects.md](python/classes-and-objects.md).
- **Swap.** Disk space the kernel uses as overflow memory. With swap, a memory-hungry process slows down instead of being killed, which is why the OOM lab sets `MemorySwapMax=0`. *See:* [concepts/cgroups-and-oom-killer.md](concepts/cgroups-and-oom-killer.md).
- **Symbol table / SymbolTable / Symbol.** The compiler's record of every name in every scope and whether it is local, global or free. Python's `symtable` module exposes it: one `SymbolTable` per scope, one `Symbol` per name. *See:* [python/symtable-module.md](python/symtable-module.md)
- **`symtable.symtable`.** Builds the symbol table for a piece of source code without running it. *See:* [python/symtable-module.md](python/symtable-module.md)
- **`sys.getrefcount` / `sys._is_immortal`.** An object's reference count (plus 1 for the argument itself) / whether it is immortal (3.14+). *See:* [python/sys-getrefcount.md](python/sys-getrefcount.md)
- **`sys.meta_path`.** The list of finders every import consults, in order. *See:* [python/import-hooks-meta-path.md](python/import-hooks-meta-path.md)
- **sys.modules.** Python's cache of modules already imported; a second import reuses the entry. *See:* [python/modules-and-imports.md](python/modules-and-imports.md).
- **sys.path.** The list of folders Python searches when you import something. *See:* [python/modules-and-imports.md](python/modules-and-imports.md).
- **Syscall (system call).** A request from a program to the operating system kernel, such as `fork`, `read`, `waitpid` or `kill`.
- **systemd scope.** A temporary cgroup that systemd creates for a command (`systemd-run --scope`). Note: by default systemd stops the whole scope after an OOM kill inside it (`OOMPolicy=stop`). *See:* [concepts/cgroups-and-oom-killer.md](concepts/cgroups-and-oom-killer.md).
- **systemd-run scope.** A one-off cgroup created by `systemd-run --scope`, used to run Kiln under a memory limit for the OOM lab. *See:* [spec/07](spec/07-environment.md).

## T
- **t-string (PEP 750, 3.14).** `t"Hello {name}"` produces a `Template` that keeps the fixed text and the values separate, so they can be escaped safely. *See:* K9.
- **Task.** A named Python function registered with `@app.task`.
- **TCP.** The network protocol that gives a reliable, ordered **stream** of bytes (with no message boundaries).
- **Teach-back.** Checking your understanding by explaining a concept in your own words. K10's final exercise.
- **Template / Interpolation.** What a t-string produces: a `Template` holding the fixed text parts plus `Interpolation` objects for each `{value}`. *See:* K9.
- **Terminal.** The window where you type commands and read their output. *See:* [concepts/terminal-and-wsl.md](concepts/terminal-and-wsl.md).
- **Terminal state.** A job state that never changes again: SUCCEEDED, FAILED, TIMED_OUT, LOST, CANCELLED.
- **Terminated by signal.** A process that was killed by a signal has no exit code; waitpid reports the signal number instead. *See:* [concepts/exit-status.md](concepts/exit-status.md).
- **Test / test function.** Code that runs your code and checks the result; in pytest, a function named `test_...` in a file named `test_...`. *See:* [python/pytest-basics.md](python/pytest-basics.md).
- **Test collection.** pytest's automatic search for test files and test functions. *See:* [python/pytest-basics.md](python/pytest-basics.md).
- **Test pyramid.** Many fast unit tests, fewer integration tests, and only a few slow end-to-end tests. *See:* [spec/09](spec/09-testing-strategy.md).
- **Thread.** A separate line of execution inside a process, sharing the process's memory. A worker's heartbeat runs on its own thread. *See:* [python/threading-basics.md](python/threading-basics.md).
- **Threshold / automatic collection.** The GC runs *automatically* when allocations minus deallocations exceed `threshold0` (2000 on Kiln's versions). `gc.set_threshold(0)` turns automatic collections off; an explicit `gc.collect()` still runs. *See:* [concepts/garbage-collector.md](concepts/garbage-collector.md)
- **Throughput.** How many jobs per second the system completes.
- **Timer.** An action scheduled for a deadline, such as "kill this job's worker if it's still running at t=31.5". *See:* [concepts/event-loops.md](concepts/event-loops.md).
- **tmp_path.** A pytest fixture that gives each test its own fresh, empty temporary folder. *See:* [python/pytest-basics.md](python/pytest-basics.md).
- **TOML.** A config file format: `key = value` under `[section]` headers. Read with the stdlib `tomllib`.
- **TOML table.** A `[section]` in a TOML file; it becomes a dict. *See:* [python/tomllib.md](python/tomllib.md).
- **TOMLDecodeError.** The error `tomllib` raises for invalid TOML. *See:* [python/tomllib.md](python/tomllib.md).
- **tomllib.** The stdlib module that reads TOML files (read-only). *See:* [python/tomllib.md](python/tomllib.md).
- **Traceback.** The report Python prints for an uncaught exception: the chain of calls that led there. Read it from the bottom. *See:* [python/exceptions.md](python/exceptions.md).
- **`traceback.extract_stack` / `traceback.format_list` / `FrameSummary`.** The current call stack as a list of FrameSummary objects (filename, line number, function, source line) / those frames as traceback text. *See:* [python/traceback-module.md](python/traceback-module.md)
- **`tracemalloc` / `tracemalloc.get_traced_memory`.** The stdlib allocation tracer / its (current, peak) traced bytes. It keeps an entry per live allocation, which is why Kiln traces only a sample of the preload graph (ADR-036). *See:* [python/tracemalloc-basics.md](python/tracemalloc-basics.md)
- **Tracked object.** A container object (list, dict, class instance…) the cycle collector knows about; it has a GC header the collector writes into. *See:* [concepts/garbage-collector.md](concepts/garbage-collector.md)
- **Transition / transition table.** A move from one state to another; the table lists every allowed (from, to) pair. Kiln's is `_ALLOWED` in jobs.py. *See:* [concepts/state-machines.md](concepts/state-machines.md).
- **try / except / else / finally.** Catch exceptions (`except`), run code only on success (`else`), and always clean up (`finally`). *See:* [python/exceptions.md](python/exceptions.md).
- **TTL (time to live).** How long something is kept before it's deleted automatically.
- **Type.** The kind of an object, which decides what you can do with it: `type(42)` is `int`. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Type checker / mypy.** A tool that reads annotations and finds type mistakes without running the code. Kiln uses mypy. *See:* [python/type-hints.md](python/type-hints.md).
- **Type hint (annotation).** A note about the expected type: `def f(x: int) -> str:`. Python doesn't enforce it; tools like mypy check it. *See:* [python/type-hints.md](python/type-hints.md).

## U
- **Union type.** `A | B`: a value that may be either type. *See:* [python/typing-protocol.md](python/typing-protocol.md).
- **Unix domain socket.** A socket for processes on the same machine (AF_UNIX), with no network involved. socketpair() creates two connected ones. *See:* [concepts/sockets-and-socketpairs.md](concepts/sockets-and-socketpairs.md).
- **Unix epoch.** 1970-01-01 00:00 UTC; `time.time()` counts seconds since then. *See:* [python/time-monotonic.md](python/time-monotonic.md).
- **Usage error / exit code 2.** The command was used incorrectly (unknown flag, missing argument). By Unix convention the exit code is 2. *See:* [python/argparse.md](python/argparse.md).
- **UTF-8.** The standard way to encode text as bytes.
- **UUID / uuid4.** A 128-bit unique identifier; uuid4 makes a random one (122 random bits). *See:* [python/uuid.md](python/uuid.md).
- **uv.** A fast tool that installs Python versions, creates virtual environments and manages dependencies.
- **uv run.** Runs a command inside the project's virtual environment, creating or updating it first if needed. *See:* [python/running-python-and-the-repl.md](python/running-python-and-the-repl.md).

## V
- **ValueError.** The error for "right type, wrong value", such as `int("abc")`. *See:* [python/exceptions.md](python/exceptions.md).
- **Variable (name).** A label pointing at an object. `y = x` makes a second label for the same object; it doesn't copy it. *See:* [python/variables-and-types.md](python/variables-and-types.md).
- **Verification gate.** A step where we check a version-specific claim against the real interpreter before relying on it ([ADR-020](decisions.md#adr-020)).
- **Virtual address / physical memory.** Every address a process uses is *virtual*: a number in its own private map. The CPU's MMU translates it to a location in *physical memory* (the real RAM), one page at a time. *See:* [concepts/virtual-memory-and-pages.md](concepts/virtual-memory-and-pages.md)
- **Virtual environment (venv).** A folder with its own Python interpreter link and installed packages, isolated from the rest of the system.
- **Virtual memory.** The address space a process may use. Much of it is only reserved and has no RAM behind it until touched. *See:* [concepts/memory-metrics.md](concepts/memory-metrics.md).
- **VmHWM / ru_maxrss.** Peak resident memory of a process. VmHWM is in `/proc/<pid>/status`. `ru_maxrss` comes from `resource.getrusage`, and it is in **KiB** on Linux. *See:* K7.
- **VmSize.** A process's total virtual memory size (from /proc/<pid>/status), usually much larger than its RSS. *See:* [concepts/memory-metrics.md](concepts/memory-metrics.md).

## W
- **Wait status.** The integer `waitpid` returns, which packs "exited with code N" or "killed by signal S" into one number. *See:* [spec/04 §4.1](spec/04-supervisor.md).
- **waitpid.** The syscall a parent uses to learn how a child process ended, and to reap it.
- **Wakeup fd.** A file descriptor that Python writes a byte to whenever a signal arrives, which wakes the event loop (`signal.set_wakeup_fd`).
- **Wall-clock time.** Real-world time (`time.time()`); it can jump when the clock is adjusted, so never use it for durations. *See:* [python/time-monotonic.md](python/time-monotonic.md).
- **Watchdog.** A separate thread or timer that notices when something (like the event loop) stopped responding, and records what it was stuck on. *See:* K5.
- **Weak reference.** A reference that doesn't keep its target alive (`weakref.ref`). It is useful for caches that shouldn't cause leaks. *See:* K6.
- **Wheel / wheel tag.** A wheel is Python's prebuilt package format (`.whl`). Its filename tag records the Python version, ABI and platform it works on.
- **with statement.** `with open(p) as f:` runs a context manager's setup, the block, then its cleanup. *See:* [python/context-managers.md](python/context-managers.md).
- **WNOHANG.** The waitpid() option meaning "return immediately if no child has exited" instead of waiting. *See:* [concepts/zombies-and-reaping.md](concepts/zombies-and-reaping.md).
- **Worker.** A Kiln child process that runs jobs.
- **Working tree.** The files you see and edit in the project folder, as opposed to the history stored in `.git/`. *See:* [concepts/git-basics.md](concepts/git-basics.md).
- **Wrapper function.** The new function a decorator returns, which runs instead of (and usually calls) the original. *See:* [python/decorators.md](python/decorators.md).
- **WSL2.** Windows Subsystem for Linux: a real Linux kernel running inside Windows. Kiln runs there.
- **.wslconfig.** The Windows file (`%UserProfile%\.wslconfig`) that sets WSL's memory and CPU limits.

## X
- **X | None.** A type meaning "an X, or None" (nothing). *See:* [python/type-hints.md](python/type-hints.md).

## Z
- **Zombie.** A dead child process whose exit status hasn't been collected (**reaped**) by its parent yet.
