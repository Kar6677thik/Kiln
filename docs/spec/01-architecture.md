# 01 — Architecture: The Pieces and How They Talk

> **Status:** Approved spec. **Depends on:** [00-overview](00-overview.md).
> **Concepts you'll meet:** process, file descriptor, socket, socketpair, fork, signal, event loop. If any of these are new, the K0 primers cover them. A plain-English definition of each is in the [glossary](../glossary.md).

---

## 1. The big picture

```
                         ┌───────────────────────────── one Linux machine (WSL2) ─────────────────────────────┐
                         │                                                                                       │
  ┌──────────┐  KP/1 TCP │   ┌──────────────────────────── kiln master ("arbiter") ────────────────────────┐   │
  │ kiln CLI │──────────────▶│  single thread · selectors event loop · owns the QUEUE and RESULTS tables    │   │
  │ (client) │  :7340    │   │                                                                              │   │
  └──────────┘           │   │   ┌─────────┐   ┌───────────┐   ┌──────────────┐   ┌───────────────────┐   │   │
                         │   │   │ Listener│   │ JobTable  │   │ Dispatcher   │   │ Supervisor        │   │   │
  ┌──────────┐  HTTP     │   │   │ (accept)│   │ queue +   │   │ picks idle   │   │ spawns / reaps /  │   │   │
  │ curl /   │───┐       │   │   └─────────┘   │ results   │   │ worker slots │   │ recycles workers  │   │   │
  │ load gen │   │ :7341 │   │                 └───────────┘   └──────────────┘   └───────────────────┘   │   │
  └──────────┘   │       │   └───────┬──────────────────┬──────────────────┬───────────────────┬─────────┘   │
                 │       │           │ socketpair       │ socketpair       │ socketpair        │ KP/1 TCP    │
                 │       │           ▼                  ▼                  ▼                   │             │
                 │       │   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────┴─────────┐   │
                 └──────────▶│   worker 0   │   │   worker 1   │   │   worker N-1 │   │  gateway (K5+)   │◀──┘
                         │   │ executor:    │   │              │   │              │   │ asyncio HTTP/1.1 │
                         │   │ sync|threads │   │     ...      │   │     ...      │   │ + loop watchdog  │
                         │   │ |interp|async│   │              │   │              │   └──────────────────┘
                         │   └──────────────┘   └──────────────┘   └──────────────┘                          │
                         │      (optional K2: forkserver process sits between master and workers)               │
                         └───────────────────────────────────────────────────────────────────────────────────────┘
```

**How to read this diagram:**
- Each box is a separate operating-system **process** with its own memory.
- Arrows are **connections**, and data travels along them as bytes.
- The master is the only process that knows the whole state: which jobs exist, which worker is running which job, and which results are ready.
- Workers know only the job they are running right now.

## 2. Processes and their roles

### 2.1 Master (also called the "arbiter")

- **What it is:** the process you start with `kiln serve myapp:app`. It runs for the whole life of the server.
- **What it owns (state):**
  - the **job queue**, a FIFO of jobs waiting for a worker
  - the **job table**, which maps every known job id to its record (see [03-jobs-and-tasks](03-jobs-and-tasks.md))
  - the **worker table**: for each worker, its id, pid, generation, state, free slots, in-flight job ids, last heartbeat and channel
  - the **waiters**, clients blocked on `wait` for a job
  - the **task manifest**, the list of task names the current workers can run
- **What it does:**
  1. Accepts client connections and handles their requests.
  2. Dispatches queued jobs to free worker slots.
  3. Receives results.
  4. Supervises workers: starts them, notices when they die, replaces them, recycles them, and reloads them on `SIGHUP`.
  5. Enforces job timeouts.
  6. Reacts to signals.
