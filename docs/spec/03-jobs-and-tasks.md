# 03 — Jobs and Tasks: The Data Model

> **Status:** Approved spec. **Depends on:** [02-protocol](02-protocol.md).
> **Concepts:** function, decorator, dict, enum, state machine, FIFO queue, idempotency.

---

## 1. Vocabulary (these words have exact meanings in Kiln)

| Word | Meaning | Example |
|---|---|---|
| **Task** | a *named Python function* that Kiln knows how to run. It is registered with `@app.task` | `add(a, b)` registered as `"add"` |
| **Job** | *one request to run a task* with specific arguments. A job has an id and a state | "run `add` with `[2, 3]`", id `6f1c…` |
| **Attempt** | *one try* at running a job on a worker. A job gets more than one attempt only if it is retried | attempt 1 on worker 2 died, attempt 2 on worker 0 succeeded |
| **App** | the user's object that holds the task registry | `app = kiln.App("demo")` |
| **App spec** | the string that tells Kiln where the app is: `"module.path:attribute"` | `"examples.demo:app"` |
| **Manifest** | the JSON summary of an app's tasks that a worker sends to the master | `{"tasks": {"add": {...}}}` |

## 2. The task API (what a Kiln user writes)

```python
# examples/demo.py
import kiln

app = kiln.App("demo")                 # name is used only in logs

@app.task                              # registers under the function's name: "add"
def add(a: int, b: int) -> int:
    return a + b

@app.task(name="img.resize", timeout_s=120, max_attempts=3)
def resize(path: str, width: int) -> dict:
    ...

@app.task                              # async tasks are allowed; how they run depends on the executor (K4)
async def fetch_title(url: str) -> str:
    ...
```

### 2.1 Rules

| Rule | Detail |
|---|---|
| Name | defaults to `func.__name__`. Must match `^[A-Za-z_][A-Za-z0-9_.]{0,127}$`. Registering the same name twice raises `ValueError` at import time |
| Options | `timeout_s` (float > 0, default = config `jobs.default_timeout_s` = 30), `max_attempts` (int ≥ 1, default = config `jobs.default_max_attempts` = 1), `retry_on` (set of `"worker_lost"`, `"timeout"`, `"exception"`, default `{"worker_lost", "timeout"}`) |
| Arguments | must be JSON-serializable. **Tuples arrive as lists**, dict keys are always strings, and there are no sets, bytes or datetimes. This is documented in the task API docstring |
| Return value | must be JSON-serializable. Otherwise the attempt fails with `error.kind = "serialization"` |
| The decorator returns | the **original function, unchanged**, so you can still call `add(2, 3)` directly in tests. The registry stores a `TaskSpec(name, func, timeout_s, max_attempts, retry_on, is_async)` |
| Async detection | `inspect.iscoroutinefunction(func)` |
| Submit-time overrides | a `submit` may override `timeout_s` and `max_attempts` for that one job |

## 3. The job record (master-side)

```python
@dataclass(slots=True)
class Job:
    job_id: str                 # uuid4().hex
    task: str
    args: list
    kwargs: dict
    timeout_s: float
    max_attempts: int
    retry_on: frozenset[str]
    state: JobState
    submitted_mono: float       # time.monotonic() — used for all math
    submitted_wall_ms: int      # time.time() — shown to humans only
    started_wall_ms: int | None
    finished_wall_ms: int | None
    attempts: list[Attempt]
    result: object | None       # JSON value
    error: ErrorInfo | None
    expires_mono: float | None  # set when terminal: finished + result_ttl_s
```

The `JobTable` holds:
- `jobs: dict[str, Job]`, every known job
- `queue: collections.deque[str]`, the job ids in FIFO order
- `expiry: list` (a heap of `(expires_mono, job_id)`), used to evict old terminal jobs. Eviction runs from the master timers, at most once a second.

### 3.1 Limits (all configurable; see [05-configuration](05-configuration-and-cli.md))

