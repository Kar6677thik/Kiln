# 05 — Configuration and Command-Line Interface

> **Status:** Approved spec. **Concepts:** TOML, environment variables, command-line arguments, precedence, `argparse`, `tomllib`, dataclasses.

---

## 1. Where settings come from, and who wins

Settings are merged in this order. **Later sources win**:

1. **Built-in defaults** (the tables below)
2. **Config file**, TOML: `--config path/to/kiln.toml`. With no flag, `./kiln.toml` is used if it exists
3. **Environment variables**: `KILN_<SECTION>__<KEY>` (double underscore between section and key, uppercase). Example: `KILN_WORKER__MAX_JOBS=500`
4. **CLI flags**, for the handful of common settings listed in §4

Values from env vars are parsed as TOML values (`KILN_MASTER__WORKERS=8` becomes the int 8, `KILN_LABS__ENABLED=true` becomes the bool true). If parsing fails, the raw string is used.

**Validation** happens once, after merging. Any error (wrong type, out of range, unknown key) prints *every* problem and exits with **code 2**. Unknown keys are errors, not warnings, because a typo like `max_job = 500` should never be silently ignored.

`Config` is a tree of frozen dataclasses (`@dataclass(frozen=True, slots=True)`), loaded once in the master. Workers receive it how their start method allows: inherited through fork, or serialized as JSON in the `--config-json` argument (spawn) or the `spawn_worker` message (forkserver).

## 2. Complete key reference

### `[master]`
| Key | Type | Default | Meaning |
|---|---|---|---|
| `bind` | str `host:port` | `"127.0.0.1:7340"` | KP/1 listener. A non-loopback host is rejected unless `allow_non_loopback = true` (ADR-021) |
| `allow_non_loopback` | bool | `false` | safety switch |
| `workers` | int ≥ 1 | `4` | target worker count |
| `boot_timeout_s` | float > 0 | `30` | a worker must send `ready` within this time |
| `graceful_timeout_s` | float > 0 | `30` | how long retiring workers get to finish |
| `boot_failure_limit` | int ≥ 1 | `5` | consecutive boot failures before giving up |
| `pidfile` | str or `""` | `""` | if set, the master writes its pid here (used by `kiln reload/stop`) |

### `[app]`
| Key | Type | Default | Meaning |
|---|---|---|---|
| `spec` | str | — (required; or the CLI positional arg) | `"module:attribute"` |
| `preload` | bool | `true` | **fork:** import the app in the master before forking, which is needed for copy-on-write sharing (K3). **forkserver:** the app module is added to the *forkserver's* preload list instead, so the master stays clean (K2). **spawn:** no effect; the master logs a WARNING once |
| `pythonpath` | list[str] | `[]` | extra entries prepended to `sys.path` before import |

### `[start]` (K2)
| Key | Type | Default | Meaning |
|---|---|---|---|
| `method` | `"fork"`/`"spawn"`/`"forkserver"` | `"fork"` | how workers are created |
| `forkserver_preload` | list[str] | `["kiln"]` | modules the forkserver imports before forking |

### `[worker]`
| Key | Type | Default | Meaning |
|---|---|---|---|
| `executor` | `"sync"`/`"threads"`/`"interpreters"`/`"asyncio"` | `"sync"` | how a worker runs jobs (K4 adds everything except sync) |
| `executor_concurrency` | int ≥ 1 | `8` | slots per worker for non-sync executors |
| `heartbeat_interval_s` | float > 0 | `1.0` | |
| `heartbeat_timeout_s` | float > interval | `10.0` | |
| `max_jobs` | int ≥ 0 | `0` | 0 means unlimited |
| `max_jobs_jitter` | int ≥ 0 | `0` | |
| `max_rss_mb` | int ≥ 0 | `0` | 0 means unlimited |
| `memory_metric` | `"rss"`/`"pss"`/`"uss"` | `"rss"` | metric used for `max_rss_mb` |
| `gc_mode` | `"default"`/`"disabled"`/`"threshold0"`/`"freeze"` | `"default"` | K3 copy-on-write strategies (see the K3 spec) |
| `exit_via_os_exit` | bool | `false` | K3: Instagram's `atexit.register(os._exit, 0)` trick. It only changes behaviour for **spawned** workers, because forked workers already end with `os._exit` ([ADR-028](../decisions.md#adr-028)) |

### `[queue]` and `[jobs]`
| Key | Type | Default | Meaning |
|---|---|---|---|
| `queue.max_size` | int ≥ 1 | `50000` | QUEUED cap → `QUEUE_FULL` |
| `queue.retry_after_s` | float | `1.0` | hint sent with `QUEUE_FULL` |
| `jobs.default_timeout_s` | float > 0 | `30` | |
| `jobs.default_max_attempts` | int ≥ 1 | `1` | |
| `jobs.cancel_grace_s` | float ≥ 0 | `2.0` | async cooperative-cancel grace (K4) |
| `jobs.result_ttl_s` | float > 0 | `600` | |
| `jobs.max_retained` | int ≥ 1 | `200000` | |