- **What it must never do:** run user task code, start threads, or block. [ADR-006](../decisions.md#adr-006) explains why.
  - *Blocking* means calling something that waits, such as `time.sleep` or a socket read without a timeout. One blocked call freezes the whole server, because the master has exactly one thread.
  - *Threads* are forbidden because the master calls `fork()`. `fork()` copies only the calling thread into the child. Any lock another thread held at that moment stays locked forever in the child, and the child can deadlock. The K1 primer `docs/concepts/fork-and-threads.md` covers this.

### 2.2 Workers

- **What they are:** child processes of the master (or of the forkserver, in K2). The config value `workers` (default **4**) sets how many there are.
- **Lifecycle:** `BOOTING → READY → (RETIRING) → EXITED`. The full state machine is in [04-supervisor](04-supervisor.md).
- **What they do:**
  1. Connect to the master through their own private channel.
  2. Import the user's app, unless it was already preloaded before the fork.
  3. Report `ready` along with their **manifest** (the task names and options they know).
  4. Then loop: receive `run`, execute the task in their **executor**, and send back `result`.
  5. A small heartbeat thread sends `heartbeat` messages every `heartbeat_interval_s` (default 1.0 s), even while a long job is running.
- **Capacity:** a worker can run `capacity` jobs at once. For the `sync` executor, capacity is 1. For `threads`, `interpreters` and `asyncio`, it is `executor_concurrency` (default 8). Executors arrive in K4. K1 only has `sync`.

### 2.3 Gateway (from K5)

- **What it is:** an *optional* process (`gateway.enabled = true`) that speaks **HTTP** to the outside world and **KP/1** to the master. To the master it is just another client.
- **Why it's a separate process:** the master's job is supervision, and it must stay simple and never block. HTTP parsing and thousands of keep-alive connections are a different job, and they're a perfect fit for `asyncio`. Keeping it separate means a bug in the gateway can't freeze job dispatch ([ADR-014](../decisions.md#adr-014)).
- **How it starts:** the master supervises it and starts it with **spawn**, a fresh `python -m kiln.gateway` process. It never imports the user app.

### 2.4 Forkserver (K2, only when `start_method = "forkserver"`)

- **What it is:** a small, clean process that the master starts early. Its only job is to `fork()` new workers on request.
- **Why it exists:** forking from the master copies *all* of the master's state into the child. The forkserver is "clean" because it imported only a short, chosen list of modules, so its children start from a known state.
- **The tricky part:** workers forked by the forkserver are the forkserver's children, not the master's. The master therefore **can't** call `waitpid()` on them to learn how they died. The forkserver reaps them itself and reports each exit status back to the master. The details are in [04-supervisor §6](04-supervisor.md#6-start-methods-k2).

## 3. Connections ("channels")

| Channel | Transport | Protocol | Created by |
|---|---|---|---|
| Client ↔ master | TCP on `127.0.0.1:7340` (`master.bind`) | KP/1 ([02-protocol](02-protocol.md)) | client connects, master `accept()`s |
| Master ↔ worker *i* | `socket.socketpair(AF_UNIX, SOCK_STREAM)`, one pair per worker | KP/1 IPC messages | master, just before starting the worker |
| Gateway ↔ master | TCP on `127.0.0.1:7340` | KP/1 (as a normal client) | gateway |
| Outside ↔ gateway | TCP on `127.0.0.1:7341` (`gateway.bind`) | HTTP/1.1 subset ([02-protocol §5](02-protocol.md#5-gateway-http-api-k5)) | HTTP clients |
| Master ↔ forkserver | `socketpair(AF_UNIX, SOCK_STREAM)` | KP/1 control messages, plus **fd passing** (`socket.send_fds`) | master, at startup |
| Signal → master | a `socketpair` registered with `signal.set_wakeup_fd` | 1 byte per signal | master, at startup |

**Why a `socketpair` per worker, and not one shared listening socket?** gunicorn uses a shared listening socket: every worker calls `accept()` on it, and the kernel decides who gets each connection. That works for web requests, but the master then has no idea which worker holds which request. Kiln needs to know which job each worker is running, so that when a worker dies it can mark exactly those jobs `LOST` and retry them, and so that a reload can wait for exactly those jobs to finish. So the master **pushes** each job to a chosen worker over that worker's private channel ([ADR-007](../decisions.md#adr-007)). K1's lab compares this with gunicorn's design.

## 4. The master's event loop (the heart of Kiln)

The master is one thread running one loop forever. Every pass through the loop does the following:

```
loop:
    timeout = time until the nearest deadline (job timeout, heartbeat check, shutdown deadline, respawn backoff),
              capped at 1.0 s
    events  = selector.select(timeout)        # sleeps until a socket is readable/writable or timeout passes
    for each ready socket:
        listener readable      → accept new client connections (non-blocking)
        client readable        → read bytes into that client's buffer; parse complete frames; handle requests
        worker channel readable→ read bytes; parse frames; handle ready/heartbeat/result/retiring messages
        forkserver readable    → handle child_exit reports (K2)
        signal pipe readable   → read the signal numbers that arrived; handle each (see 04-supervisor §3)
        any socket writable    → flush its pending output buffer
    run expired timers         → job timeouts, heartbeat timeouts, shutdown/reload progress, respawns
    dispatch()                 → while queue not empty AND some READY worker has a free slot: send a `run`
```

Five rules make this loop correct. Each one fixes a real bug class.

1. **Every socket is non-blocking** (`sock.setblocking(False)`). A read returns whatever bytes are available, possibly none, and never waits.
2. **Frames can arrive in pieces.** One `recv()` might return half a message, or two and a half messages. Each connection has an input `bytearray` buffer, and we only parse **complete** frames out of it. This is the most common networking bug, and K1 has a test that feeds frames one byte at a time.
3. **Writes can be partial too.** Each connection has an output buffer. We call `send()` with what's buffered, keep whatever wasn't sent, and register the socket for `EVENT_WRITE` until the buffer is empty.
4. **Signal handlers do almost nothing.** Python's `signal.set_wakeup_fd` writes the signal number into our pipe, and the real handling happens inside the loop. That avoids running complex code in the middle of whatever line the master happened to be executing.
5. **Time comes from `time.monotonic()`**, never `time.time()`. Wall-clock time can jump when the system clock is adjusted. Monotonic time only moves forward.

## 5. Package layout (module map)

This is the target layout at the end of the project. Each module is created in the milestone shown. [`08-repo-layout-and-tooling`](08-repo-layout-and-tooling.md) covers the files outside `src/`.

```
src/kiln/
  __init__.py            K0  public API: App, task decorator, __version__
  __main__.py            K0  `python -m kiln` → cli.main()
  cli.py                 K0+ argparse-based CLI: serve, submit, status, wait, stats, doctor, bench, forkcheck...
  doctor.py              K0  environment checks (interpreters, ptrace, cgroups, rust...)
  client.py              K1  blocking KP/1 client used by the CLI (connect-with-retry, --trace)
  config.py              K1  Config dataclass, TOML loading (tomllib), env/CLI overrides, validation
  logsetup.py            K1  logging configuration + the event() helper (spec/06)
  errors.py              K1  KilnError base class and the error-code enum shared by master/gateway
  app.py                 K1  App class, @app.task decorator, TaskSpec, manifest building
  protocol/
    framing.py           K1  length-prefixed frame encoder + incremental decoder (FrameDecoder)
    messages.py          K1  message type constants, builders, validators, error codes
  master/
    arbiter.py           K1  the event loop (§4), wiring of all master components
    jobs.py              K1  Job record, JobState enum, JobTable (queue + results + TTL eviction)
    dispatcher.py        K1  choose worker slot, send `run`
    workers.py           K1  WorkerHandle records, worker state machine
    supervisor.py        K1  spawn/reap/recycle/reload/shutdown logic, death classification
    signals.py           K1  wakeup-fd setup, signal → action mapping
    timers.py            K1  deadline heap (heapq)
    connections.py       K1  per-connection buffers, non-blocking read/write helpers
  worker/
    __main__.py          K2  `python -m kiln.worker --fd N --config-json ...` (spawn entrypoint)
    main.py              K1  worker entrypoint (used by every start method); run_worker(), finalize_and_exit()
    heartbeat.py         K1  heartbeat thread
    executors/
      base.py            K1  Executor protocol (submit job → result callback)
      sync.py            K1  run inline, capacity 1
      threads.py         K4  ThreadPoolExecutor-based
      interpreters.py    K4  concurrent.interpreters / InterpreterPoolExecutor (3.14+)
      asyncio_exec.py    K4  event loop per worker
    gilwatch.py          K4  detects which import re-enabled the GIL (3.13t+/3.15t)
    debug.py             K6  tracemalloc / gc / stack-dump handlers for `debug` messages
  launch/
    base.py              K2  Launcher protocol: start(worker_spec) → pid, reap source
    fork.py              K1  os.fork() launcher (K1 hard-codes this; K2 extracts the protocol)
    spawn.py             K2  os.posix_spawn launcher with fd inheritance
    forkserver.py        K2  master-side client of the forkserver
    forkserver_main.py   K2  the forkserver process itself
  forkcheck/             K2  ★ static fork-assumption checker
    __init__.py, rules.py, scanner.py, symbols.py, report.py, confirm.py
  memory/
    procfs.py            K1/K3  read /proc/<pid>/status, smaps_rollup (RSS, PSS, USS, shared)
    gcmodes.py           K3  apply worker.gc_mode (disabled / threshold0 / freeze recipe) in parent and child
    gcwatch.py           K3/K7  who-enabled-gc detector (shim, import flip, poll/sentinel); K7 pause histogram
    exittrick.py         K3  worker.exit_via_os_exit: os._exit from the last atexit handler
    memtable.py          K3  the `kiln mem` table (RSS/PSS/USS/shared per process)
  gateway/               K5
    __main__.py, server.py, http11.py, routes.py, kpclient.py, backpressure.py, watchdog.py, metrics.py
  native/                K8  pure-Python reference validator (+ Rust crate lives in rust/kiln_native/)
    validate.py
  bench/                 K1+ shared measurement toolkit
    records.py           K0  ExperimentRecord schema (JSON) — every lab writes these
    load.py              K1  load generator (asyncio KP/1 client)
    stats.py             K9  bootstrap CIs, summary statistics (stdlib only)
    runner.py            K9  cross-interpreter matrix runner
    report/              K9  t-string HTML renderer (3.14+ only)
  labs/                  K1+ built-in lab tasks (registered only when labs.enabled = true)
    tasks.py             sleep, burn_cpu, alloc, touch_preloaded, crash, hold_gil, leak_* ...
    preload.py           K3  builds the labs.preload_mb object graph (in the master, the forkserver, or each spawned worker)
```

**Dependency direction rule:** `protocol`, `errors` and `logsetup` form the "core" layer and import nothing else from Kiln (`protocol` may import `errors`). `master` and `worker` import `protocol`, `config`, `app` and `memory`, but **never each other**. `gateway` imports only `protocol`, `config` and `bench.records`. `client` imports only the core layer and `config`. `native` imports only the core layer, and `master`/`gateway` may import `native` (K8). `cli` may import everything. `ValidationError` and `FrameDecodeError` live in `errors.py`. `forkcheck` imports nothing else from Kiln. K0 adds an import-linter-style check, written as a small pytest test that parses imports with `ast`, to enforce this.

## 6. Data flow: the life of one job (happy path)

```
client                 master                                   worker 2
  │  submit{task:"add",  │                                          │
  │   args:[2,3]}  ────▶ │ validate task in manifest                │
  │                      │ create Job(id=…, state=QUEUED)           │
  │ ◀── ok{id}  ──────── │ append to queue                          │
  │                      │ dispatch(): worker 2 has a free slot     │
  │                      │ state=RUNNING, attempt=1  ── run{…} ───▶ │ look up "add" in registry
  │  wait{id} ─────────▶ │ register waiter                          │ executor runs add(2,3)
  │                      │ ◀────────────── result{ok:true,value:5} ─│
  │                      │ state=SUCCEEDED, store result, free slot │
  │ ◀── ok{job:{…5…}} ── │ wake waiter                              │
```

The unhappy paths are all specified in [03-jobs-and-tasks §4](03-jobs-and-tasks.md#4-the-job-state-machine) and [04-supervisor](04-supervisor.md): the worker dies, the job times out, the queue is full, the task is unknown, the master is shutting down.

## 7. Concurrency model summary

| Process | Threads | Event loop | Blocking allowed? |
|---|---|---|---|
| master | **exactly 1** | `selectors` (epoll) | **never** |
| worker (sync executor) | main + heartbeat (+ executor threads in K4) | blocking `recv` on its channel, or a small selector | in task code, yes. That's the point |
| gateway | main (asyncio) + watchdog thread | `asyncio` | **never** on the loop thread (K5 detects violations) |
| forkserver | exactly 1 | blocking accept loop + `SIGCHLD` reaping | yes, it does nothing else |

## 8. Things that cut across the whole system

- **Logging:** stdlib `logging`, one line per event, `key=value` structured format, and every line tagged with the process role and pid. See [06-observability](06-observability.md).
- **Config:** a single `Config` object, loaded once in the master and passed to children. See [05-configuration-and-cli](05-configuration-and-cli.md).
- **Measurements:** every lab writes `ExperimentRecord` JSON files. See [10-measurement-methodology](10-measurement-methodology.md).
- **Version gates:** any feature that needs Python ≥ 3.14 or 3.15 lives behind a `sys.version_info` check and has a clear error message. See [ADR-004](../decisions.md#adr-004).
