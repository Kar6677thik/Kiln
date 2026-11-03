# 04 — Supervisor: Starting, Watching, Replacing and Stopping Workers

> **Status:** Approved spec. **Depends on:** [01-architecture](01-architecture.md), [03-jobs-and-tasks](03-jobs-and-tasks.md).
> **Concepts:** process id (pid), parent/child, `fork`, exit code vs. signal, zombie, reaping, `waitpid`, signal handlers, process groups, RSS/PSS. Primers written in K1: `processes.md`, `fork.md`, `signals.md`, `exit-status.md`, `zombies-and-reaping.md`, `memory-metrics.md`.

The supervisor is the part of the master that keeps the right number of healthy workers running. Most of K1 is building this file's contents.

---

## 1. Worker states

```
            launch()
               │
               ▼
        ┌─────────────┐  boot_failed / died before ready      ┌──────────┐
        │   BOOTING   │──────────────────────────────────────▶│  EXITED  │
        └──────┬──────┘  (no `ready` within boot_timeout_s →   └──────────┘
               │ `ready` received        SIGKILL, then EXITED)      ▲
               ▼                                                    │ reaped
        ┌─────────────┐ recycle (max_jobs / max_rss) / reload /     │
        │    READY    │ scale-down / graceful shutdown              │
        └──────┬──────┘─────────────┐                               │
               │                    ▼                               │
               │             ┌─────────────┐ in-flight empty → exit 0
               │             │  RETIRING   │────────────────────────┤
               │             └─────────────┘ (grace expired → SIGKILL)
               │ died unexpectedly / heartbeat timeout / job timeout kill
               └────────────────────────────────────────────────────┘
```

| State | Gets new `run` messages? | Counts toward `workers` target? |
|---|---|---|
| BOOTING | no | yes |
| READY | yes, if it has free slots | yes |
| RETIRING | **no** | **no**, so a replacement is launched immediately |
| EXITED | — | no. The record is deleted after its in-flight jobs are resolved |

## 2. `WorkerHandle` (the master's record of one worker)

```python
@dataclass(slots=True)
class WorkerHandle:
    worker_id: int            # small integer, reused: the lowest free id (0..workers-1, plus extras during reload)
    generation: int           # increments on every SIGHUP reload
    pid: int | None
    state: WorkerState
    channel: socket.socket    # master's end of the socketpair
    capacity: int             # from `ready`
    inflight: dict[str, int]  # job_id → attempt number
    jobs_done: int
    last_heartbeat_mono: float
    rss_bytes: int | None
    launched_mono: float
    retire_reason: str | None
    kill_sent: signal.Signals | None   # the master records its OWN kills, so it can tell them apart from external kills
    deadline_mono: float | None        # boot deadline, or retire grace deadline
```

## 3. Signals the master handles

A **signal** is a tiny notification that the operating system delivers to a process, like a doorbell. It carries no data except which signal it is. Users send them with `kill -HUP <pid>`. Ctrl-C in a terminal sends `SIGINT`.

| Signal | Master action | Mirrors |
|---|---|---|
| `SIGINT` (Ctrl-C) | **1st:** graceful shutdown. **2nd** (while already shutting down): fast shutdown | gunicorn |
| `SIGTERM` | graceful shutdown | gunicorn, systemd/Kubernetes stop |
| `SIGQUIT` | immediate shutdown: SIGKILL every child, exit code 1 | gunicorn |
| `SIGHUP` | graceful **reload** (§5.2) | gunicorn |
| `SIGTTIN` | `workers += 1` | gunicorn |
| `SIGTTOU` | `workers -= 1` (minimum 1). The newest READY worker retires | gunicorn |
| `SIGUSR1` | log a one-shot status dump (worker table, queue depth) at INFO | — |
| `SIGCHLD` | wake up and **reap** (§4) | — |