### `[protocol]`
| Key | Type | Default |
|---|---|---|
| `max_frame_bytes` | int | `1048576` |
| `validator` | `"python"`/`"native"` | `"python"` | K8: which payload validator the master and gateway use. `native` needs the `kiln_native` Rust extension, and there is **no silent fallback** (it's a config error, exit 2) |

### `[gateway]` (K5)
| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `false` | supervise a gateway process |
| `bind` | str | `"127.0.0.1:7341"` | |
| `max_inflight` | int | `1024` | concurrent HTTP requests being processed; above this → `429` |
| `master_connections` | int | `4` | pool of KP/1 connections to the master |
| `max_body_bytes` | int | `1048576` | |
| `header_timeout_s` | float | `10` | |
| `keepalive_timeout_s` | float | `5` | |
| `watchdog.enabled` | bool | `true` | |
| `watchdog.interval_ms` | int | `50` | how often the watchdog pings the loop |
| `watchdog.threshold_ms` | int | `100` | lag above this is a "stall" → logged with a stack |

### `[debug]`, `[labs]`, `[log]`
| Key | Type | Default | Meaning |
|---|---|---|---|
| `debug.enabled` | bool | `false` | enables the KP `debug` request and `/debug/*` routes |
| `labs.enabled` | bool | `false` | registers the `labs.*` tasks |
| `labs.preload_mb` | int | `0` | K3: size of the object graph preloaded in the master |
| `labs.gc_watch` | bool | `false` | K3: install the "who re-enabled the GC?" detector (`memory/gcwatch.py`) in the master and workers |
| `labs.preload_composition` | `"mortal"`/`"immortal-heavy"` | `"mortal"` | K3 lab (b): what the preload graph's leaf values are made of ([ADR-036](../decisions.md#adr-036)) |
| `log.level` | str | `"INFO"` | |
| `log.format` | `"kv"`/`"json"` | `"kv"` | |
| `log.file` | str or `""` | `""` | `""` means stderr |

## 3. Example `kiln.toml`

```toml
[app]
spec = "examples.demo:app"

[master]
workers = 4

[worker]
max_jobs = 1000
max_jobs_jitter = 100
max_rss_mb = 512

[labs]
enabled = true
```

## 4. The `kiln` CLI

Implemented with `argparse` (stdlib). Every subcommand has `--help` text written for beginners, with an example. Exit codes follow §5 of [04-supervisor](04-supervisor.md) for `serve`. Other commands use 0 for success, 1 for failure and 2 for usage errors.

| Command | Milestone | What it does |
|---|---|---|
| `kiln doctor [--json] [--milestone Kn]` | K0 | checks the environment and prints a table of pass/warn/fail with a fix hint each. `--milestone` checks only what that milestone needs (spec/07 §12) |
| `kiln serve APP_SPEC [--config F] [--workers N] [--bind H:P] [--start-method M] [--executor E] [--labs] [--debug]` | K1 | runs the master in the foreground. Logs go to stderr |
| `kiln submit TASK [--args JSON] [--kwargs JSON] [--wait] [--timeout S]` | K1 | prints the job id, or with `--wait` the final JobView as pretty JSON |
| `kiln status JOB_ID` / `kiln wait JOB_ID [--timeout S]` / `kiln cancel JOB_ID` | K1 | |
| `kiln stats [--watch]` | K1 | prints the stats table. `--watch` refreshes every second |
| `kiln reload` / `kiln stop [--fast]` / `kiln scale +N\|-N` | K1 | sends SIGHUP / SIGTERM (SIGINT×2 with `--fast`) / SIGTTIN×N or SIGTTOU×N to the pid from `--pidfile` |
| `kiln bench load --jobs N (--concurrency C \| --rate R) [--task T] [--args JSON] [--kwargs JSON] [--reload-every S] [--pidfile F] [--out DIR]` | K1 | load generator: closed loop (`--concurrency`) or open loop (`--rate`, which avoids coordinated omission). `{i}` inside `--args`/`--kwargs` is replaced by the job's index (K6 needs unique keys). Writes an ExperimentRecord |
| `kiln forkcheck PATH... [--format text\|json] [--confirm]` | K2 | ★ the fork-assumption checker |
| `kiln mem PID... [--children-of PID] [--json] [--watch S]` | K3 | prints RSS/PSS/USS/Shared/Private_Dirty/Swap per process from `/proc/<pid>/smaps_rollup`, with a Σ row; `--children-of` adds a master's workers (and the forkserver's) |
| `kiln debug WORKER_ID OP [--params JSON]` | K6 | sends a KP `debug` request |
| `kiln bench matrix [--suite S] [--interpreters ...] [--repeats N]` | K9 | runs the cross-interpreter matrix |
| `kiln report RESULTS_DIR -o report.html` | K9 | renders HTML (needs Python ≥ 3.14 for t-strings) |

**Global flags** (accepted by every subcommand, placed before it: `kiln --trace submit ...`):

| Flag | Meaning |
|---|---|
| `--version` | print the Kiln version, the Python version and the build (gil/freethreaded), then exit |
| `--trace` | print every KP/1 frame sent or received, as hex plus pretty JSON (ADR-008's debugging aid) |
| `--log-level L`, `--log-format kv\|json` | override `log.level` and `log.format` |
| `--host H`, `--port P` | for client commands. The default comes from `master.bind` |

`serve` also accepts `--pidfile PATH`, which overrides `master.pidfile`. `reload`, `stop` and `scale` read the pid from `--pidfile`. They default to `master.pidfile`, and fail with a clear message if it's empty.
