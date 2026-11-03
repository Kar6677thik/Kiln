# K1: Supervisor (master/worker, signals, queue, reload, recycling, OOM detection)

> **Status:** 📝 specified · **Depends on:** K0 · **Branch:** `milestone/k1-supervisor` · **Size:** XL
> **Incident:** research §2.7 (Celery/pre-fork workers: memory multiplied by concurrency; Back Market's silent OOM kills; "worker lost")
> **Reference implementations (read *after* building):**
> - gunicorn `gunicorn/arbiter.py`: `Arbiter.run`, `init_signals`, `signal`, `sleep`, `wakeup`, `handle_hup`, `handle_ttin`/`handle_ttou`, `reload`, `manage_workers`, `spawn_worker`, `kill_worker`, `murder_workers`, `reap_workers`, `stop`, `halt`
> - Celery prefork (`celery/concurrency/prefork.py`) and billiard (`billiard/pool.py`: `Pool._join_exited_workers`, `maintain_pool`, `WorkerLostError`, `maxtasksperchild`)
> - Ryan Tomayko, "I like Unicorn because it's Unix" (and its Python port, research §00)

---

## 1. The story (plain English)

Many companies run background work with **Celery**. One boss process starts several worker processes, and each worker picks up jobs and runs them. Back Market ran Celery inside Kubernetes containers with a memory limit. When the workers together used more memory than the limit, the Linux kernel stepped in and killed one, instantly and without warning. That's the **OOM killer** ("out of memory"). Kubernetes didn't even report an "OOM" event. The only trace was a vague "worker lost" message. Wikimedia saw each forked child grow to about 310 MB. The fixes people reached for were **recycling** (restart a worker after N jobs or after it grows past M megabytes) and better **detection**.

To understand any of that, you need to know how a boss process creates workers, how it learns that one died (and *how* it died), and how it replaces it without losing work. That's what gunicorn's "arbiter" and Celery's pool do. It's surprisingly little code, but every line touches the operating system: `fork`, signals, sockets, `waitpid`.

In this milestone we build Kiln's master and workers from scratch. The master keeps a queue, hands jobs to workers over private sockets, notices every way a worker can die (including a real kernel OOM kill inside a memory-limited **cgroup**), and replaces workers. It can reload all workers under load without dropping a single job. Then we read gunicorn's arbiter and compare.

## 2. What you will learn

- **Explain** what `os.fork()` does, and **predict** what the child inherits (memory, fds, signal handlers, threads → no!). *(Processes)*
- **Explain** signals, why handlers must do almost nothing, and the **self-pipe / wakeup-fd** trick. *(Processes, signals)*
- **Decode** a `waitpid` status into "exited with code N" or "killed by signal S", and **explain** zombies and reaping. *(Processes)*
- **Explain** why TCP needs **framing**, and **implement** an incremental length-prefix decoder. *(Networking basics)*
- **Explain** non-blocking sockets and how a `selectors` event loop serves many connections in one thread. *(Concurrency, a precursor to asyncio)*
- **Design** a job **state machine**, and **test** it with property-based testing. *(Testing)*
- **Explain** RSS, and why it's only a rough guide. *(Memory, a precursor to K3)*
- **Reproduce** a kernel OOM kill in a cgroup and **tell it apart** from other deaths. *(Processes, memory)*
- **Explain** why `fork()` + threads is dangerous. *(Concurrency)*
- **Explain** at-most-once vs at-least-once delivery. *(Systems design)*

## 3. Entry criteria

- K0 ✅. `kiln doctor --milestone K1` exits 0.
- Python notes from K0 exist, especially functions, classes, exceptions, context managers, decorators and bytes-vs-str.

## 4. Scope

### In scope
- Every module marked **K1** in [spec/01 §5](../spec/01-architecture.md#5-package-layout-module-map), including `errors.py` and `logsetup.py` ([spec/06](../spec/06-observability.md)), plus `src/kiln/client.py` (see §5.10).
- The full `Config` schema of [spec/05](../spec/05-configuration-and-cli.md). *All* keys are parsed and validated now, even ones only used later, so `config.py` isn't restructured in every milestone. Keys whose feature isn't built yet are validated but have no effect. Each is marked `# NOT YET USED (Kn)` in a comment.
- KP/1 client and IPC messages ([spec/02 §2–4.2](../spec/02-protocol.md)), excluding `debug` (K6) and the forkserver messages (K2).
- The `sync` executor only.
- The fork launcher only, written as a plain function. K2 extracts the `Launcher` protocol.
- CLI commands marked K1 in [spec/05 §4](../spec/05-configuration-and-cli.md#4-the-kiln-cli).
- Lab tasks: `labs.echo`, `labs.sleep`, `labs.burn_cpu`, `labs.alloc`, `labs.crash`, `labs.raise`, `labs.hold_gil` (new, §5.9).
- Logging per [spec/06](../spec/06-observability.md), the `event=` names used by these modules.

### Out of scope (and why)
- spawn/forkserver (K2); `pss`/`uss` memory metrics (K3); other executors (K4); the gateway (K5); `debug` ops (K6).
- Persistence ([ADR-011](../decisions.md#adr-011)).
- Fairness, priorities ([spec/00 §4](../spec/00-overview.md#4-non-goals-things-kiln-deliberately-does-not-do)).

## 5. Design

The behaviour is fully specified in [spec/01](../spec/01-architecture.md) through [spec/05](../spec/05-configuration-and-cli.md). This section defines the **code-level shape** and the details those specs leave open.

### 5.1 `kiln.protocol.framing`

```python
HEADER = struct.Struct("!I")                 # 4-byte big-endian unsigned length

class FrameError(Exception): ...             # base class
class FrameTooLarge(FrameError): ...         # raised as soon as the 4 header bytes say N > max
class BadFrame(FrameError): ...              # zero length, invalid UTF-8, invalid JSON, NaN/Infinity, non-object

def encode_frame(obj: dict[str, object], max_bytes: int) -> bytes: ...
    # json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    # raises FrameTooLarge if the payload > max_bytes (the sender checks too, so we never send what the peer rejects)

class FrameDecoder:
    def __init__(self, max_bytes: int) -> None: ...
    def feed(self, data: bytes) -> list[dict[str, object]]: ...   # returns 0..n complete payloads, keeps the leftovers
    @property
    def buffered(self) -> int: ...                                 # bytes waiting (for tests and logging)
```

- JSON decoding uses `json.loads(text, parse_constant=_reject)`. `_reject` raises `BadFrame` for `NaN`, `Infinity` and `-Infinity`.
- The internal buffer is a `bytearray`. Consumed bytes are deleted with `del buf[:n]`. The tour must explain the cost of this, and why it's fine at our frame sizes.

### 5.2 `kiln.protocol.messages`

- `class MsgType(StrEnum)`: every `type` value in spec/02 §3–4 (K1 subset plus the later ones as constants).
- `ErrorCode(StrEnum)` (the full list in spec/02 §3.3) is defined in **`kiln.errors`**, together with `KilnError`, per spec/01 §5 and code-style. `messages` imports and re-exports it (`protocol` may import `errors`).
- Builders: `ok(req_id: int, **fields) -> dict`, `error(req_id: int | None, code: ErrorCode, message: str, **extra) -> dict`.
- Validators turn an untrusted dict into a typed request or raise `BadRequest(field: str, reason: str)`:
  - `parse_client_request(msg) -> ClientRequest`, where `ClientRequest` is a union of frozen dataclasses: `Ping`, `Submit`, `Status`, `Wait`, `Cancel`, `Stats`, `Debug`.
  - `parse_worker_message(msg) -> WorkerMessage`: `Ready`, `Heartbeat`, `Result`, `Retiring`, `BootFailed`.
  - `parse_master_message(msg)` (worker side): `Run`, `Shutdown`.
- Validation rules come from spec/02 and spec/03 §2.1: types, `max_attempts ≥ 1`, `timeout_s > 0`, `wait.timeout_s ≤ 300`, `req_id` an int. `bool` must not be accepted where an int is expected. The Python note explains the gotcha that `isinstance(True, int)` is `True`.

### 5.3 `kiln.config`

- One frozen, slotted dataclass per section of spec/05 §2, plus `Config`.
- `load_config(path: Path | None, env: Mapping[str, str], overrides: Mapping[str, object]) -> Config`, merged in the order of spec/05 §1.
- `ConfigError(problems: list[str])`. Its `__str__` lists every problem, one per line, each with a fix hint ([documentation §4.6](../standards/documentation.md#46-error-messages-and-logs-teach-too)).
- `Config.to_json() -> str` and `Config.from_json(s) -> Config` (used by spawn in K2; unit-tested now).

### 5.4 `kiln.app`

```python
@dataclass(frozen=True, slots=True)
class TaskSpec:
    name: str; func: Callable[..., object]; timeout_s: float | None; max_attempts: int | None
    retry_on: frozenset[str] | None; is_async: bool        # None means "use the config default"

class App:
    def __init__(self, name: str) -> None: ...
    def task(self, func=None, /, *, name=None, timeout_s=None, max_attempts=None, retry_on=None): ...
        # works both as @app.task and @app.task(...): the Python note on decorators explains both forms
    def get(self, name: str) -> TaskSpec: ...              # KeyError → the worker reports error kind unknown_task
    def manifest(self, jobs_cfg: JobsConfig) -> dict: ...  # fills config defaults; shape per spec/02 §4.1
    def tasks(self) -> Mapping[str, TaskSpec]: ...

class AppLoadError(Exception): ...
def load_app(spec: str, pythonpath: Sequence[str] = ()) -> App: ...
    # "pkg.mod:attr" → importlib.import_module + getattr; it must be an App. Errors say what's wrong and how to fix it
```

`kiln.labs.tasks.register(app: App) -> None` registers the `labs.*` tasks. The **worker** calls it when `labs.enabled`; the master never runs task code.

### 5.5 `kiln.master.jobs`

- `JobState(StrEnum)`: `QUEUED, RUNNING, SUCCEEDED, FAILED, TIMED_OUT, LOST, CANCELLED`, plus `TERMINAL: frozenset`.
- `Job`, `Attempt`, `ErrorInfo` as in spec/03 §3. `Attempt` has `n, worker_id, pid, started_mono, ended_mono, outcome, duration_ms`.
- `_ALLOWED: dict[tuple[JobState, JobState], str]` is a literal copy of the spec/03 §4.1 table. `_transition(job, to, why)` asserts `(job.state, to)` is allowed, *then* mutates. This is the single place a state changes.
- `class JobTable` (pure logic: **no sockets, no clock calls**. Time is passed in as `now`, so tests are deterministic):

| Method | Effect |
|---|---|
| `submit(task, args, kwargs, timeout_s, max_attempts, retry_on, now_mono, now_wall_ms) -> Job` | raises `QueueFull` per spec/03 §3.1 |
| `pop_next() -> Job \| None` | the next QUEUED job (FIFO). A dispatcher that can't place it calls `push_front(job)` |
| `start(job, worker_id, pid, now) -> Attempt` | QUEUED → RUNNING |
| `succeed(job_id, attempt_n, value, duration_ms, now) -> Job \| None` | returns None for a stale or unknown result ([spec/03 §4.1 "late result"](../spec/03-jobs-and-tasks.md#41-transition-table-every-allowed-transition-anything-else-is-a-bug-and-raises-assertionerror)) |
| `fail_attempt(job_id, attempt_n, kind, error, now) -> Job \| None` | requeues or makes terminal, per `retry_on` and the attempt budget |
| `cancel(job_id, now) -> Job` | raises `NotCancellable` / `NotFound` |
| `cancel_all_queued(message, now) -> list[Job]` | used by shutdown |
| `fail_unknown_task(job, now)` | used at dispatch after a reload |
| `evict_expired(now) -> int`, `evict_for_capacity() -> int` | |
| `view(job) -> dict` | a JobView exactly per spec/02 §3.5 |
| `stats() -> dict` | counters per state and the queue depth |
| `on_terminal: Callable[[Job], None]` | set by the arbiter to wake waiters |

### 5.6 `kiln.master` (the other modules)

| Module | Contents |
|---|---|
| `timers.py` | `TimerHeap`: `schedule(deadline_mono, kind: str, key: object) -> int`, `cancel(timer_id)` (lazy deletion: a cancelled id is skipped when popped), `pop_expired(now) -> list[Timer]`, `next_deadline() -> float \| None`. Kinds: `job_timeout`, `wait_timeout`, `boot_deadline`, `retire_deadline`, `heartbeat_check`, `rss_check`, `respawn`, `evict`, `reap_poll`, `shutdown_deadline` |
| `connections.py` | `Connection(sock, role: "client"\|"worker", max_frame)`: `read() -> list[dict]` (raises `PeerClosed`, `FrameError`), `send(obj)` (encode + append to the out buffer), `flush() -> bool` (True when empty), `wants_write`. Constants (not config, documented with WHY): `MAX_CLIENT_OUTBUF = 8 MiB`. A client whose unsent output exceeds this is a slow reader and gets disconnected, so one stuck client can't eat the master's memory. `ACCEPT_BATCH = 64`, the most `accept()`s per loop pass. `MAX_CLIENTS = 1024`: beyond this, new connections are accepted and closed immediately, with a WARNING log |
| `signals.py` | `SignalPipe`: creates the socketpair, `set_wakeup_fd(w, warn_on_full_buffer=False)`, installs handlers that append to `pending: list[int]`, `drain() -> list[signal.Signals]`, `restore()`. Mapping table per spec/04 §3 |
| `workers.py` | `WorkerState(StrEnum)`, `WorkerHandle` per spec/04 §2, `WorkerTable` (`by_id`, `by_pid`, `by_fd`, `lowest_free_id()`, `ready_with_capacity()`, `count_active()`) |
| `dispatcher.py` | `dispatch(table, workers, send) -> int`. While the queue is non-empty and a READY worker has a free slot, pick the worker with the most free slots (ties → lowest id, spec/03 §6), `start()` the job, schedule its timeout, send `run`. If the job's task isn't in the current manifest → `fail_unknown_task` |
| `supervisor.py` | `Supervisor`: `launch(worker_id)`, `on_ready`, `on_exit(pid, status)` → `classify_exit` → resolve in-flight jobs → maybe respawn; `check_heartbeats(now)`, `check_rss(now)`, `retire(handle, reason)`, `kill(handle, sig, reason)` (records `kill_sent`), `reload()`, `scale(delta)`, `begin_shutdown(mode)`. Crash-loop backoff and limit per spec/04 §5.1. OOM evidence per spec/04 §4.2 (`read_oom_kill_count() -> int \| None`) |
| `arbiter.py` | `Arbiter(config: Config)`: `run() -> int` (the exit code). It owns the selector, wires the components together, and runs the loop exactly as in [spec/01 §4](../spec/01-architecture.md#4-the-masters-event-loop-the-heart-of-kiln). Startup order: logging → pidfile → `SignalPipe` → preload the app (if `app.preload`) → launch workers → loop. The listener is created on the first `ready` ([ADR-013](../decisions.md#adr-013)) |

**Assertion at every fork:** before calling `os.fork()`, the arbiter asserts `threading.active_count() == 1` ([ADR-006](../decisions.md#adr-006)). A failure here is a bug, and the message points to `docs/concepts/fork-and-threads.md`.

**Preload in K1:** with `app.preload = true` the master calls `load_app()` before forking, so children inherit the imported module. The master never calls `register(app)` for labs and never runs a task. Note the master *does* execute the app module's top-level code, which is exactly what gunicorn `--preload` does.

### 5.7 `kiln.launch.fork` (K1 form)

```python
def fork_worker(*, worker_id: int, generation: int, config: Config, app: App | None,
                close_in_child: Sequence[int]) -> tuple[int, socket.socket]:
    """Return (pid, master_end_of_channel)."""
```

Steps, each with a `# CONCEPT(fork)` / `# GOTCHA:` comment:
1. `parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)`.
2. `sys.stdout.flush(); sys.stderr.flush()`, plus flushing every logging handler. **GOTCHA:** unflushed buffers get copied into the child and printed twice.
3. `pid = os.fork()`.
4. **Child** (`pid == 0`): close `parent_sock` and every fd in `close_in_child` (the listener, client connections, other workers' channels, both ends of the signal pipe). Then call `kiln.worker.main.run_worker(...)` inside `try/except BaseException`. Then finish with `kiln.worker.main.finalize_and_exit(code)` (§5.8). **The child never returns into the master's code.**
5. **Parent:** close `child_sock`, set `parent_sock` non-blocking, return.

### 5.8 `kiln.worker`

**`main.py`**

```python
def run_worker(*, channel: socket.socket, worker_id: int, generation: int,
               config: Config, app: App | None) -> int: ...       # returns the exit code
def finalize_and_exit(code: int) -> NoReturn: ...
```

`run_worker` sequence:
1. Signal setup exactly as in [spec/04 §3.1](../spec/04-supervisor.md#31-signals-in-workers), including `signal.set_wakeup_fd(-1)`.
2. Logging re-initialised with `role=worker worker_id=N`.
3. Best-effort `/proc/self/oom_score_adj = 500` (raising it needs no privilege). This makes the kernel prefer killing a worker over the master if the cgroup runs out of memory. A failure is logged at DEBUG. *WHY:* the supervisor must survive to report the death.
4. If `app is None`: `load_app()`. On failure, send `boot_failed` and return **3**.
5. If `labs.enabled`: `kiln.labs.tasks.register(app)`.
6. Build the executor (`SyncExecutor`, capacity 1).
7. Start the heartbeat thread.
8. Send `ready`.
9. Loop: blocking `recv` on the channel → `FrameDecoder` → handle `run` and `shutdown`. After each job, run the recycling checks (spec/04 §5.5). When draining and nothing is in flight, return 0. If the channel hits EOF (the master died), log a WARNING and return 0. Workers don't outlive their master.

**`finalize_and_exit(code)`** (per [ADR-028](../decisions.md#adr-028)): a forked child must never unwind back through the master's stack, because `finally` blocks there would remove the pidfile and close master sockets, and it must never run the master's inherited `atexit` handlers. So forked children always end with `os._exit`:
1. `logging.shutdown()`, so the worker's own log lines are flushed;
2. `sys.stdout.flush()` and `sys.stderr.flush()`;
3. `os._exit(code)`.

It does **not** run `atexit` handlers and does **not** do a final `gc.collect()`. The tour must explain what normal interpreter shutdown *would* have done (atexit LIFO, a final collection, module teardown), and why skipping it is right here. It must also point out that this already gives forked workers the benefit of Instagram's `atexit.register(os._exit, 0)` trick. The fork launcher wraps the whole child body in `try/finally: finalize_and_exit(code)`. Spawned workers (K2) instead return normally from `python -m kiln.worker` and get the real shutdown.

**`heartbeat.py`:** `HeartbeatThread(channel_writer, interval_s, stats_source)` is a `threading.Thread(daemon=True)` that sends `heartbeat` every interval. It reads RSS via `kiln.memory.procfs.read_status("self")`. All channel writes (main thread and heartbeat) go through a `ChannelWriter` holding a `threading.Lock`, so two frames are never interleaved byte-wise. That's a race the tour must point out.

**`executors/base.py`:** `class Executor(Protocol)` with `capacity: int`, `submit(run: Run, on_done: Callable[[ResultMsg], None]) -> None`, `inflight() -> int`, `shutdown(wait: bool) -> None`. **`executors/sync.py`:** runs the task inline. It converts exceptions to `error{type, message, traceback}`, and catches non-JSON-serializable results (the `serialization` kind is decided by the *master* when the worker reports `error.type == "SerializationError"`). Calling an `async def` task under the sync executor uses `asyncio.run(coro)` per job (documented; K4 does better).

### 5.9 `kiln.labs.tasks` (K1 set) and `kiln.memory.procfs` (K1 part)

- Lab tasks per [spec/03 §7](../spec/03-jobs-and-tasks.md#7-built-in-lab-tasks-kilnlabstasks), plus one **new task** for lab (d) and the spec/09 §6 "hung worker" fault:
  - `labs.hold_gil(seconds: float, method: "pydll" | "regex" = "pydll")` blocks the whole process's Python threads for about `seconds`, by running C code **while holding the GIL**.
    - **`pydll` (primary):** `ctypes.PyDLL(None).sleep(int(seconds))`. Normal `ctypes.CDLL` calls *release* the GIL around the C call. `PyDLL` is the variant that deliberately *keeps* it, so libc's `sleep()` runs with the GIL held, and no other thread in that process can run, including the heartbeat thread.
    - **`regex` (fallback):** `re.match(r"(a+)+$", "a" * n + "b")`, which is *catastrophic backtracking*: the time doubles with each extra `a`. CPython's regex engine (`_sre`) runs the match in C without releasing the GIL. `n` is calibrated by the gate probe.
  - It is marked `# LAB:`. This milestone adds it to spec/03 §7.
- `procfs.read_status(pid: int | Literal["self"]) -> StatusInfo(vm_rss, vm_hwm, rss_anon, rss_file, rss_shmem)` in bytes, parsed from `/proc/<pid>/status` (values in kB × 1024). K3 adds `read_smaps_rollup`.

### 5.10 `kiln.client` (new module; spec/01 gets this added)

A **blocking** KP/1 client for the CLI: `KilnClient(host, port, timeout_s)` with `ping()`, `submit(...)`, `status(id)`, `wait(id, timeout_s)`, `cancel(id)`, `stats()`, each returning dicts or raising `KilnError(code, message)`. `connect(retry_for_s=10)` uses exponential backoff, because the listener only opens after the first READY (ADR-013). `--trace` prints each frame as hex and JSON to stderr ([ADR-008](../decisions.md#adr-008)).

### 5.11 `kiln.bench.load` (load generator)

- `kiln bench load --jobs N (--concurrency C | --rate R) [--task labs.echo] [--args JSON] [--kwargs JSON] [--reload-every S] [--pidfile F] [--out DIR]`.
- **Closed loop** (`--concurrency C`): C connections, each waiting for its job before sending the next. **Open loop** (`--rate R`): submits on a fixed schedule of R jobs/s, regardless of replies, and measures latency from the *scheduled* send time. That avoids coordinated omission ([spec/10 §6](../spec/10-measurement-methodology.md#6-latency-measurements)). The record states the mode.
- It uses **asyncio** (`asyncio.open_connection`). The master doesn't use asyncio, but the *client* may. The tour notes this is the learner's first look at asyncio, explained properly in K4/K5.
- On `QUEUE_FULL` it sleeps `retry_after_s` and retries (counted). Throughput excludes ramp-up and drain, per [spec/10 §8](../spec/10-measurement-methodology.md#8-throughput).
- `--reload-every S` sends `SIGHUP` to the pid read from `--pidfile`, every S seconds.
- It writes an ExperimentRecord with: jobs submitted, the count per terminal state, error codes, retries, wall duration, throughput (jobs/s), latency (submit→terminal) p50/p90/p99/max, reload timestamps, and the master's CPU seconds (from `/proc/<pid>/stat` utime+stime before and after).

### 5.12 Ctrl-C behaviour (explicit)

In a terminal, Ctrl-C sends SIGINT to the master *and* the workers (same process group). Workers ignore it (ADR-024). The master starts a graceful shutdown on the first, and a fast shutdown on the second. An integration test reproduces this with `os.killpg(master_pgid, SIGINT)`. The master is started with `start_new_session=True` in tests, so its group is separate from pytest's.

## 6. Steps

Test files go into the layers of [spec/09 §2](../spec/09-testing-strategy.md#2-the-test-pyramid): files named `*_e2e.py` and launcher tests go in `tests/integration/`, lab smoke tests in `tests/lab/`, and everything else in `tests/unit/`. The waiting, port, cleanup and fd rules of spec/09 §4 apply to every integration test.

| # | Goal | Docs first | Tests | Code | Done when |
|---|---|---|---|---|---|
| 1.1 | **Framing** | concepts `framing`, `sockets-and-socketpairs` (part 1: bytes on a stream); python `struct`, `json`, `bytearray-and-memoryview` | `test_framing.py`: the 4 behaviours of spec/02 §2.2, plus Hypothesis "split at every offset" and "random concat of k frames" | `protocol/framing.py` | AC-1.1 |
| 1.2 | **Errors + messages** | python `enum` (add StrEnum), `slots-and-frozen-dataclasses`, `typing-protocol` (union types intro) | `test_messages.py`: table-driven valid/invalid inputs per message type, including the bool-vs-int gotcha; the Hypothesis "never raises anything but BadRequest" property (spec/09 §5) | `errors.py`, `protocol/messages.py` | all spec/02 §3–4.2 messages parse and build |
| 1.3 | **Config + logging** | python `dataclasses` (extend), `tomllib`, `os-module` (env part), `logging-basics` (extend: structured `key=value` events) | `test_config.py`: precedence (default < file < env < CLI) incl. a Hypothesis merge property, unknown key → error, type errors collected (not first-only), JSON round-trip; `test_logsetup.py`: the event format of spec/06 | `config.py`, `logsetup.py` | AC-1.10 |
| 1.4 | **App & task registry** | python `decorators` (extend: optional-argument decorator), `importlib`, `inspect` (coroutine detection) | `test_app.py`: both decorator forms, duplicate names, the name regex, the manifest with defaults, load_app errors | `app.py`, `labs/tasks.py` | `@app.task` works both ways |
| 1.5 | **JobTable state machine** | concepts `state-machines`; python `collections-deque`, `heapq`, `hypothesis-basics`, `uuid`, `time-monotonic` | `test_jobs.py` (every row of spec/03 §4.1) + `test_jobtable_state_machine.py` (**Hypothesis `RuleBasedStateMachine`**, §6.1 and spec/09 §5.1) | `master/jobs.py`, `master/timers.py` | AC-1.2 |
| 1.6 | **Fork launcher + worker + sync executor + heartbeat** | concepts `fork`, `fork-and-threads`, `what-is-a-process` (extend); python `os-module` (fork part), `threading-basics`, `socket-module` | `test_worker_unit.py` (run_worker over a socketpair in a *thread* of the test process with the fork step skipped), `test_fork_launcher.py` (integration: fork one worker, get `ready`, run `labs.echo`, the fd hygiene check of spec/04 §6 rule 1, random-differs check) | `launch/fork.py`, `worker/*`, `memory/procfs.py` (status) | a forked worker answers a job |
| 1.7 | **Arbiter loop + connections + signals** | concepts `non-blocking-io-and-selectors`, `event-loops`, `signals`; python `selectors-module`, `signal-module` | `test_connections.py` (partial reads/writes with a socketpair and tiny buffers), `test_arbiter_e2e.py` (start `kiln serve` as a subprocess; submit/wait; pipelining with out-of-order replies; `NOT_FOUND`; `QUEUE_FULL` with `queue.max_size=3` and `labs.sleep`) | `master/arbiter.py`, `connections.py`, `signals.py`, `dispatcher.py`, `workers.py` | AC-1.3 |
| 1.8 | **Supervisor: reap, classify, respawn** | concepts `exit-status`, `zombies-and-reaping`, `cgroups-and-oom-killer` | `test_classify_exit.py` (pure function, every spec/04 §4.1 row, using `os.waitstatus_to_exitcode`-compatible synthetic statuses), `test_deaths_e2e.py` (each `labs.crash` mode, boot failure → exit 4 after 5, boot timeout) | `master/supervisor.py` | AC-1.4, AC-1.5 |
| 1.9 | **Reload, scale, recycling, timeouts, heartbeats, shutdown** | concepts `memory-metrics` (RSS basics) | `test_reload_e2e.py` (zero-drop under a small load), `test_scale_e2e.py` (TTIN/TTOU), `test_recycle_e2e.py` (max_jobs with jitter, max_rss with `labs.alloc hold=true`), `test_timeouts_e2e.py` (job timeout → TIMED_OUT + respawn; retry path with `max_attempts=2`), `test_heartbeat_e2e.py`, `test_shutdown_e2e.py` (graceful cancels the queue, Ctrl-C ×1 and ×2, SIGQUIT) | supervisor + arbiter | AC-1.6–AC-1.9 |
| 1.10 | **CLI + client + load generator + lab** | concepts `sockets-and-socketpairs` (part 2: TCP, localhost, ports); python `argparse` (extend: subcommands) | `test_cli_e2e.py` (serve + submit --wait + stats + reload + stop via the pidfile) | `cli.py` (K1 commands), `client.py`, `bench/load.py` | AC-1.11–AC-1.14, lab report complete |

### 6.1 The stateful test (step 1.5) in detail

A Hypothesis `RuleBasedStateMachine` drives a real `JobTable` and a **model** (plain dicts and lists) side by side:

- **Rules:** `submit`, `dispatch_one` (to a fake worker id), `result_ok`, `result_err`, `timeout`, `worker_lost`, `cancel`, `stale_result` (an old attempt number), `advance_time` + `evict_expired`.
- **Invariants after every rule:**
  - (1) a terminal job never changes state again;
  - (2) `queue` contains exactly the ids of QUEUED jobs, in FIFO order (retries at the back);
  - (3) `len(attempts) ≤ max_attempts`;
  - (4) every RUNNING job has exactly one open attempt;
  - (5) the `stats()` counts equal the counts recomputed from `jobs`;
  - (6) a stale result never changes anything.

The test docstring explains what property-based testing is, and why it finds bugs that hand-written examples miss.

## 7. Lab design

Lab report: `docs/labs/K1-supervisor.md`. Four experiments, all run on 3.15 (plus the 3.15t contrast in (d)). Lab ids for `dev.sh lab`: `k1-deaths`, `k1-oom` (wraps spec/07 §6.1/§6.2), `k1-reload`, `k1-ceiling`, `k1-heartbeat`. Scripts live in `labs/k1/` (`oom_demo.py`, `reload_under_load.py`, `ceiling.py`, `gil_hold_probe.py`, `heartbeat_demo.py`). Each writes ExperimentRecords, with `experiment` = the lab id without the `k1-` prefix.

### (a) Death classification and real OOM detection
- **Question:** can Kiln tell *how* a worker died, and recognise a real kernel OOM kill?
- **Hypothesis:**
  - `exit1` → `crashed(exit_code=1)`
  - `segv` → `killed(SIGSEGV)`
  - `abort` → `killed(SIGABRT)`
  - `sigkill_self` → `killed(SIGKILL, suspected_oom=true)` with **unconfirmed** evidence, because no `oom_kill` increment happened. That's the key lesson: *suspected ≠ confirmed*.
  - A real OOM in a 512 MiB cgroup → `killed(SIGKILL, suspected_oom=true, oom_confirmed=true)`. The job becomes `LOST(worker_lost)`, and the master survives (thanks to `oom_score_adj`).
- **Procedure:** (`./scripts/dev.sh lab k1-deaths` and `./scripts/dev.sh lab k1-oom` automate the following)
  - (1) `kiln serve examples.demo:app --labs --workers 2`, then `kiln submit labs.crash --kwargs '{"how":"exit1"}' --wait`, and repeat for each `how`.
  - (2) OOM: `systemd-run --user --scope -p MemoryMax=512M -p MemorySwapMax=0 ./scripts/dev.sh serve-demo --labs --workers 2`, then `kiln submit labs.alloc --kwargs '{"mb":700,"hold":true}' --wait`. Fallback when there's no systemd user manager or no delegated memory controller (gate G1.4): a manual cgroup v2 setup with sudo. Create the group with `sudo mkdir /sys/fs/cgroup/kiln-lab`, write `512M` to `memory.max` and `0` to `memory.swap.max`, and move the shell into it by writing `$$` to `cgroup.procs`. The commands go in the lab report.
- **Measured:** the log line, JobView.error, the `memory.events` before/after, and master survival.
- **Result table shape:** `how | expected classification | observed log fields | job state | ✅/❌`.

### (b) Zero-drop reload under load (SC-2)
- **Question:** do any jobs get lost when all workers are replaced mid-load?
- **Hypothesis:** zero. The queue lives in the master, and retiring workers finish their in-flight jobs ([ADR-007](../decisions.md#adr-007)). Latency p99 rises during reload windows (fewer slots plus new workers booting). Total RSS briefly approaches 2× during each reload.
- **Variables:** reload frequency (none vs `--reload-every 2`). **Controls:** `workers=4`, `labs.sleep ms=5`, 10,000 jobs, concurrency 64.
- **Procedure:** `kiln bench load --jobs 10000 --concurrency 64 --task labs.sleep --kwargs '{"ms":5}' --reload-every 2 --pidfile /tmp/kiln.pid`. Repeats per [spec/10](../spec/10-measurement-methodology.md).
- **Failure-mode demo:** `graceful_timeout_s=1` with `labs.sleep ms=3000` shows forced kills → `LOST` jobs. This is the *negative* control, showing the measurement can detect drops.
- **Result table shape:** `condition | jobs | SUCCEEDED | LOST | CANCELLED | other | throughput (jobs/s) | p50 | p99 | peak workers | peak Σ RSS`.

### (c) Master throughput ceiling
- **Question:** how many trivial jobs per second can one single-threaded master dispatch, and where is the bottleneck?
- **Hypothesis:** throughput rises with workers until master CPU reaches about 100% of one core, then plateaus. The bottleneck is JSON encode/decode plus syscalls per job ([ADR-007](../decisions.md#adr-007) consequence).
- **Variables:** `workers ∈ {1, 2, 4, 8, 12}` × `concurrency ∈ {16, 64, 256}`. **Task:** `labs.echo`. **Measured:** jobs/s, master CPU% (Δ(utime+stime)/Δwall), worker CPU%.
- **Result table shape:** `workers | concurrency | jobs/s (median, 95% CI) | master CPU% | mean worker CPU%`.

### (d) Heartbeats: a Python CPU loop vs. a GIL-holding C call
- **Question:** does a busy worker stop sending heartbeats?
- **Hypothesis:**
  - `labs.burn_cpu ms=15000` (pure Python): heartbeats continue, because the interpreter forces thread switches every `sys.getswitchinterval()` (5 ms). No kill.
  - `labs.hold_gil seconds=20`: heartbeats stop for the duration, the master kills the worker with `reason=heartbeat_timeout` at about 10 s, and the job is `LOST`.
  - On **3.15t** (no GIL), heartbeats continue even during `hold_gil`. The hang detector has nothing to detect, which foreshadows K4.
- **Measured:** the heartbeat timestamps seen by the master (from DEBUG logs), the kill time, and the job state.
- **Result table shape:** `interpreter | task | max heartbeat gap (s) | killed? | reason | job state`.

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check (command) | If false, fallback |
|---|---|---|---|
| G1.1 | `selectors.DefaultSelector` is `EpollSelector` on WSL | `python -c "import selectors; print(selectors.DefaultSelector)"` | fine either way; record it and adjust the primer |
| G1.2 | `signal.set_wakeup_fd` accepts a non-blocking socket fd, and writes the signal number byte | a snippet in `concepts/snippets/signals_wakeup.py` | use `os.pipe()` (non-blocking) instead of a socketpair |
| G1.3 | `random` produces different values in forked children (auto-reseed via `os.register_at_fork`) | `test_fork_launcher.py::test_random_differs` | reseed explicitly in `run_worker` (`random.seed()`) and document it |
| G1.4 | a systemd user manager with a delegated memory controller is available (`systemd-run --user --scope -p MemoryMax=…` works) | `systemd-run --user --scope -p MemoryMax=64M true; echo $?` and `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers` | the manual cgroup fallback in §7(a). If cgroup v2 memory isn't available at all, SC-3 is demonstrated as "suspected only", with an errata entry |
| G1.5 | `/proc/self/cgroup` has a single `0::/path` line (pure cgroup v2), and `memory.events` has `oom_kill` | `cat /proc/self/cgroup; cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.events` | report `oom_evidence="unavailable"` (spec/04 §4.2) |
| G1.6 | `ctypes.PyDLL(None).sleep(3)` holds the GIL: a Python thread incrementing a counter every 10 ms makes **no** progress during the call on 3.15 (GIL build), but does progress with `ctypes.CDLL(None).sleep(3)` (the control) | `labs/k1/gil_hold_probe.py`: prints the counter progress for `pydll`, `cdll` and `regex` (n = 20..34) | use `regex` (calibrated n). If neither holds the GIL: (1) huge-int arithmetic `pow(3, 10**8)`; (2) defer to K4's C extension and mark lab (d) "GIL-holding part completed in K4". Record which method was used |
| G1.7 | `os._exit` in a forked child skips the inherited atexit handlers and leaves the parent's pidfile untouched | `test_fork_launcher.py::test_child_does_not_run_parent_atexit` (the parent registers a handler that writes a marker file; the child exits; no marker) | none needed. If it fails, it's a bug in `finalize_and_exit` |
| G1.8 | Forking while another thread runs emits `DeprecationWarning` (3.12+) | snippet `concepts/snippets/fork_with_threads.py` | adjust the primer text |
| G1.9 | WSL's kernel OOM killer kills inside a cgroup without freezing WSL | run (a) with 512M first, then watch `dmesg` (sudo) | lower to 256M; document it |
| G1.10 | `os.waitstatus_to_exitcode` gives negative numbers for signals | REPL | decode with `WIFSIGNALED`/`WTERMSIG` only (that's the plan anyway) |

## 9. Acceptance criteria

- **AC-1.1** `FrameDecoder` passes the 4 behaviours of spec/02 §2.2, including the Hypothesis split test (`test_framing.py`).
- **AC-1.2** The `JobTable` stateful test runs ≥ 500 examples with all 6 invariants holding. Every row of spec/03 §4.1 has an example test.
- **AC-1.3** End to end: `submit` → `wait` returns SUCCEEDED with the right result. Pipelined requests are answered with the right `req_id`s, even out of order. `QUEUE_FULL` and `NOT_FOUND` behave per spec/02 (`test_arbiter_e2e.py`).
- **AC-1.4** Each `labs.crash` mode is classified exactly as in spec/04 §4.1, and the job becomes `LOST` (`test_deaths_e2e.py`).
- **AC-1.5** A broken app makes the master exit with code **4** after `boot_failure_limit` failures, with exponential backoff between attempts (log timestamps checked).
- **AC-1.6** **SC-2 (small):** a test with 2,000 jobs and 3 reloads ends with every job SUCCEEDED.
- **AC-1.7** Job timeout: `labs.sleep ms=5000` with `timeout_s=1` → TIMED_OUT within 1.5 s, the worker is replaced, and with `max_attempts=2` (plus `timeout` in `retry_on`) the job is retried once.
- **AC-1.8** Recycling: with `max_jobs=10, max_jobs_jitter=5`, each worker retires after between 10 and 15 jobs. With `max_rss_mb=200`, `labs.alloc mb=300 hold=true` causes a graceful retirement, not a kill.
- **AC-1.9** Shutdown: SIGTERM cancels queued jobs (state CANCELLED, waiters woken) and exits 0. SIGINT ×2 exits 1 within 3 s. SIGQUIT exits 1 immediately. Workers ignore a group SIGINT.
- **AC-1.10** An invalid config prints *all* problems and exits 2.
- **AC-1.11** **SC-1:** `kiln bench load --jobs 10000 --concurrency 64` completes with 0 errors (published record).
- **AC-1.12** **SC-2:** the same run with `--reload-every 2` (≥ 5 reloads) has 0 `LOST`/`CANCELLED` (published record).
- **AC-1.13** **SC-3:** the OOM experiment's log shows `suspected_oom=true` *and* a confirmed `oom_kill` increment (or the documented gate-fallback outcome).
- **AC-1.14** A worker's fd list contains only stdio, its channel, and fds it opened itself (spec/04 §6 rule 1), checked by inode comparison against the master's listener, channels and signal pipe.
- **AC-1.15** All docs deliverables exist. `dev.sh check` and `dev.sh test-matrix` are green.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/fork.md` | what fork copies (memory as copy-on-write, a teaser for K3; fds; signal dispositions; *only the calling thread*); the return value 0 vs pid; `os._exit` vs `sys.exit` in the child; duplicated stdio buffers |
| Concept | `docs/concepts/fork-and-threads.md` | why locks held by other threads deadlock the child; the 3.12+ DeprecationWarning; why the master has no threads (ADR-006); how workers safely start threads *after* fork |
| Concept | `docs/concepts/signals.md` | what signals are; default actions; handlers; why Python handlers run "between bytecodes"; the wakeup-fd trick; process groups and Ctrl-C; the table of signals Kiln uses |
| Concept | `docs/concepts/exit-status.md` | exit codes vs termination signals; `WIFEXITED`/`WTERMSIG`; the shell's `128+N` convention; core dumps |
| Concept | `docs/concepts/zombies-and-reaping.md` | the zombie state; `waitpid(-1, WNOHANG)`; SIGCHLD; why the loop is "reap until nothing" |
| Concept | `docs/concepts/sockets-and-socketpairs.md` | a socket as a bidirectional fd; TCP vs Unix domain sockets; ports and `127.0.0.1`; `socketpair`; `listen`/`accept`; backlog; "connection refused" |
| Concept | `docs/concepts/framing.md` | the stream vs message problem with a byte-level diagram; length prefixes; big-endian; partial reads; the decoder walkthrough |
| Concept | `docs/concepts/non-blocking-io-and-selectors.md` | blocking vs non-blocking calls; `EAGAIN`/`BlockingIOError`; `select`/`epoll`; readiness vs completion; output buffers |
| Concept | `docs/concepts/event-loops.md` | the loop shape of spec/01 §4; timers via a heap; "never block the loop"; a preview of asyncio being the same idea |
| Concept | `docs/concepts/state-machines.md` | states, events, transitions; the transition table as code; invariants; why one `_transition()` function |
| Concept | `docs/concepts/memory-metrics.md` | virtual vs resident memory; RSS; VmHWM; why RSS over-counts shared pages (K3 teaser) |
| Concept | `docs/concepts/cgroups-and-oom-killer.md` | what a cgroup is; `memory.max`; `memory.events`; how the OOM killer chooses (`oom_score_adj`); why the only symptom is SIGKILL; Back Market's story; swap and `memory.swap.max` |
| Concept | `docs/concepts/delivery-semantics.md` | at-most-once vs at-least-once vs exactly-once; idempotency; Kiln's defaults (ADR-010) |
| Python | `docs/python/struct.md` | `struct.pack`/`unpack`, format strings, `!I`, byte order |
| Python | `docs/python/json.md` | `dumps`/`loads`, separators, `ensure_ascii`, `allow_nan`, tuples → lists, str keys |
| Python | `docs/python/bytearray-and-memoryview.md` | mutable bytes, slicing costs, `del buf[:n]` |
| Python | `docs/python/slots-and-frozen-dataclasses.md` | `slots=True`, `frozen=True`, why both are used for records and config |
| Python | `docs/python/collections-deque.md` | O(1) appends and pops at both ends; FIFO queues |
| Python | `docs/python/heapq.md` | min-heaps, `(deadline, id)` tuples, lazy deletion |
| Python | `docs/python/typing-protocol.md` | structural typing, `Protocol`, how `Executor` uses it; `Callable` |
| Python | `docs/python/threading-basics.md` | `Thread`, `daemon`, `Lock`, `Event`; the GIL in one paragraph (K4 goes deep) |
| Python | `docs/python/signal-module.md` | `signal.signal`, `SIG_IGN`, `set_wakeup_fd`, `Signals` enum |
| Python | `docs/python/os-module.md` | `fork`, `_exit`, `waitpid`, `kill`, `getpid`, `environ`, status macros |
| Python | `docs/python/socket-module.md` | `socket`, `socketpair`, `setblocking`, `recv`/`send` semantics, `SO_REUSEADDR` |
| Python | `docs/python/selectors-module.md` | `DefaultSelector`, `register`/`modify`, `EVENT_READ`/`EVENT_WRITE`, the `data` slot |
| Python | `docs/python/uuid.md` | `uuid4().hex`, why random ids |
| Python | `docs/python/time-monotonic.md` | `time.monotonic()` vs `time.time()` vs `perf_counter()` |
| Python | `docs/python/hypothesis-basics.md` | strategies, `@given`, shrinking, `RuleBasedStateMachine` |
| Python | `docs/python/tomllib.md` | reading TOML, types it produces |
| Python | `docs/python/importlib.md` | `import_module`, how `load_app` works, `sys.path` |
| Python | `docs/python/inspect.md` | `iscoroutinefunction`, `signature` |
| Python | `docs/python/asyncio-first-look.md` | just enough to read `bench/load.py`: `async def`, `await`, `asyncio.run`, `gather` (K4/K5 go deep) |
| Tour | `docs/tours/K1.md` | ≥ 10 stops: framing → messages → jobs → timers → fork launcher → worker main → heartbeat → arbiter loop → supervisor classify → reload |
| Lab | `docs/labs/K1-supervisor.md` | experiments (a)–(d), full template |
| Spec update | `docs/spec/03-jobs-and-tasks.md` §7 | add `labs.hold_gil` |
| Spec update | `docs/spec/01-architecture.md` §5 | add `client.py` |

## 11. Reference comparison assignment

The lab report's "Reference comparison" must answer:
1. **gunicorn `Arbiter.sleep`/`wakeup`** use a `PIPE` + `select`, and signals are queued in `SIG_QUEUE`. How is that the same as our wakeup fd, and how is it different? Why does gunicorn cap `SIG_QUEUE` at 5?
2. **`reap_workers`**: how does gunicorn decode statuses, and which exit codes does it treat specially (`WORKER_BOOT_ERROR = 3`, `APP_LOAD_ERROR = 4`)? Compare with our exit codes in spec/04 §5.6.
3. **`murder_workers`**: gunicorn detects hung workers with a *temp file whose mtime the worker updates*, not a socket heartbeat. What are the trade-offs?
4. **`reload`**: how does gunicorn sequence new vs old workers? Does it guarantee zero dropped requests? Why is that harder with a shared listening socket (ADR-007)?
5. **`manage_workers` / `spawn_worker`**: how does gunicorn's child avoid returning into the arbiter's code (look for `sys.exit` in the child branch)? Compare with our `finalize_and_exit`.
6. **billiard**: how does Celery's pool detect `WorkerLostError`, and how does `maxtasksperchild` work? Why did Back Market only see "worker lost"?
7. **Unicorn is Unix**: which Unix ideas from the essay did we use (self-pipe, `fork`, signals as a control API)?

## 12. Risks and pitfalls

| Risk | Mitigation |
|---|---|
| A forked child accidentally returning into master code (a pidfile deleted by the child, a double-flush) | `finalize_and_exit` always ends with `os._exit`; a test asserts the pidfile survives worker deaths |
| Flaky e2e tests from timing | all waits poll with deadlines (no fixed sleeps); generous timeouts; `pytest-timeout`; tests set short `heartbeat_*` and `graceful_timeout_s` values |
| Zombie or orphan processes left by failing tests | a pytest fixture kills the master's process group in teardown (`start_new_session=True` + `os.killpg`) |
| Partial-write bugs that only appear under load | the `connections.py` tests use `SO_SNDBUF` set small, plus the load test |
| Interleaved frames from the heartbeat thread and the main thread | `ChannelWriter` lock; a test with a tiny heartbeat interval during a large result |
| The signal-handler work creeping into the handler | code review rule: handlers only append; enforced by a unit test inspecting `SignalPipe` |
| WSL freezing during the OOM lab | cgroup limit 512 MiB + `memory.swap.max=0`; gate G1.9 |
| `bool` accepted as `int` in validation | explicit tests (step 1.2) |

## 13. Checkpoint quiz (seed questions)

1. After `pid = os.fork()`, how many processes run the next line? What is `pid` in each?
2. Why does the master forbid threads, but workers may start a heartbeat thread?
3. A worker's `waitpid` status says "terminated by signal 9", and `kill_sent` is `None`. What do we log, and why only "suspected" OOM?
4. Why does a TCP `recv(4096)` sometimes return half a message? How does `FrameDecoder` cope?
5. What would go wrong if the signal handler itself called `supervisor.reload()`?
6. During a SIGHUP reload, where are queued jobs, and why aren't any lost?
7. Why does `max_jobs_jitter` exist?
8. Why does a pure-Python infinite loop *not* stop heartbeats on the GIL build, but a catastrophic regex does? What changes on 3.15t?
9. What is a zombie process? What happens if the master never calls `waitpid`?
10. Why do workers ignore SIGINT?
11. With `max_attempts=3`, could a task's side effect happen twice? When?
12. Why is RSS summed over all workers larger than the real memory used?

## 14. Stretch / later

- `kiln stats --watch` as a curses-free live table (if not done in step 1.10).
- Reading `memory.events` and `memory.current` continuously into stats.
- A `SIGUSR2` "dump all in-flight jobs" signal.

## 15. ADRs this milestone relies on (added during the spec pass as ADR-029, ADR-030 and ADR-031)

- **Workers raise their own `oom_score_adj` to 500**, so the kernel kills workers before the master. (§5.8 step 3)
- **`labs.hold_gil`**, plus the `kiln.client` module and its import-boundary row (K0 §5.8). (§5.9, §5.10)
- **Master-side connection limits as constants** (`MAX_CLIENT_OUTBUF`, `ACCEPT_BATCH`, `MAX_CLIENTS`), not config keys. (§5.6)