**How signals reach the loop.** At startup the master:
1. Creates `rsock, wsock = socket.socketpair()`, sets both non-blocking, and calls `signal.set_wakeup_fd(wsock.fileno())`.
2. Installs a Python handler for each signal above. The handler only records the signal: `self._pending_signals.append(signum)`. When a signal arrives, Python's C-level handler also writes the signal number byte to `wsock`, which wakes `select()`.
3. The loop reads and discards the bytes from `rsock`, then processes `_pending_signals` in order.

> **Why not do the work inside the handler?** Python runs signal handlers *between two bytecodes of whatever the main thread was doing*. If the handler changed the job table while the loop was halfway through changing it too, the table could be corrupted. Deferring the work to a known safe point in the loop avoids that. The K1 concept primer `signals.md` must explain this with a concrete example.

### 3.1 Signals in workers

Right after starting, before importing the app, each worker must:

| Signal | Worker disposition | Why |
|---|---|---|
| `SIGINT` | **ignored** (`SIG_IGN`) | Ctrl-C goes to the *whole foreground process group*, which includes the workers. We want only the master to decide what happens ([ADR-024](../decisions.md#adr-024)) |
| `SIGTERM` | handler sets a "retire after current jobs" flag | lets external tools stop one worker gracefully |
| `SIGQUIT` | default (terminate) | |
| `SIGHUP`, `SIGTTIN`, `SIGTTOU`, `SIGUSR1` | reset to default, then ignored | they're inherited from the master after fork and mean nothing in a worker |
| wakeup fd | `signal.set_wakeup_fd(-1)` | the forked child inherited the master's wakeup fd. Left alone, the child's signals would write into the **master's** pipe |

## 4. Reaping and classifying deaths

When a child process exits, the kernel keeps a small record of it, a **zombie**, until the parent calls `waitpid` to collect its **wait status**. If you never reap, zombies pile up.

On every loop pass after a `SIGCHLD` (and, as a safety net, once per second):

```python
while True:
    try:
        pid, status = os.waitpid(-1, os.WNOHANG)   # -1 = any child; WNOHANG = don't block
    except ChildProcessError:
        break                                       # no children at all
    if pid == 0:
        break                                       # children exist but none has exited
    handle_exit(pid, status)
```

In forkserver mode the forkserver sends the same `(pid, status)` in `child_exit` messages, and they go through the same `handle_exit`.

### 4.1 Death classification (the K1 "OOM detection" requirement)

`handle_exit` decodes the status with `os.WIFEXITED`, `os.WEXITSTATUS`, `os.WIFSIGNALED`, `os.WTERMSIG` and `os.WCOREDUMP`, then classifies it:

| Observation | Classification | Log level | In-flight jobs become |
|---|---|---|---|
| exited 0, state was RETIRING | `retired` | INFO | (none should remain. If some do, that's a bug: `LOST`) |
| exited 0, state was READY | `exited_unexpectedly` | WARNING | `worker_lost` |
| exited 3, state BOOTING | `boot_failed` (+ the `boot_failed` message, if one arrived) | ERROR | — |
| exited *n* ≠ 0 | `crashed(exit_code=n)` | ERROR | `worker_lost` |
| killed by SIGKILL, and `kill_sent == SIGKILL` | `killed_by_master(reason)`, where the reason is timeout, heartbeat, boot timeout, grace expired or quit | WARNING | the timeout path (§5.3), or `worker_lost` |
| killed by SIGKILL, **not** sent by the master | `killed(SIGKILL, suspected_oom=true)` + the OOM evidence check (§4.2) | ERROR | `worker_lost` |
| killed by another signal (SIGSEGV, SIGABRT, SIGBUS...) | `killed(signal=<name>, core_dumped=<bool>)` | ERROR | `worker_lost` |

The log line must name the signal (`signal.Signals(n).name`) **and** give a human hint. Example:

```
level=ERROR role=master event=worker_died worker_id=2 pid=4312 how=killed signal=SIGKILL suspected_oom=true
  oom_evidence="cgroup memory.events oom_kill 0→1" inflight=1 hint="SIGKILL not sent by Kiln: usually the kernel OOM killer; check memory limits"
```

This is exactly the signal Back Market didn't have (research §2.7). Their only clue was "worker lost".

### 4.2 OOM evidence

"Suspected OOM" is a guess. Something else could have sent the SIGKILL. To make it **evidence**, the master reads the cgroup v2 file `memory.events` for its own cgroup:

- Find the cgroup path from `/proc/self/cgroup`: the line `0::/some/path`. The file is then `/sys/fs/cgroup/some/path/memory.events`.
- Read it at startup and again on every SIGKILL death. If the `oom_kill` counter went up, set `oom_confirmed=true`. Both fields appear in the `worker_died` event: `oom_confirmed` (bool) and `oom_evidence` (text such as `"cgroup memory.events oom_kill 0→1"`, `"no change"` or `"unavailable"`).
- If the file doesn't exist or can't be read (for example the cgroup isn't delegated), report `oom_evidence="unavailable"`. Never crash because of it.

## 5. Supervisor procedures

### 5.1 Keeping the worker count

After any death or retirement, if `count(BOOTING + READY) < workers`, schedule a launch.

**Crash-loop protection** (so a broken app doesn't fork a thousand times a second):
- Launches back off on consecutive *boot* failures: delay = `min(0.1 × 2^(k-1), 30)` seconds, where k = consecutive boot failures.
- If `boot_failure_limit` (default **5**) boot failures happen in a row with no worker reaching READY, the master logs CRITICAL "app fails to boot; giving up", shuts down, and **exits with code 4**.
- A worker reaching READY resets k to 0.

### 5.2 Graceful reload (`SIGHUP`): zero dropped jobs

Goal: replace every worker with a fresh one, while every job that was queued or running still finishes.

1. `generation += 1`. Re-read the config file. If the new config is invalid, log ERROR and **abort the reload**. Old workers keep running.
2. Launch `workers` new workers of the new generation. They boot in parallel with the old ones, so for a moment there are up to 2× the workers. The lab report must note this memory cost.
3. As each new worker becomes READY, pick one old READY worker (the fewest in-flight jobs first) and **retire** it: send `shutdown{graceful:true}`, set state RETIRING, set `deadline = now + graceful_timeout_s` (default 30 s).
4. A retiring worker gets no new jobs. It finishes in-flight work, sends the results, and exits 0. New jobs go to the new workers. **The queue lives in the master, so it's never interrupted.** That's why zero-drop reload is easy in our design and hard in shared-accept designs.
5. If a retiring worker passes its deadline, SIGKILL it. Its in-flight jobs follow the `worker_lost` rules. The lab must show this case too.
6. The manifest switches to the new generation's manifest when the **first** new worker reports `ready`. Queued jobs whose task disappeared fail with `unknown_task` at dispatch time.
7. If new-generation workers fail to boot (the crash-loop limit), **keep the old generation**, log CRITICAL "reload failed; still serving with generation N-1", and don't exit. This is safer than gunicorn's behaviour, and the lab notes the difference.

**Code-reload caveat:** with `start_method = fork` and `app.preload = true`, new workers are forked from a master that already imported the *old* code, so **code changes are not picked up**. That's the same as gunicorn `--preload`. To pick up code, use `app.preload = false` or `spawn`. The docs and the CLI `kiln reload --help` text must both say this.

### 5.3 Job timeout enforcement

- When a job starts, the master sets a timer at `start + timeout_s`.
- When it fires and the job is still RUNNING on that attempt, the master marks the attempt `timeout`, sends **SIGKILL** to the worker (`kill_sent = SIGKILL`, reason `job_timeout`), and resolves the job (retry or TIMED_OUT).
- **Collateral damage:** a worker with capacity > 1 (threads, async) has *other* in-flight jobs. They become `worker_lost`, with error message "collateral: worker killed due to timeout of job X". This trade-off is intentional and documented. Python can't safely kill one thread. In K4, the asyncio executor first tries *cooperative* cancellation at `timeout_s`, and the master's hard kill fires only at `timeout_s + jobs.cancel_grace_s` (default 2 s).

### 5.4 Heartbeats and hung workers

- If `now - last_heartbeat > worker.heartbeat_timeout_s` (default 10 s), the master SIGKILLs the worker (reason `heartbeat_timeout`).
- **Note:** a pure-Python CPU loop does *not* stop heartbeats on the GIL build, because the interpreter switches threads every 5 ms (`sys.getswitchinterval()`). A C extension holding the GIL, or a deadlock, *does* stop them. That's exactly the hung-worker case we want to catch. K1's lab demonstrates both.

### 5.5 Recycling (`max_jobs`, `max_rss`)

| Setting | Default | Check | Action |
|---|---|---|---|
| `worker.max_jobs` | 0 (off) | in the worker, after each job: `jobs_done ≥ max_jobs + jitter`, where jitter is a random integer in `[0, worker.max_jobs_jitter]` chosen at boot | worker sends `retiring{reason:"max_jobs"}`, then the RETIRING flow |
| `worker.max_rss_mb` | 0 (off) | in the worker after each job (reading `/proc/self/status` `VmRSS`), **and** in the master every 5 s from the heartbeat's `rss_bytes` | worker: `retiring{max_rss}`. Master-side detection: send `shutdown{graceful:true}` |
| `worker.memory_metric` | `rss` | `rss`, `pss` or `uss` (K3 adds `pss`/`uss` via `smaps_rollup`) | — |

**Why jitter?** Without it, all workers started together hit `max_jobs` together and restart together, which causes a capacity dip. gunicorn has `max_requests_jitter` for this reason.

**Why RSS can mislead:** RSS counts *shared* copy-on-write pages in every worker, so the sum of RSS across workers overstates real memory use. K3 is the lab about this. Until K3, RSS is used and the docs point out the caveat.

### 5.6 Shutdown

| Mode | Steps | Exit code |
|---|---|---|
| **Graceful** (SIGTERM, or the 1st SIGINT) | 1) close the listener, so new clients get "refused"; existing connections get `SHUTTING_DOWN` for new `submit`s. 2) QUEUED jobs → CANCELLED ("master shutting down"), and waiters are woken. 3) send `shutdown{graceful}` to all workers. 4) wait until all have exited or `graceful_timeout_s` passes, then SIGKILL the rest. 5) flush client responses (up to 1 s). 6) exit | 0 (1 if any SIGKILL was needed) |
| **Fast** (2nd SIGINT) | send SIGTERM to all workers, wait up to 2 s, SIGKILL the rest, exit | 1 |
| **Immediate** (SIGQUIT) | SIGKILL all children, exit | 1 |
| **Give up** (crash-loop limit) | like graceful | 4 |