| Limit | Default | When hit |
|---|---|---|
| `queue.max_size` (QUEUED jobs) | 50,000 | `submit` → `QUEUE_FULL` |
| `jobs.max_retained` (all jobs in the table) | 200,000 | evict the oldest *terminal* jobs early. If every job is non-terminal, `submit` → `QUEUE_FULL` |
| `jobs.result_ttl_s` | 600 | terminal jobs are removed once the TTL passes. After that, `status` → `NOT_FOUND` |
| `protocol.max_frame_bytes` | 1 MiB | covers args and results alike |

## 4. The job state machine

```
                         submit
                           │
                           ▼
       cancel   ┌──────────────────┐
   ┌────────────│      QUEUED      │◀───────────────────────────┐
   │            └────────┬─────────┘                            │ retry allowed
   ▼                     │ dispatched to a worker slot          │ (attempts < max_attempts
┌───────────┐            ▼                                      │  AND failure kind ∈ retry_on)
│ CANCELLED │   ┌──────────────────┐   worker died / timeout /  │
└───────────┘   │     RUNNING      │───exception ───────────────┤
                └──┬──────┬─────┬──┘                            │ no retry
                   │      │     │                               ▼
        result ok  │      │     │ result error        ┌─────────────────────────────┐
                   ▼      │     ▼                     │ terminal failure state:     │
         ┌───────────┐    │  ┌────────┐               │   FAILED    (exception /    │
         │ SUCCEEDED │    │  │ FAILED │               │              serialization) │
         └───────────┘    │  └────────┘               │   TIMED_OUT (timeout)       │
                          │ timeout / worker lost     │   LOST      (worker_lost)   │
                          └──────────────────────────▶└─────────────────────────────┘
```

