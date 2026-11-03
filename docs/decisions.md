# Architecture Decision Records (ADRs)

> An **ADR** is a short note that records one decision: the situation, what we chose, what we gave up, and which options we rejected. ADRs exist so that nobody, including future-you and every implementation round, has to re-argue a settled question or wonder "why on earth is it like this?".
>
> **Rules:**
> - ADRs are never deleted. To change a decision, add a new ADR that says `Supersedes ADR-0NN`, and mark the old one `Superseded by ADR-0MM`.
> - An implementer who disagrees with an ADR stops and asks. They don't quietly deviate.
> - New ADRs use [`templates/adr.md`](templates/adr.md).

| # | Title | Status |
|---|---|---|
| [001](#adr-001) | The implementer writes the code; the learner learns from docs | Accepted |
| [002](#adr-002) | Reader baseline: new to Python | Accepted |
| [003](#adr-003) | Run in WSL2 Debian; repo stays on the Windows path | Accepted |
| [004](#adr-004) | Interpreter matrix and syntax floor | Accepted |
| [005](#adr-005) | Stdlib-only runtime; explicit dev and lab dependencies | Accepted |
| [006](#adr-006) | Master: single-threaded `selectors` loop, no asyncio, no threads | Accepted |
| [007](#adr-007) | Master owns the queue and pushes jobs over per-worker socketpairs | Accepted |
| [008](#adr-008) | Length-prefixed JSON framing (KP/1) | Accepted |
| [009](#adr-009) | JSON-only job payloads, no pickle | Accepted |
| [010](#adr-010) | At-most-once by default; opt-in retries | Accepted |
| [011](#adr-011) | No persistence | Accepted |
| [012](#adr-012) | Hand-built start methods, not `multiprocessing` | Accepted |
| [013](#adr-013) | The task manifest comes from workers; listen only after the first READY | Accepted |
| [014](#adr-014) | Gateway is a separate spawned process with a hand-written HTTP/1.1 subset | Accepted |
| [015](#adr-015) | K4 uses our own tiny C extension to re-enable the GIL | Accepted |
| [016](#adr-016) | K6 planted leaks redefined (`__del__` cycles are collectable) | Accepted |
| [017](#adr-017) | K2 calibration corpus: distilled reproductions + provenance | Accepted |
| [018](#adr-018) | Statistics in the stdlib; HTML report with t-strings | Accepted |
| [019](#adr-019) | Tooling: uv, ruff, mypy --strict, pytest + Hypothesis | Accepted |
| [020](#adr-020) | Version-specific facts are verified, not trusted | Accepted |
| [021](#adr-021) | Localhost-only, no auth, debug off by default | Accepted |
| [022](#adr-022) | One experiment-record format for all labs | Accepted |
| [023](#adr-023) | Memory budget for experiments | Accepted |
| [024](#adr-024) | Workers ignore SIGINT | Accepted |
| [025](#adr-025) | Git workflow: a branch per milestone, user sign-off to merge | Accepted |
| [026](#adr-026) | Documentation is part of "done" and is checked by a script | Accepted |
| [027](#adr-027) | Add `coverage` to the dev dependencies (amends ADR-005) | Accepted |
| [028](#adr-028) | Forked children always end with `os._exit()` | Accepted |
| [029](#adr-029) | Workers raise their own `oom_score_adj` to 500 | Accepted |
| [030](#adr-030) | `labs.hold_gil` fault task, and the `kiln.client` module | Accepted |
| [031](#adr-031) | Master connection limits are constants, not config | Accepted |

---

<a id="adr-001"></a>
## ADR-001: The implementer writes the code; the learner learns from docs
- **Context:** The learner chose "the implementer writes, I learn". The code is a means. Understanding is the product.
- **Decision:** The implementer writes all the code. Every implementation round must also produce the learning material in [`standards/documentation.md`](standards/documentation.md): primers, Python notes, code tours, lab reports and quizzes. The learner reads, runs labs, fills in prediction boxes and asks questions in `docs/questions.md`.
- **Consequences:** Documentation effort will match or exceed coding effort, and that's intended. The research method's "build it without reading the reference first" applies to the *design write-up*. Each lab report has a "Reference comparison" section written after our version works.
- **Rejected:** learner-writes (the learner opted out); docs written only at the end (understanding has to grow alongside the code).

<a id="adr-002"></a>
## ADR-002: Reader baseline is "new to Python"
- **Context:** The learner said they're new to Python itself, not only to internals.
- **Decision:** No Python knowledge is assumed. Every language feature gets a `docs/python/` note the first time it appears in the codebase, and a `# PY:` comment at its first use in each file. K0 writes the starter set of Python notes *before* K1 code exists. The OS and runtime concepts (processes, fds, signals...) get `docs/concepts/` primers, also assuming nothing.
- **Consequences:** Code favours **plain, explicit Python** over clever idioms. A clever construct needs a reason in a `# WHY:` comment *and* a Python note. Examples: walrus `:=`, nested comprehensions, metaclasses, `functools.singledispatch`.

<a id="adr-003"></a>
## ADR-003: Run in WSL2 Debian; the repo stays on the Windows path
- **Context:** The machine is Windows 11. `fork`, `/proc`, Unix signals and Memray need Linux. The environment check on 2026-11-03 found **WSL2 Debian 13 (trixie)**, not the Ubuntu the research notes assumed. It also found kernel 6.6, `uv` installed, system Python 3.13.5, `ptrace_scope=1`, 14 CPUs, about 7.5 GiB RAM visible to WSL, and **no** gcc or Rust yet. The repo is at `C:\Users\Karthik Reddy\Documents\Study\Projects\Kiln`. Note the **space** in the path.
- **Decision:**
  - The source of truth stays on the Windows path, so the editor and T3 work unchanged. Inside WSL it's `/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln`.
  - **All Kiln commands run inside WSL** through `scripts/dev.sh`.
  - Virtual environments live on the WSL ext4 filesystem, not on `/mnt/c`: `UV_PROJECT_ENVIRONMENT=$HOME/.cache/kiln/venvs/<python-tag>`. `/mnt/c` goes through the 9P network filesystem, which is slow and handles symlinks and permissions badly.
  - **Benchmarks that measure startup or import time** (K2 spawn cost, K9) run from an rsync'd snapshot on ext4 (`scripts/bench-snapshot.sh` → `~/kiln-bench/`), so 9P latency doesn't pollute the numbers. The lab report says so.
  - From Windows, the implementer runs `wsl.exe -d Debian --cd "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln" -- ./scripts/dev.sh <cmd>`. **Always quote the path.**
  - Git: `.gitattributes` forces `eol=lf`. Shell scripts break with CRLF.
- **Rejected:** moving the repo into WSL (it would break the existing T3 project setup); native Windows (impossible for Kiln).

<a id="adr-004"></a>
## ADR-004: Interpreter matrix and syntax floor
- **Decision:**
  - **Development interpreter:** CPython **3.15** (latest patch, GIL build).
  - **Test matrix:** 3.13, 3.14 (latest), 3.15, **3.15t** (free-threaded).
  - **Lab-only interpreters:** **3.14.4** and **3.14.5**, pinned for K7.
  - **Syntax floor for `src/kiln` core:** Python **3.13**. That means no t-strings, no `lazy import` and no `frozendict` in core modules.
  - Version-specific features live in gated modules. Gated modules can only be imported on the right version, and they're imported lazily, behind a check that gives a friendly error. Examples: `executors/interpreters.py` needs 3.14+, `bench/report/` needs 3.14+ for t-strings, and the lazy-import demos in K9 need 3.15.
  - Installed with `uv python install 3.13 3.14 3.14.4 3.14.5 3.15 3.15t`.
- **Consequences:** CI-style matrix runs via `scripts/dev.sh test-matrix`. `kiln doctor` checks every interpreter is present. If an exact patch version is missing from uv's index, K0 records the closest available one and the K7 spec gets updated in an ADR.

<a id="adr-005"></a>
## ADR-005: Stdlib-only runtime; explicit dev and lab dependencies
- **Decision:** `src/kiln` has **zero** third-party runtime dependencies. We're learning to build these pieces ourselves.
  - Dev group: `pytest`, `hypothesis`, `ruff`, `mypy`, `pytest-timeout`.
  - Lab groups (optional, per milestone): `lab-k3 = [msgpack]`, `lab-k4 = [kiln-gil-labs]` (our own C extension pair, a local path source; see ADR-015 and spec/08), `lab-k6 = [memray, requests]`, `lab-k8 = [maturin]`, `lab-k9 = []`. `coverage` was added to the dev group by ADR-027.
  - New dependencies need an ADR.
- **Rejected:** psutil (we read `/proc` ourselves, which teaches more); h11 or aiohttp (K5 hand-writes HTTP); numpy and scipy (K9 implements a bootstrap in the stdlib).

<a id="adr-006"></a>
## ADR-006: The master is a single-threaded `selectors` loop (no asyncio, no threads)
- **Context:** The master calls `fork()`. `fork()` in a multi-threaded process copies only the calling thread. Locks held by other threads (for example inside `logging`) stay locked forever in the child, and the child deadlocks. Python 3.12+ even emits a `DeprecationWarning` when you fork with threads running.
- **Decision:** The master never starts threads. Its event loop is written directly on `selectors.DefaultSelector` (epoll on Linux).
- **Why not asyncio in the master?** Writing the loop by hand teaches what asyncio does underneath. And K5 uses asyncio in the gateway, so both get learned.
- **Consequences:** No blocking calls in the master. Anything slow (like reading `/proc/<pid>/smaps_rollup` for many workers) has to be cheap or rate-limited.

<a id="adr-007"></a>
## ADR-007: The master owns the queue and pushes jobs over per-worker socketpairs
- **Context:** gunicorn-style workers `accept()` on a shared socket, so the master never knows what each worker is doing.
- **Decision:** One in-memory queue in the master, and one `AF_UNIX` socketpair per worker. The master **pushes** `run` messages only to workers with free slots.
- **Consequences:** The master knows each worker's in-flight jobs. That enables `LOST` detection, retries, and zero-drop reload (SC-2). The master's CPU becomes the throughput ceiling: about one frame decode plus encode per job. K1 measures this ceiling, and the lab explains it.
- **Rejected:** shared accept (it can't attribute jobs to workers); Redis or another broker (an external dependency that hides the interesting parts).

<a id="adr-008"></a>
## ADR-008: Length-prefixed JSON framing (KP/1)
- **Decision:** 4-byte big-endian length followed by a UTF-8 JSON object. See [spec/02-protocol](spec/02-protocol.md).
- **Why not newline-delimited JSON?** It's easier to eyeball, but it forces the receiver to scan for `\n` and depends on JSON never containing raw newlines. Length prefixes teach `struct`, byte order and incremental decoding, which are the skills HTTP/2, Postgres, Redis RESP3 and Kafka all build on.
- **Consequences:** You can't debug it with `nc`. So `kiln submit` and the other CLI commands *are* the debugging tools, and a `--trace` flag prints every frame as hex and JSON.

<a id="adr-009"></a>
## ADR-009: JSON-only job payloads, no pickle
- **Decision:** Arguments and results must be JSON values.
- **Why:** `pickle.loads` on untrusted data runs arbitrary code. Pickled payloads also couple the client to the server's Python version and class definitions. Pickling does still appear *as a subject of study* in K2, because `multiprocessing` relies on it.

<a id="adr-010"></a>
## ADR-010: At-most-once by default; opt-in retries
- **Decision:** `max_attempts = 1` by default. Retries (at-least-once) only happen when the task or submit opts in. See [spec/03 §5](spec/03-jobs-and-tasks.md#5-delivery-semantics-how-many-times-can-my-function-run).

<a id="adr-011"></a>
## ADR-011: No persistence
- **Decision:** Queue and results live in memory only. A master crash loses everything. This is stated in `kiln serve --help` and in the README.
- **Rejected:** a SQLite journal. It's a worthwhile project of its own, but off-topic here.

<a id="adr-012"></a>
## ADR-012: Hand-built start methods, not `multiprocessing`
- **Decision:** Kiln's launchers use `os.fork`, `os.posix_spawn`, our own forkserver, `socket.send_fds` and `waitpid` directly. The `multiprocessing` module appears in two places only: K2's *confirmation harness*, which runs third-party code that itself uses multiprocessing, and the *reading assignment* afterwards (compare with `multiprocessing/popen_*.py` and `forkserver.py`).

<a id="adr-013"></a>
## ADR-013: The task manifest comes from workers; the master listens only after the first READY
- **Context:** With `app.preload = false` or `spawn`, the master never imports the app, but it still has to validate `submit`.
- **Decision:** Workers report their manifest in `ready`. The master opens its listener only after the first `ready`. On reload, the manifest switches when the first new-generation worker is ready.
- **Consequences:** Early clients get "connection refused". `kiln submit` retries for 10 s.

<a id="adr-014"></a>
## ADR-014: The gateway is a separate spawned process with a hand-written HTTP/1.1 subset
- **Decision:** `kiln.gateway` runs as its own process, supervised by the master and started with spawn. It talks to the master over KP/1, and it parses HTTP itself. The supported subset is in [spec/02 §5](spec/02-protocol.md#5-gateway-http-api-k5).
- **Rejected:** HTTP inside the master (it breaks ADR-006); aiohttp or h11 (they hide exactly what K5 teaches). **The reference comparison afterwards** is uvicorn + h11.

<a id="adr-015"></a>
## ADR-015: K4 uses our own tiny C extension to re-enable the GIL
- **Context:** Research §2.9 says that importing a C extension that doesn't declare free-threading support turns the GIL back on. Which *third-party* packages still lack that declaration in Nov 2026 isn't something we can know in advance, and it changes every month.
- **Decision:** `labs/k4/gil_reenabler/` contains a ~30-line C extension *without* `Py_mod_gil`, and a twin *with* `Py_MOD_GIL_NOT_USED`. They're built with setuptools in WSL, which needs `build-essential`. That makes the experiment deterministic. As a bonus, K4 also scans the installed environment and reports which real packages re-enable the GIL. That's informational only, because the result depends on dates.

<a id="adr-016"></a>
## ADR-016: K6 planted leaks redefined (`__del__` cycles are collectable)
- **Context:** Research K6 says "planted leaks (`__del__` cycles, …)". Since **Python 3.4 (PEP 442)**, cycles whose objects have `__del__` **are collected**. Before that they went to `gc.garbage`. A plain `__del__` cycle no longer leaks.
- **Decision:** K6 plants these leaks instead:
  1. **Cycles while the GC is disabled.** This links K3 to K6: Instagram-style `gc.disable()` turns every cycle into a leak.
  2. **`__del__` resurrection:** a finalizer that stores `self` in a global.
  3. An **unbounded `functools.lru_cache`** (or a dict used as a cache) keyed on request data.
  4. A **closure capturing a large object**, registered in a long-lived callback list.
  5. **Optional:** a `requests.Session` pattern in the spirit of the Snyk/Fugue story. It's labelled "modelled after", since we can't reproduce their exact bug.
  - The lab also *demonstrates* that a plain `__del__` cycle is collected now. That's a good "myth vs. fact" teaching moment.

<a id="adr-017"></a>
## ADR-017: K2 calibration corpus: distilled reproductions plus provenance
- **Context:** The checker must catch the three real breakages: krb5, oslo.concurrency and FSL pyfeeds. Preliminary research (2026-11-03) found:
  - **krb5 #9197 / PR #1483:** `src/tests/t_otp.py` has **no `if __name__ == "__main__":` guard**. Under forkserver the child re-imports the script and its `k5test` import, which **registers an `atexit` handler** that prints a spurious failure message. `daemon.start()` raised `ConnectionResetError`. Upstream's fix was to force `fork`.
  - **oslo.concurrency (test_lockutils):** **local (nested) functions used as Process targets**, which can't be pickled under spawn or forkserver. The Debian fix moved them to module level; the Gentoo fix forced `get_context('fork')`.
  - **FSL pyfeeds MR 46:** **logging handlers configured in the parent** were assumed to exist in children. The page was unreachable during the spec pass, so K2 step 1 must retrieve it or record that it's unavailable.
  - Bonus cases found: **git-repo** (a class-level mock patched in the parent, invisible in the child) and **pexpect** (a module-level context).
- **Decision:** For each case, `corpus/real/<name>/` holds a **distilled reproduction** (≤ 120 lines that keep the failing pattern), a `PROVENANCE.md` (links, quoted upstream diff, what was simplified and why), and `expected.json` (which rules must fire). `scripts/fetch-corpus.sh` optionally clones the upstream pre-fix revisions. SC-5 is judged on the distilled cases. Running on the upstream checkouts is a stretch goal.
- **Licensing:** distilled reproductions are written fresh, not copied. Quotes in PROVENANCE are short and attributed.

<a id="adr-018"></a>
## ADR-018: Statistics in the stdlib; HTML report with t-strings
- **Decision:** `kiln.bench.stats` implements median, mean, stdev, a **percentile bootstrap 95% CI** (10,000 resamples, fixed seed) and a bootstrap CI for the ratio of medians, using only `statistics` and `random`. The K9 report renderer uses PEP 750 **t-strings** with our own HTML-escaping processor, and it runs on 3.14+. Charts are inline SVG that we generate ourselves.

<a id="adr-019"></a>
## ADR-019: Tooling: uv, ruff, mypy --strict, pytest + Hypothesis
- **Decision:**
  - `uv` manages interpreters and environments, and `uv.lock` is committed.
  - `ruff` handles lint and formatting, with the rule set in `pyproject.toml`, including `D` (docstrings) and `ANN` (annotations) for `src/`.
  - `mypy --strict` runs on `src/kiln`.
  - `pytest` with `pytest-timeout` (every test ≤ 30 s by default; integration tests are marked).
  - `hypothesis` for property tests.
  - Only one type checker, to keep things simple. Comparing type checkers is Cartographer C4's job, not Kiln's.

<a id="adr-020"></a>
## ADR-020: Version-specific facts are verified, not trusted
- **Context:** The research notes make claims about specific versions: 3.14.5 reverted the incremental GC, msgpack calls `gc.enable()`, Tachyon CLI flags, PyO3 support for `abi3t`, and so on. Some of these are recent, some come from secondary sources, and some could be wrong.
- **Decision:** Every milestone has a **Verification gates** section. Before building on a claim, the implementer checks it against the real interpreter or package, and records the outcome in the lab report's "Verified facts" table: claim, command run, observed output, ✅/❌. If a claim is false, the milestone follows its documented **fallback**, and `docs/research/errata.md` gets an entry.

<a id="adr-021"></a>
## ADR-021: Localhost-only, no auth, debug off by default
- **Decision:** Every listener binds to `127.0.0.1`, and anything else needs `allow_non_loopback = true`. There's no authentication. `debug.enabled` and `labs.enabled` default to `false`, because debug ops can dump memory and labs tasks can crash workers on purpose.

<a id="adr-022"></a>
## ADR-022: One experiment-record format for all labs
- **Decision:** Every measurement any lab takes is written as an `ExperimentRecord` JSON file under `results/<milestone>/<experiment>/<timestamp>-<interp>.json`, using the schema in [spec/10-measurement-methodology](spec/10-measurement-methodology.md). K9 consumes these records without any per-lab code. `results/` is git-ignored, except for `results/published/`, which holds the runs cited in lab reports.

<a id="adr-023"></a>
## ADR-023: Memory budget for experiments
- **Context:** WSL sees about 7.5 GiB, and Windows also needs RAM.
- **Decision:** No experiment may use more than **3 GiB** total. K3's preload graph defaults to 300 MiB with 4 workers. OOM labs run inside a **cgroup with `memory.max` = 512 MiB**, so the real kernel OOM killer fires without endangering the rest of WSL. The learner can raise WSL's memory in `%UserProfile%\.wslconfig`, and `docs/spec/07-environment.md` explains how.

<a id="adr-024"></a>
## ADR-024: Workers ignore SIGINT
- **Decision:** See [spec/04 §3.1](spec/04-supervisor.md#31-signals-in-workers). Ctrl-C reaches the whole foreground process group. Only the master reacts to it.

<a id="adr-025"></a>
## ADR-025: Git workflow: a branch per milestone, user sign-off to merge
- **Decision:**
  - Each milestone is developed on `milestone/kN-<slug>`, with one commit per step in the milestone's plan.
  - The implementer runs the full milestone Definition of Done ([standards/milestone-workflow.md](standards/milestone-workflow.md)) and then asks the learner to review.
  - The branch merges into `main` only after the learner signs off.
  - There's no remote yet. Pushing anywhere needs explicit approval.
  - Commit messages explain *why*, in plain English.

<a id="adr-026"></a>
## ADR-026: Documentation is part of "done" and is checked by a script
- **Decision:** A milestone isn't done until its documentation deliverables exist and `scripts/check_docs.py` passes. The checker covers module docstrings, required comment tags, link targets, glossary coverage of terms used in primers, and the required sections of every lab report. The standard is [standards/documentation.md](standards/documentation.md).

<a id="adr-027"></a>
## ADR-027: Add `coverage` to the dev dependencies (amends ADR-005)
- **Context:** [spec/09](spec/09-testing-strategy.md) sets a line-coverage target of at least 85% for `src/kiln`, excluding gated modules. Measuring that needs a tool.
- **Decision:** Add `coverage` (coverage.py) to the dev group. It's used only through `scripts/dev.sh test --cov`, and it never becomes a runtime dependency.

<a id="adr-028"></a>
## ADR-028: Forked children always end with `os._exit()`
- **Context:** A child created by `os.fork()` holds a copy of the parent's whole interpreter: its `atexit` handlers, buffered stdout, and `finally` blocks further up the stack. If the child returns normally, or calls `sys.exit()`, it runs the *parent's* cleanup a second time. That can flush duplicate output, delete the parent's pidfile, or run the final GC that destroys copy-on-write sharing.
- **Decision:** Every code path in a forked child (fork launcher, forkserver children) ends with `os._exit(code)`, inside a `try/finally` that wraps the whole child body. Spawned workers are fresh interpreters, so they exit normally.
- **Consequences:** K3's `worker.exit_via_os_exit` (Instagram's `atexit.register(os._exit, 0)` trick) changes behaviour **only for spawned workers**. Forked workers already skip the final GC because of this ADR. The K3 lab must demonstrate the difference. See [code-style §7](standards/code-style.md) and errata E-16.

<a id="adr-029"></a>
## ADR-029: Workers raise their own `oom_score_adj` to 500
- **Context:** When a cgroup runs out of memory, the kernel's OOM killer picks the process with the highest "badness" score. If it picked the **master**, nobody would be left to report the death, which is the exact failure SC-3 exists to prevent.
- **Decision:** Early in boot, every worker writes `500` to `/proc/self/oom_score_adj`. Raising your own score needs no privilege. If the write fails, it's logged at DEBUG and boot carries on. The master leaves its own score alone.
- **Consequences:** Under memory pressure, the kernel kills workers first. The K1 primer `cgroups-and-oom-killer.md` explains `oom_score_adj`.

<a id="adr-030"></a>
## ADR-030: `labs.hold_gil` fault task, and the `kiln.client` module
- **Context:** K1's heartbeat lab needs a worker that is truly hung: alive, but unable to run *any* Python thread. A pure-Python busy loop doesn't do that, because the interpreter switches threads every 5 ms. The CLI also needs a small blocking KP/1 client.
- **Decision:**
  - Add `labs.hold_gil(seconds, method)`. The primary method is `ctypes.PyDLL(None).sleep(...)`, which calls libc `sleep` with the GIL held. The fallback is a catastrophic-backtracking regex. It's defined in [spec/03 §7](spec/03-jobs-and-tasks.md#7-built-in-lab-tasks-kilnlabstasks) and K1 §5.9.
  - Add `kiln/client.py`, a blocking client that imports only the core layer and `config`.
- **Consequences:** On 3.15t, `hold_gil` doesn't stop other threads, because there is no GIL. That is itself a lesson, and K1 and K4 both point it out.

<a id="adr-031"></a>
## ADR-031: Master connection limits are constants, not config
- **Decision:** `MAX_CLIENT_OUTBUF = 8 MiB`, `ACCEPT_BATCH = 64` and `MAX_CLIENTS = 1024` are module constants in `master/connections.py`. Each has a `# WHY:` comment.
- **Why:** These limits protect the master from slow or abusive clients. Nobody needs to tune them for the labs, and every config key is one more thing to document, validate and test.
