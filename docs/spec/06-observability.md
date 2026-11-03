# 06 — Observability: Logs, Metrics, Stats and Debug Operations

> **Status:** Approved spec. **Depends on:** [01-architecture](01-architecture.md), [02-protocol](02-protocol.md), [04-supervisor](04-supervisor.md).
> **Concepts:** logging, log levels, structured logs, counters vs. gauges vs. histograms, Prometheus text format, stack traces. The K1 primer is `docs/concepts/observability.md`.

**Observability** means being able to answer "what is the system doing right now, and what did it do a minute ago?" *from the outside*, without attaching a debugger. Kiln has four tools for it:

| Tool | Answers | Where |
|---|---|---|
| **Logs** | "What happened, in order?" One line per event | stderr (or `log.file`) of every process |
| **Stats** | "What is the state right now?" A JSON snapshot | KP `stats` request, `kiln stats`, `GET /v1/stats` |
| **Metrics** | "How much / how fast over time?" Numbers that a scraper polls | `GET /metrics` on the gateway (K5+) |
| **Debug ops** | "Show me inside one worker": memory, GC, stacks | KP `debug`, `kiln debug`, `POST /debug/workers/<id>/<op>` (only when `debug.enabled = true`) |

---

## 1. Logging

### 1.1 Format

Kiln uses the stdlib `logging` module, configured once per process by `kiln.logsetup.configure(role, config)`. That function lives in `config.py`'s neighbour `logsetup.py`, created in K1. Each log record becomes **one line**.

**`kv` format (the default)** is human-friendly and still easy to grep:

```
ts=2026-11-03T19:12:04.123+00:00 level=INFO role=master pid=4301 event=worker_ready worker_id=2 gen=1 pid_worker=4312 capacity=1 boot_ms=84.2
```

**`json` format** is the same data as one JSON object per line ("JSON Lines"), which is handy for scripts:

```json
{"ts":"2026-11-03T19:12:04.123+00:00","level":"INFO","role":"master","pid":4301,"event":"worker_ready","worker_id":2,"gen":1,"pid_worker":4312,"capacity":1,"boot_ms":84.2}
```

### 1.2 Fields on every line

| Field | Meaning |
|---|---|
| `ts` | wall-clock timestamp, ISO 8601, UTC, millisecond precision. It's for humans, so it may jump if the clock is adjusted |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `role` | `master`, `worker`, `gateway` or `forkserver` |
| `pid` | the process id of the process *writing* the line |
| `worker_id` | present on every `worker` line (the worker knows its own id) |
| `event` | a **stable** snake_case name from the catalogue in §2. Tests and lab scripts grep for these, so they never change without an ADR |
| `msg` | optional free text, used when no event name fits. Avoid it for anything a lab script needs |

**Value encoding in `kv`:** values with no spaces, `=` or quotes are written bare. Anything else is written in double quotes with `\"` and `\\` escaped. Newlines in values, such as tracebacks, become `\n`. **Every event is exactly one line**, so `grep` always works.

### 1.3 Levels: when to use which

| Level | Use for | Example |
|---|---|---|
| `DEBUG` | high-volume detail, off by default | `job_dispatched` for every job |
| `INFO` | normal lifecycle events a human wants to see | `worker_ready`, `reload_finished` |
| `WARNING` | something odd that Kiln handled | `late_result_ignored`, `worker_exited_unexpectedly` |
| `ERROR` | something failed: a job, a worker | `worker_died` with a crash, `job_failed` |
| `CRITICAL` | Kiln itself is in trouble | `boot_failure_limit_reached`, `forkserver_died` |

**Per-job events are `DEBUG`** (`job_submitted`, `job_dispatched`, `job_finished`). At 10,000 jobs, INFO-level per-job lines would drown everything else and slow the master down. Failures are the exception: `job_failed`, `job_timeout` and `job_lost` are logged at WARNING or ERROR.

### 1.4 Fork safety of logging