Master exit codes, all together: **0** clean, **1** forced, **2** bad config or usage (argparse convention), **3** reserved for worker boot errors, **4** app failed to boot repeatedly.

## 6. Start methods (K2)

A **start method** is *how* a new worker process gets created. Python's `multiprocessing` has three. Kiln implements its own versions of all three from scratch, because this is a learning project ([ADR-012](../decisions.md#adr-012)).

| Method | How Kiln creates the worker | The worker starts with... | Cost | Typical breakage |
|---|---|---|---|---|
| `fork` (K1 default) | `os.fork()` in the master | a **copy of the master's memory**: preloaded app, globals, *and* inherited fds and signal handlers | fastest; memory is shared copy-on-write | threads and locks copied mid-state, inherited fds and sockets, "works on Linux, fails on macOS/Windows" |
| `spawn` | `os.posix_spawn(sys.executable, ["python", "-m", "kiln.worker", "--fd", N, ...])`, inheriting only the channel fd (`os.set_inheritable(fd, True)`) | a **fresh interpreter**. It must import the app itself | slowest (hundreds of ms); no sharing | code at module level without a `__main__` guard runs again; globals set at runtime are missing; lambdas can't be pickled (for `multiprocessing` users) |
| `forkserver` | the master asks the forkserver process (started with `posix_spawn` at master boot, which imports `start.forkserver_preload` modules) to fork; the channel fd is sent with `send_fds` | a copy of the **forkserver's** clean memory | fast after the first one; some sharing | the same as spawn for state set in the master after startup |

