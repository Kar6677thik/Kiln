# 09 — Testing Strategy

> **Status:** Approved spec. **Implemented from:** K0 (harness, fixtures), then every milestone.
> **Concepts:** unit test, integration test, fixture, assertion, flaky test, property-based testing, fault injection, code coverage. The K0 Python notes are `python/pytest-basics.md` and `python/hypothesis-basics.md`. The K1 concept primer is `concepts/testing-multiprocess-code.md`.

---

## 1. Why we test, and what tests mean here

A **test** is a small program that runs a piece of Kiln and checks that it behaves as the spec says. In a learning repo tests do two jobs:

1. **Catch mistakes**, as usual.
2. **Document behaviour precisely.** A test like `test_worker_killed_by_external_sigkill_is_reported_as_suspected_oom` is a runnable sentence from the spec. Every test docstring names the spec section it pins down ([standards/documentation.md §4.5](../standards/documentation.md#45-tests-are-documentation-too)).

## 2. The test pyramid

```
                 ▲  fewer, slower, more realistic
                ╱ ╲
               ╱lab╲          tests/lab/          short versions of each milestone experiment  (-m lab)
              ╱─────╲
             ╱integra-╲       tests/integration/  real `kiln serve` + workers as subprocesses   (seconds each)
            ╱  tion    ╲
           ╱────────────╲
          ╱     unit     ╲    tests/unit/         pure functions and classes, in-process          (milliseconds)
         ╱────────────────╲
        ╱    snippets      ╲  tests/snippets/     every docs snippet still runs and prints what the docs say
        ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔  many, fast
```

| Layer | Marker | Runs in `dev.sh check`? | Time budget | Examples |
|---|---|---|---|---|
| Unit | `unit` | yes | whole suite < 20 s | `FrameDecoder`, `JobTable` transitions, `classify_exit`, config merging, HTTP parser, forkcheck rules, bootstrap CI |
| Integration | `integration` | yes | each ≤ 30 s (pytest-timeout), suite < 3 min | submit/wait round-trip, SIGHUP under load, worker crash → LOST, OOM classification with `labs.crash sigkill_self`, spawn vs fork launchers |
| Lab smoke | `lab` | no (`dev.sh test -m lab`) | each ≤ 60 s | "K3 experiment runs end-to-end on a 20 MiB graph and writes a valid ExperimentRecord" |
| Snippets | `snippets` | yes | < 30 s | `docs/concepts/snippets/fork_basics.py` exits 0 and its output matches the primer's shown output (§7) |
| Slow | `slow` | no | — | SC-1/SC-2 full 10K-job runs (K10) |

## 3. Unit tests: what to cover

- **Pure logic first.** Code is structured so that decisions live in pure functions or classes that can be tested without processes. Examples:
  - `supervisor.classify_exit(handle, wait_status)` takes a fake handle and a wait-status integer. Tests build statuses with known encodings (on Linux, `9` means killed by SIGKILL and `1 << 8` means exited with code 1). They use `os.waitstatus_to_exitcode` to cross-check.
  - `JobTable` is driven entirely by method calls, with an injectable clock (§5.4).
  - Rule functions in `forkcheck` take an `ast` tree and return findings.
- **Every row of every state table** in the spec gets at least one test: 03-jobs-and-tasks §4.1, 04-supervisor §4.1, 02-protocol §2.1 and §3.3. The test name includes the row (`test_running_timeout_with_retry_budget_requeues`).
- **Error messages are tested** for their key words, because they are documentation too (standards §4.6).

## 4. Testing multi-process code deterministically

Multi-process tests are where flakiness comes from: a test that passes 99 times and fails once, with no code change. Kiln's rules:

### 4.1 Never `sleep` to wait for something; wait for a *condition*

```python
# ❌ flaky: what if the worker takes 600 ms on a slow day?
time.sleep(0.5); assert stats()["workers"][0]["state"] == "READY"

# ✅ deterministic: poll the condition with a deadline, and fail with a useful message
wait_for(lambda: all(w["state"] == "READY" for w in stats()["workers"]),
         timeout_s=10, what="all workers READY")
```

`tests/helpers.py::wait_for(predicate, timeout_s, what, interval_s=0.02)` polls with `time.monotonic()`. On timeout it raises `AssertionError(f"timed out after {timeout_s}s waiting for {what}; last value: …")`. Sleeping is allowed **only** inside `wait_for`, or when the sleep itself is what's being tested (and then it gets a `# WHY:` comment).

### 4.2 Readiness via logs or protocol, not timing

The `kiln_server` fixture (§4.5) starts `kiln serve` as a subprocess with `KILN_LOG__FORMAT=json` in its environment and reads its stderr on a background thread into a queue. The fixture considers the server *ready* when the `listening` event appears (spec/06). Tests can also `await_event("worker_died", worker_id=2)` to wait for a specific log event. That makes the logs part of the test contract, which is one reason event names are stable.

### 4.3 Ports: bind to port 0

Tests never use the fixed ports 7340/7341, since two test runs, or a dev server, would collide. The fixture passes `--bind 127.0.0.1:0`, and the kernel picks a free port. The master logs the **actual** port in the `listening` event (`bind=127.0.0.1:43817`), and the fixture reads it from there. Gateway tests do the same with `gateway.bind`.

### 4.4 Isolation: temp dirs and env

- Every integration test gets `tmp_path` (a pytest built-in) for its config file, pidfile, log file and results dir.
- The fixture clears every `KILN_*` environment variable and sets only what the test wants, so the developer's shell can't leak settings into tests.

### 4.5 Cleanup: no stray processes, ever

A crashed test can leave workers running, and they eat CPU and ports for every later test. The `kiln_server` fixture:
1. Starts the master in a **new session** (`start_new_session=True`), which also makes it a process-group leader. Its workers inherit the group.
2. On teardown, sends SIGTERM to the master and waits up to `graceful_timeout_s + 2`. Then it sends **SIGKILL to the whole process group** (`os.killpg(pgid, SIGKILL)`) as a backstop.
3. Asserts **no process from that group is still alive** (by scanning `/proc/*/stat` for the pgid). If any are, the test **fails** with a list of pids and their command lines. A leak is a bug.

Forkserver-launched workers are in the same group (inherited through the forkserver), so the same sweep catches them. The K2 spec has a test that proves it.

### 4.6 No leaked file descriptors

- **In-process:** a `fd_leak_check` fixture lists `/proc/self/fd` before and after the test and fails if new fds stayed open. It's used on unit tests of `connections.py`, `framing` socket helpers and the launchers.
- **In workers:** an integration test reads `/proc/<worker pid>/fd` directly (same user, so no special permissions are needed) to see which fds the worker holds. It asserts the set is {0, 1, 2, channel, + fds the worker opened itself}. That catches forgotten `close()` calls after fork (spec/04 §6 rule 1).

### 4.7 Timeouts everywhere

- `pytest-timeout`: 30 s per test by default. A test may raise its own limit with `@pytest.mark.timeout(N)` and a `# WHY:` comment.
- Every socket in test helpers has `settimeout(5)`.
- Every `subprocess.run` in tests has `timeout=`.

### 4.8 Making races reproducible

When a test must hit a specific interleaving (for example "a result arrives *after* the timeout kill was sent"), it doesn't rely on luck. It uses:
- **Injected clocks and hooks:** the master's timer heap reads `self._now()`, which tests replace. Unit tests can therefore "advance time" exactly.
- **Barrier tasks:** `labs.sleep` combined with `threading.Event` files or a tiny control-socket handshake, so a worker blocks at a known point until the test releases it.

## 5. Property-based tests (Hypothesis)

**Property-based testing** means: instead of writing a few example inputs by hand, you describe *what any valid input looks like*. Hypothesis generates hundreds of them, including nasty edge cases, and checks that a *property* (a rule that should always hold) holds for each. When one fails, Hypothesis **shrinks** it to the smallest failing example.

| Target | Property |
|---|---|
| `FrameDecoder` | for any list of JSON objects and **any** way of chopping their encoded bytes into chunks, decoding yields exactly the original list in order (spec/02 §2.2) |
| `FrameDecoder` | any length > max raises `FrameTooLarge` after exactly 4 bytes |
| Message validators | for any dict, the validator either accepts or produces `BAD_REQUEST` naming a field, and never raises anything else |
| HTTP parser (K5) | any request we can serialize, split at any offset, parses identically; random bytes never crash it (only 4xx results) |
| `stats.bootstrap_ci` (K9) | the CI contains the sample median for non-degenerate samples; it's deterministic for a fixed seed |
| Config merge | for any combination of defaults, file and env values, the later source wins key by key |

### 5.1 The JobTable state machine test

`tests/unit/test_jobtable_state_machine.py` uses `hypothesis.stateful.RuleBasedStateMachine`. Hypothesis then calls random sequences of operations and checks invariants after each step:

- **Rules (operations):** `submit`, `dispatch_to(worker)`, `result_ok`, `result_err`, `timeout_fires`, `worker_dies(worker)`, `cancel`, `advance_clock(s)`, `evict`.
- **Invariants checked after every step:**
  1. A job in a terminal state never changes state again.
  2. Every QUEUED job appears exactly once in `queue`, and nothing else is in `queue`.
  3. Every RUNNING job's current attempt is in exactly one worker's `inflight`.
  4. `len(attempts) ≤ max_attempts`.
  5. A late result for an old attempt never changes the job (spec/03 §4.1).
  6. A per-state count equals the StatsView `by_state` numbers.

This one test explores more edge cases than hundreds of hand-written ones. The K1 tour must include a stop on it.

## 6. Fault injection

**Fault injection** means making failures happen *on purpose* to check that the system reacts correctly. Kiln's tools:

| Fault | How | Asserted behaviour |
|---|---|---|
| Worker crashes with exit 1 | `labs.crash how=exit1` | `worker_died how=crashed exit_code=1`; job `LOST` (or retried) |
| Segfault | `labs.crash how=segv` | `how=killed signal=SIGSEGV`; respawn |
| Abort | `labs.crash how=abort` | `signal=SIGABRT core_dumped=…` |
| "External" SIGKILL | `labs.crash how=sigkill_self`, or the test calls `os.kill(worker_pid, SIGKILL)` | `suspected_oom=true`; `kill_sent` is None |
| Real OOM | `labs.alloc mb=1024 hold=true` inside the 512 MiB cgroup (spec/07 §6) | `oom_evidence` counter increment (lab test, `-m lab`) |
| Hung worker | a task that holds the GIL in C (`labs.crash how=…` variant, or `ctypes` `sleep` with the GIL held) / a deadlock | `worker_heartbeat_timeout` → SIGKILL |
| Boot failure | an app spec whose module raises on import | exit code 3; backoff; after 5 → master exit 4 |
| Slow client | a test socket that reads 1 byte per 100 ms | the master keeps serving other clients (output buffering works) |
| Garbage frames | random bytes, oversize length | `BAD_FRAME` / `PAYLOAD_TOO_LARGE` + close |
| Forkserver killed | `os.kill(forkserver_pid, SIGKILL)` | `forkserver_died`; restarted; existing workers keep serving |

## 7. Snippet tests (docs stay true)

Every primer's "See it for yourself" snippet lives in `docs/concepts/snippets/` or `docs/python/snippets/` (standards/documentation.md §2). `tests/snippets/test_snippets.py`:

1. Discovers every `*.py` there.
2. Runs each with the current interpreter in a subprocess, with a 20 s timeout.
3. If a sibling file `<name>.expected.txt` exists, compares the output after **normalizing** volatile parts: pids → `<PID>`, hex addresses → `<ADDR>`, numbers marked in the expected file as `<NUM>`, and timings → `<MS>`.
4. A snippet that only works on Linux, or on ≥ 3.14, declares it in a header comment (`# requires: linux, py>=3.14`), and the test skips it otherwise.

So when a primer says "this prints `child pid=<PID>`", that stays true as Python versions change.

## 8. Free-threaded and cross-version runs

- `scripts/dev.sh test-matrix` runs the default suite on **3.13, 3.14, 3.15 and 3.15t**, each in its own venv.
- On **3.15t** the conftest asserts at session start that `sys._is_gil_enabled()` is `False`. If something re-enabled the GIL (an imported extension), the whole run is marked with a warning in the summary, because results from a "free-threaded" run that actually had the GIL would be misleading. K4's `gilwatch` explains which import did it.
- Tests that only make sense on free-threading (K4 race demo) are marked `requires_ft`. Tests for 3.14+ or 3.15+ features are marked `requires_314` / `requires_315`. Environment-dependent tests are marked `requires_gcc`, `requires_cgroup` or `requires_native` (K8). The full marker list is in spec/08.
- The **K4 race test** is special. It must *demonstrate* the race on 3.15t: the unsynchronized counter loses updates in at least one of 20 trials. It must also show the locked version never does. A demonstration that fails to reproduce the race is reported as `xfail(strict=False)` with an explanation, not as a pass. Races are probabilistic, and the docs say so honestly.

## 9. Coverage

- **Target:** ≥ **85% line coverage** of `src/kiln`. Branch coverage is reported but not gated. Gated modules (`bench/report/`, `executors/interpreters.py`) and `kiln/labs/` are excluded from the threshold. They're tested on their own interpreters.
- **Tool:** `coverage.py`, run via `scripts/dev.sh coverage`. Worker subprocesses are measured too: `COVERAGE_PROCESS_START` points to `pyproject.toml`, and a `.pth` hook in the venv starts coverage in every child. The output is combined with `coverage combine`.
- **Coverage is a smoke alarm, not a goal.** A line being *run* doesn't mean its behaviour is *checked*. The K1 testing primer must explain this.
- Coverage isn't in ADR-005's original dev group. Proposal below.

> **ADR proposal: ADR-027, "Add `coverage` to the dev dependency group"** (status: *Proposed by spec/09; to be accepted in K0*)
> - **Context:** ADR-005 lists the dev group as `pytest, pytest-timeout, hypothesis, ruff, mypy`. This spec needs line coverage measured across subprocesses.
> - **Decision:** add `coverage` (coverage.py) to the `dev` group. Don't add `pytest-cov`. Running `coverage run -m pytest` directly is simpler to explain and handles subprocesses explicitly.
> - **Consequences:** one more dev tool, and the K0 Python notes gain a short `coverage` section in `pytest-basics.md`.

## 10. Flakiness policy

1. **A flaky test is a bug**, either in Kiln or in the test. It's never "just CI being weird".
2. When a test flakes: reproduce it with a loop (`for i in $(seq 50); do ./scripts/dev.sh test tests/…::name || break; done`). Find the timing assumption. Fix it with §4 techniques.
3. **No automatic retries** (no `pytest-rerunfailures`). Retries hide real races, and races are exactly what this project studies.
4. If a fix isn't possible right now, the test is marked `@pytest.mark.skip(reason="flaky: <link to docs/questions.md or milestone 'Stretch / later' entry>")`, and that is listed in the milestone summary.
5. Lab tests (`-m lab`) check that experiments *run and produce valid records*. They never assert on performance numbers, which are noisy. Performance claims live in lab reports, with confidence intervals ([spec/10](10-measurement-methodology.md)).