`logging` handlers hold **locks**. If the master forked while another thread held a handler's lock, the child would deadlock on its first log call. The master is single-threaded ([ADR-006](../decisions.md#adr-006)), so this can't happen in Kiln. Still, every worker **re-runs `logsetup.configure(role="worker")`** right after start. That gives it its own `role`/`worker_id` fields and fresh handlers regardless of start method. Under spawn and forkserver, handlers configured in the master **don't exist** in the child at all. That's the FSL pyfeeds incident studied in K2 ([ADR-017](../decisions.md#adr-017)).

### 1.5 Where logs go

- Every process writes to **its own stderr**. Children inherit the master's stderr (fd 2), so in a terminal all lines appear interleaved. The `role`/`pid` fields tell them apart.
- Each line is written with a single `write()` call smaller than `PIPE_BUF` (4096 bytes). That makes writes to a pipe atomic, so lines from different processes don't get mixed *within* a line. Longer lines (tracebacks) may interleave. That limit is documented and accepted.
- With `log.file` set, the master and every child open the file in **append mode** (`O_APPEND`). The kernel then makes each write land at the current end of the file. There's no rotation; use `logrotate` with `copytruncate` (a non-goal to automate).

## 2. Event catalogue (complete; add new ones here first)

The "Fields" column lists fields *in addition to* the common ones in §1.2.

### 2.1 Master: startup, shutdown and signals

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `master_starting` | INFO | `version`, `python`, `start_method`, `executor`, `workers`, `app_spec`, `preload` | the very first line |
| `config_loaded` | INFO | `source` (`defaults`/file path), `overrides` (count of env and CLI overrides) | after merge and validation |
| `config_invalid` | ERROR | `errors` (list as string) | validation failed (exit 2, or reload aborted) |
| `app_preloaded` | INFO | `duration_ms`, `tasks` (count) | `app.preload = true` import finished in the master |
| `listening` | INFO | `bind`, `fd` | listener opened (after the first `ready`, ADR-013) |
| `signal_received` | INFO | `signal` (name), `action` (`graceful_shutdown`, `fast_shutdown`, `immediate_shutdown`, `reload`, `scale_up`, `scale_down`, `status_dump`, `reap`) | every signal processed by the loop (SIGCHLD is logged at DEBUG) |
| `status_dump` | INFO | `queue_depth`, `workers` (compact table as string), `inflight` | SIGUSR1 |
| `shutdown_started` | INFO | `mode` (`graceful`/`fast`/`immediate`/`give_up`), `queued_cancelled`, `inflight` | |
| `shutdown_finished` | INFO | `mode`, `duration_ms`, `forced_kills`, `exit_code` | the last line before exit |
| `boot_failure_limit_reached` | CRITICAL | `consecutive_failures`, `last_error` | crash-loop protection fires (§5.1 of 04-supervisor) |
| `cgroup_detected` | INFO | `path`, `memory_events` (`readable`/`unavailable`), `memory_max` | startup OOM-evidence probe (§4.2 of 04-supervisor) |

### 2.2 Master: workers

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `worker_spawned` | INFO | `worker_id`, `gen`, `pid_worker`, `start_method`, `launch_ms` | the launcher returned a pid |
| `worker_ready` | INFO | `worker_id`, `gen`, `pid_worker`, `capacity`, `executor`, `boot_ms`, `gil_enabled`, `tasks` | `ready` received |
| `worker_boot_failed` | ERROR | `worker_id`, `pid_worker`, `error_type`, `error_message`, `consecutive_failures`, `next_attempt_in_s` | `boot_failed` received, or exit 3 |
| `worker_boot_timeout` | ERROR | `worker_id`, `pid_worker`, `boot_timeout_s` | no `ready` in time → SIGKILL |
| `worker_retiring` | INFO | `worker_id`, `pid_worker`, `reason` (`max_jobs`, `max_rss`, `reload`, `scale_down`, `shutdown`, `sigterm`), `inflight`, `deadline_s` | a worker enters RETIRING |
| `worker_died` | INFO/WARNING/ERROR (per the classification table in 04-supervisor §4.1) | `worker_id`, `pid_worker`, `gen`, `how` (`retired`, `exited_unexpectedly`, `boot_failed`, `crashed`, `killed_by_master`, `killed`, `unknown_exit`), `exit_code`, `signal`, `core_dumped`, `suspected_oom`, `oom_confirmed`, `oom_evidence`, `kill_reason`, `inflight`, `jobs_done`, `uptime_s`, `hint` | after reaping |
| `worker_heartbeat_timeout` | ERROR | `worker_id`, `pid_worker`, `silence_s` | → SIGKILL |
| `worker_over_memory` | WARNING | `worker_id`, `metric`, `value_mb`, `limit_mb`, `detected_by` (`worker`/`master`) | max_rss recycling triggered |
| `worker_kill_sent` | WARNING | `worker_id`, `pid_worker`, `signal`, `reason` | the master sends any signal to a worker |
| `respawn_scheduled` | DEBUG | `worker_id`, `delay_s` | |
| `scale_changed` | INFO | `old`, `new` | SIGTTIN/SIGTTOU |
| `gateway_started` | INFO | `pid`, `bind` | the gateway printed its readiness line (K5) |
| `gateway_died` | ERROR | same fields as `worker_died` | the gateway was reaped (K5) |
| `gateway_gave_up` | CRITICAL | `failures` | 5 consecutive gateway failures. KP keeps serving (K5) |

### 2.3 Master: reload

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `reload_started` | INFO | `gen_old`, `gen_new`, `workers` | SIGHUP accepted |
| `reload_aborted` | ERROR | `reason` (e.g. `config_invalid`) | |
| `manifest_switched` | INFO | `gen`, `tasks_added`, `tasks_removed` | first new-generation `ready` |
| `reload_finished` | INFO | `gen`, `duration_ms`, `retired`, `forced_kills` | the last old-generation worker exited |
| `reload_failed` | CRITICAL | `gen_new`, `reason` | new generation hit the crash-loop limit; the old generation keeps serving |

### 2.4 Master: jobs

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `job_submitted` | DEBUG | `job_id`, `task`, `queue_depth` | |
| `job_rejected` | INFO | `task`, `code` (KP error code), `queue_depth` | a `submit` was refused (QUEUE_FULL, UNKNOWN_TASK...) |
| `job_dispatched` | DEBUG | `job_id`, `attempt`, `worker_id`, `wait_ms` (time spent queued) | |
| `job_finished` | DEBUG | `job_id`, `state`, `attempt`, `worker_id`, `run_ms`, `total_ms` | terminal SUCCEEDED |
| `job_failed` | WARNING | `job_id`, `task`, `attempt`, `error_kind`, `error_type`, `error_message` | terminal FAILED |
| `job_timeout` | WARNING | `job_id`, `task`, `attempt`, `worker_id`, `timeout_s`, `will_retry` | timer fired |
| `job_lost` | ERROR | `job_id`, `task`, `attempt`, `worker_id`, `death` (short classification), `will_retry` | worker death with this job in flight |
| `job_retried` | INFO | `job_id`, `attempt_next`, `reason` | RUNNING → QUEUED |
| `job_cancelled` | INFO | `job_id`, `reason` (`client`/`shutdown`) | |
| `late_result_ignored` | WARNING | `job_id`, `attempt_reported`, `attempt_current`, `worker_id` | see 03-jobs-and-tasks §4.1 |
| `jobs_evicted` | DEBUG | `count`, `reason` (`ttl`/`max_retained`) | eviction pass |

### 2.5 Master: connections and protocol

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `client_connected` | DEBUG | `peer`, `conn_id` | |
| `client_disconnected` | DEBUG | `conn_id`, `reason` (`eof`, `error`, `bad_frame`, `shutdown`) | |
| `bad_frame` | WARNING | `conn_id` or `worker_id`, `problem` (`too_large`, `zero_length`, `bad_utf8`, `bad_json`, `not_object`) | |
| `protocol_violation` | CRITICAL | `worker_id`, `detail` | a worker sent a bad frame → worker killed |
| `frame_trace` | DEBUG | `direction` (`in`/`out`), `peer`, `type`, `bytes`, `hex_head` | only with `--trace` (ADR-008) |
| `internal_error` | ERROR | `where`, `error_type`, `traceback` | an exception reached the loop's catch-all. Reply `INTERNAL`; the loop keeps running |

### 2.6 Worker

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `worker_booting` | INFO | `gen`, `start_method`, `executor`, `python`, `free_threaded_build` | first line in the child |
| `app_imported` | INFO | `duration_ms`, `tasks` | after the app import (skipped if inherited through a fork with preload) |
| `gc_mode_applied` | INFO | `mode`, `thresholds`, `frozen_count` | K3 `worker.gc_mode` |
| `gc_reenabled` | WARNING | `layer`, `module`, `job_id`, `stack` (top 8 frames) | K3 gcwatch saw the GC turned back on while `gc_mode` expects it off |
| `gil_forced_off` | WARNING | `module` | K4: `PYTHON_GIL=0` kept the GIL off despite a legacy extension ("running unsafe on purpose") |
| `interp_import_failed` | ERROR | `task`, `error_type`, `error_message` | K4: a module couldn't be imported inside a subinterpreter |
| `gil_reenabled` | WARNING | `module`, `detected_after_import_of`, `import_chain`, `origin`, `hint` | K4 gilwatch detected the GIL turning on |
| `job_started` | DEBUG | `job_id`, `attempt`, `task` | |
| `job_raised` | INFO | `job_id`, `error_type`, `error_message` | the task raised (the traceback goes into the `result` message) |
| `retire_requested` | INFO | `reason` (`max_jobs`, `max_rss`, `sigterm`, `shutdown`) | |
| `worker_exiting` | INFO | `jobs_done`, `uptime_s`, `exit_code` | the last line |
| `debug_op` | INFO | `op`, `req_id`, `duration_ms` | a debug op was served |
| `heartbeat_send_failed` | WARNING | `error` | the channel broke; the worker exits soon |

### 2.7 Forkserver (K2)

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `forkserver_ready` | INFO | `preload` (module list), `preload_ms` | |
| `forkserver_forked` | DEBUG | `worker_id`, `pid_worker` | |
| `forkserver_child_exit` | DEBUG | `pid_worker`, `wait_status` | reaped a child |
| `forkserver_died` | CRITICAL | `exit_code`, `signal`, `restart_in_s` | logged by the **master** |

### 2.8 Gateway (K5)

| Event | Level | Fields | Emitted when |
|---|---|---|---|
| `gateway_listening` | INFO | `bind`, `max_inflight`, `master_connections` | |
| `http_request` | DEBUG | `method`, `path`, `status`, `duration_ms`, `bytes_in`, `bytes_out`, `conn_id` | one line per request ("access log") |
| `http_bad_request` | INFO | `conn_id`, `problem`, `status` | parser rejected the input |
| `overloaded` | WARNING | `inflight`, `max_inflight` | a 429 was produced by gateway backpressure (rate-limited: at most 1 line/s, with a `suppressed` count) |
| `master_unreachable` | ERROR | `error`, `retry_in_s` | KP connection to the master lost |
| `loop_stall` | WARNING | `lag_ms`, `threshold_ms`, `stack` (the loop thread's stack, `\n`-joined), `task` (current asyncio task name, if known) | watchdog detected a blocked loop |
| `loop_stall_recovered` | INFO | `stall_ms` | the loop responded again |

## 3. StatsView (reply to the KP `stats` request)

```json
{
  "server": {
    "version": "0.1.0", "pid": 4301, "uptime_s": 312.5,
    "python": "3.15.2", "free_threaded_build": false,
    "start_method": "fork", "executor": "sync", "generation": 2,
    "state": "running"                       // running | reloading | shutting_down
  },
  "queue": { "depth": 12, "max_size": 50000 },
  "jobs": {
    "by_state": { "QUEUED": 12, "RUNNING": 4, "SUCCEEDED": 9850, "FAILED": 3, "TIMED_OUT": 0, "LOST": 0, "CANCELLED": 0 },
    "retained": 9869,
    "totals_since_start": { "submitted": 9869, "rejected": 0, "retried": 1, "evicted": 0 }
  },
  "workers": [
    { "worker_id": 0, "pid": 4310, "gen": 2, "state": "READY", "capacity": 1, "inflight": 1,
      "jobs_done": 2462, "rss_bytes": 41234432, "last_heartbeat_age_s": 0.4, "uptime_s": 120.1 }
  ],
  "worker_deaths": { "retired": 4, "crashed": 0, "killed": 0, "killed_by_master": 0, "suspected_oom": 0 }
}
```

`kiln stats` renders this as a table. `GET /v1/stats` returns it unchanged.

## 4. Metrics (K5: `GET /metrics` on the gateway)

**What the three metric types mean:**
- **Counter:** a number that only goes up, like a car's odometer. Example: jobs submitted. Rates come from comparing two readings.
- **Gauge:** a number that goes up and down, like a speedometer. Example: queue depth right now.
- **Histogram:** counts of observations falling into **buckets**. Example: how many requests took ≤ 5 ms, ≤ 10 ms... Percentiles can be estimated from it.

The gateway builds metrics from two sources: **its own** (HTTP and loop lag), and **the master's** StatsView, fetched with `stats` on every scrape (at most once a second; within that second the cached copy is served). The Prometheus text format is written by hand in `gateway/metrics.py` (ADR-005: no client library).

### 4.1 Metric list

| Name | Type | Labels | Source |
|---|---|---|---|
| `kiln_up` | gauge | — | 1 if the master answered `stats` |
| `kiln_queue_depth` | gauge | — | master |
| `kiln_queue_max_size` | gauge | — | master |
| `kiln_jobs` | gauge | `state` | master `jobs.by_state` |
| `kiln_jobs_submitted_total` | counter | — | master |
| `kiln_jobs_rejected_total` | counter | — | master |
| `kiln_jobs_retried_total` | counter | — | master |
| `kiln_workers` | gauge | `state` | master |
| `kiln_worker_rss_bytes` | gauge | `worker_id` | master (from heartbeats) |
| `kiln_worker_deaths_total` | counter | `how` | master |
| `kiln_generation` | gauge | — | master |
| `kiln_http_requests_total` | counter | `method`, `route`, `status` | gateway |
| `kiln_http_request_duration_seconds` | histogram | `route` | gateway |
| `kiln_http_inflight` | gauge | — | gateway |
| `kiln_http_overloaded_total` | counter | — | gateway (429s from backpressure) |
| `kiln_loop_lag_seconds` | histogram | — | gateway watchdog |
| `kiln_loop_stalls_total` | counter | — | gateway watchdog |

`route` is the route *pattern* (`/v1/jobs/{id}`), never the raw path. Putting job ids into labels would create millions of series. The primer must call this out as a classic mistake (high-cardinality labels).

**Histogram buckets:** HTTP duration uses `0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, +Inf` seconds. Loop lag uses `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5, +Inf` seconds.

### 4.2 Exposition example

```
# HELP kiln_queue_depth Jobs currently waiting in the master's queue.
# TYPE kiln_queue_depth gauge
kiln_queue_depth 12
# HELP kiln_jobs_submitted_total Jobs accepted by the master since it started.
# TYPE kiln_jobs_submitted_total counter
kiln_jobs_submitted_total 9869
# HELP kiln_http_request_duration_seconds Time from request parsed to response written.
# TYPE kiln_http_request_duration_seconds histogram
kiln_http_request_duration_seconds_bucket{route="/v1/jobs",le="0.005"} 9210
kiln_http_request_duration_seconds_bucket{route="/v1/jobs",le="0.01"} 9790
kiln_http_request_duration_seconds_bucket{route="/v1/jobs",le="+Inf"} 9869
kiln_http_request_duration_seconds_sum{route="/v1/jobs"} 31.7
kiln_http_request_duration_seconds_count{route="/v1/jobs"} 9869
```

The numbers above only illustrate the *format*. They aren't measurements.

**Format rules** (from the Prometheus text format 0.0.4): `Content-Type: text/plain; version=0.0.4; charset=utf-8`. One `# HELP` and one `# TYPE` per metric name. Label values escape `\`, `"` and newlines. Buckets are cumulative, and `+Inf` equals `_count`. The response ends with a newline.

## 5. Debug operations (K6/K7)

Debug ops are only available when `debug.enabled = true` ([ADR-021](../decisions.md#adr-021)). Otherwise the reply is `DEBUG_DISABLED` (KP) or `404` (HTTP).

**Flow:** client → master `debug{target, op, params}` → the master forwards `debug{req_id, op, params}` to that worker → the worker replies `debug_reply` → the master relays it. With `target: "master"`, only `stats` and `gc_stats` are allowed; any other op returns `BAD_REQUEST`.

**Where it runs in the worker:** debug ops run on the worker's **main/control thread** between jobs. With the `sync` executor, a debug request therefore waits until the current job finishes. This is documented, not a bug. The master applies a reply timeout of `params.timeout_s` (default 10 s) and replies `error{code:"INTERNAL", message:"debug op timed out"}` if the worker doesn't answer. Exception: `stack_dump` is served by the **heartbeat thread**, so it works *while a job is stuck*, which is exactly when you need it.

| Op | Milestone | Params | Reply `data` | Notes |
|---|---|---|---|---|
| `tracemalloc_start` | K6 | `frames` (int, default 25) | `{"tracing": true, "frames": 25}` | calls `tracemalloc.start(frames)`. Costs memory and CPU; the primer explains how much |
| `tracemalloc_snapshot` | K6 | `label` (str, required) | `{"label", "traced_current_bytes", "traced_peak_bytes", "count"}` | keeps the snapshot in the worker under `label` (max 5 kept; the oldest is dropped) |
| `tracemalloc_diff` | K6 | `from` (label), `to` (label, or `"now"`), `key_type` (`lineno`/`filename`/`traceback`, default `lineno`), `limit` (default 20), `include` (list of filename globs, optional) | `{"top": [{"where": "file.py:42", "size_diff_bytes", "count_diff", "size_bytes", "traceback": [...] }]}` | this is the Snyk/Fugue technique: diff snapshots and filter by package |
| `tracemalloc_stop` | K6 | — | `{"tracing": false}` | frees the stored snapshots |
| `gc_stats` | K6/K7 | — | `{"enabled", "thresholds", "counts", "stats" (gc.get_stats()), "frozen" (gc.get_freeze_count()), "garbage" (len(gc.garbage)), "pause_histogram" (from gcwatch, if installed)}` | read-only |
| `gc_collect` | K6/K7 | `generation` (0/1/2, default 2) | `{"collected", "duration_ms", "uncollectable"}` | runs `gc.collect(gen)` |
| `type_counts` | K6 | `limit` (default 30), `min_count` (default 100) | `{"top": [{"type": "dict", "count": 51234}], "total_tracked"}` | an "objgraph-lite": counts `type(o).__qualname__` over `gc.get_objects()`. **Slow** (seconds on big heaps) and the primer says why: it touches every tracked object, which also dirties copy-on-write pages (link to K3) |
| `stack_dump` | K6 | `all_threads` (bool, default true) | `{"threads": [{"thread_id", "name", "stack": ["file.py:12 in func", ...]}]}` | built from `sys._current_frames()` + `traceback.extract_stack` |
| `labs_containers` | K6 | — | `{"graveyard", "listeners", "sessions", "render_cache"}`: the sizes of the planted-leak containers | only when `labs.enabled` (otherwise `BAD_REQUEST` "labs disabled") |
| `memory` | K3/K6 | — | `{"rss", "pss", "uss", "shared_clean", "shared_dirty", "private_clean", "private_dirty", "swap"}` bytes | from `/proc/self/smaps_rollup` via `memory/procfs.py` |

Debug replies are ordinary KP frames, so they're limited to `protocol.max_frame_bytes` (1 MiB). Ops that could exceed that (`tracemalloc_diff` with tracebacks, `type_counts`) truncate their lists and set `"truncated": true`.