**Rules that apply to every launcher:**
1. **The child closes every fd it doesn't need.** That means the listener, client connections, other workers' channels and the wakeup pair. Fork launcher: the master keeps a registry of "fds a child must close", and the child closes them first thing. Spawn: guaranteed by `close_fds` semantics, because every Python fd is non-inheritable by default (PEP 446) except the one we mark. A K1 test checks `/proc/<worker>/fd` contains only stdin/stdout/stderr, the channel, and fds the worker itself opened.
2. **Random state.** After `os.fork()`, `random` is automatically reseeded via `os.register_at_fork` (since 3.7). The primer must mention this and the lab must verify it, because forked children producing identical "random" numbers is a classic bug.
3. **Pid tracking.** The fork and spawn launchers produce direct children, which the master reaps itself. Forkserver workers are reaped by the forkserver and reported back (§4).
4. **Losing the forkserver breaks forkserver mode.** The master detects that the forkserver exited, logs CRITICAL, and restarts it with the same backoff as §5.1. Workers forked by the old forkserver keep running, but they're now **orphans**: they've been re-parented to init (pid 1), so the master can never get their exit status.
   - **Liveness check:** for each orphan, the master opens a `pidfd` with `os.pidfd_open(pid)` (Linux 5.3+; WSL has 6.6) and registers it in its selector. The pidfd becomes readable when the process exits.
   - **Why pidfd and not `os.kill(pid, 0)`:** a pid can be **reused** after init reaps the orphan. Polling with `kill(pid, 0)` could then mistake an unrelated new process for our worker.
   - **Dead orphans:** when its pidfd fires, an orphan is classified `unknown_exit`, and its in-flight jobs become `worker_lost`.
   - **Opening the pidfd:** the master opens it **at spawn time** for every forkserver child (from the `spawned` message), not later. That closes the race in which the pid gets reused.
   - **Primer:** the K2 primer `reparenting-and-orphans.md` must explain all of this.