**Terminal states:** `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `LOST`, `CANCELLED`. Once a job is terminal it **never changes again**. That's an invariant, and a test asserts it.

### 4.1 Transition table (every allowed transition; anything else is a bug and raises `AssertionError`)

| From | Event | Condition | To | Side effects |
|---|---|---|---|---|
| — | `submit` accepted | | QUEUED | append to queue; `attempts = []` |
| QUEUED | dispatched | | RUNNING | new `Attempt(n=len+1, worker_id, pid, started_mono)`; set `started_wall_ms` (first attempt only); start the timeout timer |
| QUEUED | `cancel` | | CANCELLED | remove from queue; `error.kind = "cancelled"`; wake waiters |
| QUEUED | master graceful shutdown | | CANCELLED | `error.kind = "cancelled"`, message "master shutting down" |
| QUEUED | dispatch finds task missing from manifest (after a reload) | | FAILED | `error.kind = "unknown_task"` |
| RUNNING | `result ok:true` | | SUCCEEDED | store value; wake waiters; set expiry |
| RUNNING | `result ok:false` | `"exception"` ∈ retry_on AND attempts < max | QUEUED | append to the **back** of the queue |
| RUNNING | `result ok:false` | otherwise | FAILED | `error.kind = "exception"` |
| RUNNING | timeout timer fires | `"timeout"` ∈ retry_on AND attempts < max | QUEUED | the worker gets killed (see 04-supervisor §5.3) |
| RUNNING | timeout timer fires | otherwise | TIMED_OUT | the worker gets killed |
| RUNNING | worker death detected | `"worker_lost"` ∈ retry_on AND attempts < max | QUEUED | |
| RUNNING | worker death detected | otherwise | LOST | `error.kind = "worker_lost"`, the message includes the death classification (e.g. "killed by SIGKILL (suspected OOM)") |

**A late result.** A worker might send a `result` for an attempt the master has already given up on, for example just before the kill landed. The master compares `(job_id, attempt)` with the job's current attempt number. If they don't match, the result is **ignored** and logged at `WARNING`. That's how we guarantee the terminal-state invariant.

## 5. Delivery semantics: "how many times can my function run?"

This is a classic interview question, so here is the precise answer for Kiln ([ADR-010](../decisions.md#adr-010)).

- **Default (`max_attempts = 1`): at-most-once.** The function runs zero times (cancelled, or lost in a master crash) or once. If the worker dies mid-job, the job becomes `LOST` and Kiln does **not** run it again, because Kiln can't know whether the function had already done its side effect (sent the email, charged the card).
- **`max_attempts > 1` with `worker_lost` and/or `timeout` in `retry_on`: at-least-once** (within the attempt budget). The function **may run more than once**. Example: it sends the email, then the worker is OOM-killed before reporting, and attempt 2 sends it again. Only use this for **idempotent** tasks, meaning tasks where running twice has the same effect as running once.
- **Exactly-once** is impossible in general without cooperation from the task. Kiln doesn't pretend to offer it.

## 6. Ordering and fairness

- Dispatch is **FIFO** by queue position. A retried job goes to the **back**.
- Workers are chosen **least-loaded first**: the most free slots wins, and ties go to the lowest `worker_id`. That's deterministic, which makes tests easy.
- Jobs are *not* guaranteed to finish in order. Two workers run in parallel.

## 7. Built-in lab tasks (`kiln.labs.tasks`)

These are registered into the app only when `labs.enabled = true`, under the `labs.` prefix. They give every milestone the same controllable workloads.

| Task | Args | Behaviour | Used in |
|---|---|---|---|
| `labs.echo` | `value` | returns `value` | tests, load |
| `labs.sleep` | `ms` | `time.sleep(ms/1000)`. IO-bound stand-in | K1, K4, K5 |
| `labs.burn_cpu` | `ms` | busy loop of pure-Python arithmetic for about `ms` of CPU time | K4, K9 |
| `labs.alloc` | `mb`, `hold` (bool) | allocates `mb` MiB of `bytearray`. If `hold`, keeps it in a module-level list (simulating a leak, or pushing toward OOM) | K1 (OOM), K6 |
| `labs.crash` | `how` ∈ `exit1`, `segv`, `abort`, `sigkill_self` | dies the way requested (`os._exit(1)`, `ctypes.string_at(0)`, `os.abort()`, `os.kill(os.getpid(), 9)`) | K1 death classification |
| `labs.hold_gil` | `seconds`, `method` ∈ `pydll` (default), `regex` | runs C code **while holding the GIL** (`ctypes.PyDLL(None).sleep`, or a catastrophic-backtracking regex as fallback), so even the heartbeat thread can't run. This is the "hung worker" fault (K1 §5.9) | K1 heartbeat lab, K4 |
| `labs.random_probe` | — | returns `random.getrandbits(64)`. It shows that workers don't share random state after a fork | K2 |
| `labs.touch_preloaded` | `fraction` (0–1) | reads that fraction of the K3 preloaded object graph, which bumps refcounts | K3 |
| `labs.churn` | `n` | creates and drops `n` small cycles and short-lived dicts, which triggers *automatic* collections when the GC is enabled. Returns `gc.get_count()` before and after | K3 |
| `labs.gc_collect` | `generation` (0–2) | `gc.collect(generation)`. This runs even while the GC is disabled. Returns the number collected and the duration in ms | K3, K7 |
| `labs.gc_stats` | — | `{enabled, threshold, count, stats, frozen, pid}` from the `gc` module | K3, K7 |
| `labs.raise` | `message` | raises `RuntimeError(message)` | tests |
| `labs.leak_*` | per K6 spec | planted leaks | K6 |
| `labs.cycles` | `n` (number of "migrations"), `models=20`, `fields=10`, `payload_bytes=0` | runs `n` Django-like migrations, each building temporary model↔field reference cycles (K7 §5.4) | K7 |
| `labs.async_sleep` | `ms` | `await asyncio.sleep(ms/1000)`, the async twin of `labs.sleep` | K4 |
| `labs.block_loop` | `ms` | an `async def` that calls `time.sleep` **without** awaiting: an uncooperative task that blocks the event loop | K4, K5 |