5. **Forked children end with `os._exit()`** ([ADR-028](../decisions.md#adr-028)), never by returning or calling `sys.exit()`, so the parent's atexit handlers and buffers don't run twice.

## 6a. Supervising the gateway (K5)

The gateway is supervised like a worker, with these differences (K5 §5.1 has the details):

| Aspect | Rule |
|---|---|
| When it starts | after the first worker reports READY, so that the manifest exists |
| How it starts | spawn (`posix_spawn` of `python -m kiln.gateway`), with a pipe as its stdout |
| Readiness | it writes the single line `KILN_GATEWAY_READY <port>` to stdout. If that doesn't arrive within `master.boot_timeout_s`, the master SIGKILLs it and backs off |
| Death | it is reaped and classified like a worker (§4.1, role `gateway`), and restarted with the §5.1 backoff |
| Giving up | after **5** consecutive failures, the master logs CRITICAL `gateway_gave_up` and **keeps serving KP without a gateway**. That is softer than the worker crash-loop rule, because the gateway isn't needed to run jobs |
| Shutdown | graceful shutdown sends it SIGTERM first. It answers in-flight long-polls with 202 and exits |

## 7. Defaults summary (all of these are in [05-configuration](05-configuration-and-cli.md))

`workers=4`, `boot_timeout_s=30`, `graceful_timeout_s=30`, `heartbeat_interval_s=1`, `heartbeat_timeout_s=10`, `boot_failure_limit=5`, `max_jobs=0`, `max_jobs_jitter=0`, `max_rss_mb=0`, `memory_metric="rss"`.
