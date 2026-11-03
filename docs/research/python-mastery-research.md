# Learning Python Through Real Engineering Problems

*Research notes and two project proposals. Revision 5, as of 2026-11-03.*

**Summary:** The goal is **learning first, novelty second**. The Python problems companies actually write about fall into two separate groups, and each gets its own project:

1. **Kiln, the runtime.** A pre-fork job server built from scratch. Each milestone reproduces a real production incident (Instagram's copy-on-write, the 3.14 GC revert, the forkserver breakages, blocked event loops, leaks, free-threading), measures it, then fixes it.
2. **Cartographer, the language and its tooling.** A toolkit that analyzes Python code. It covers import graphs, lazy imports, a rule engine, typing, t-string migration, dependency diffs and CI auditing.

The two projects share no code. Most milestones rebuild things that already exist, on purpose. Each one names a **reference implementation** to compare against *after* you've built your own. Two milestones are novel as far as my searches went (marked ★): a fork-assumption checker (Kiln K2) and an f-string → t-string migration with a processor verifier (Cartographer C5).

Both target **Python 3.15** (released 9 Oct 2026, now 3.15.2) and are also tested on 3.13 and 3.14.

### How to work through each milestone
1. Read the incident in §2 and write down what you *expect* to happen.
2. Reproduce the problem, measure it, then build your own fix or tool **without reading the reference's code**.
3. Then read the reference implementation (gunicorn, import-linter, ruff, zizmor…) and write a short note: *what they did differently and why*. This step is where most of the learning happens.

---

## 00. Prior art: what already exists (use it as reference implementations)

### Existing tools by topic

| Topic | Existing tools | Reference for |
|---|---|---|
| Pre-fork server | gunicorn; [py-prefork-server](https://github.com/crustymonkey/py-prefork-server); [Python port of "Unicorn is Unix"](https://gist.github.com/StoneMoe/0122c490416e648b773303b4d7d1e256) | K1 |
| Event-loop lag watchdog | [aiodebug](https://pypi.python.org/project/aiodebug/), [loopmon](https://pypi.org/project/loopmon), [loopsentry](https://pypi.org/project/loopsentry/), [aiocop](https://pypi.org/project/aiocop/0.1.3/), pyleak, `python -m asyncio ps` | K5 |
| GC pause measurement | [gcmon](https://pypi.org/project/gcmon), [pygcprofiler](https://pypi.org/project/pygcprofiler/) | K7 |
| Benchmarks across Python versions | [asv](https://asv.readthedocs.io/en/v0.5/using.html) (several interpreters, `peakmem_*`), pytest-benchmem | K9 |
| Free-threading safety | [pytest-run-parallel](https://sources.debian.org/src/python-pytest-run-parallel/0.8.2-1/PKG-INFO) (warns when the GIL is re-enabled); [parallel-python-tracker](https://github.com/vitawasalreadytaken/parallel-python-tracker) (free-threading + subinterpreters) | K4 |
| Pure-Python race detector | [PyVFT](https://pypi.org/project/pyvft/), [threadcheck](https://pypi.org/project/threadcheck/), [PyTsan](https://utoronto.scholaris.ca/items/501945a7-4f46-4798-bb08-4ef2ef0ded2f/full), [interleave-test](https://pypi.org/project/interleave-test/), [raceguard](https://pypi.org/project/raceguard/) | K4 |
| Attaching to live processes via PEP 768 | [Peeka](https://github.com/wwulfric/peeka), debug wand, `pdb -p` | K6 |
| Differential profiles | Built into Tachyon (`--diff-flamegraph`, 3.15) | K6 |
| Import graph / cycles | grimp, [import-linter](https://import-linter.readthedocs.io/en/latest/ui/), tach, pydeps | C1 |
| Lazy-import advisor | [importcost](https://pypi.org/project/importcost/), [lazysafe](https://pypi.org/project/lazysafe/), [flake8-lazy](https://pypi.org/project/flake8-lazy/), Lifeguard, lazy-fix | C2 |
| Type-checker comparison | [Pytifex](https://pypi.org/project/pytifex/), the typing conformance suite | C4 |
| Single-version package audit | [GuardDog](https://github.com/datadog/guarddog), pip-audit, [Packj](https://github.com/ossillate-inc/packj), [OpenSSF package-analysis](https://pkg.go.dev/github.com/ossf/package-analysis) (sandboxed runs, tracks behavior over time) | C6 |
| **Comparing what two versions of a package do (Molt)** | [Upgrade X-Ray](https://apify.com/cybernative-ai/upgrade-xray.md) (PyPI + npm, static diff: install hooks, `.pth`, network/shell/eval, 998/998 on its holdout set), [Socket PR comments](https://socket.dev/blog/introducing-dependency-overview-comments); research: [CodeQL diff](https://www.plai.ifi.lmu.de/publications/scored23-diff-codeql.pdf), Amalfi, [LastPyMile](https://www.researchgate.net/publication/352546142_LastPyMile_Identifying_the_Discrepancy_between_Sources_and_Packages), Cerebro, [version-aware ML (2026)](https://www.nature.com/articles/s41598-026-71750-5) | C6 |
| Provenance changes between releases | PyPI shows "Change of provenance" and "Loss of provenance" itself; [trustcheck](https://github.com/Halfblood-Prince/trustcheck) flags publisher and workflow drift; [lockscan #13](https://github.com/lachaib/lockscan/issues/13) | C6 |
| CI workflow auditor | zizmor, poutine, octoscan, actionlint | C7 |
| Wheel rebuild verification | macaron, oss-rebuild, daleq4py ([arXiv 2607.21888](https://arxiv.org/html/2607.21888)) | (not used) |
| PEP 649 annotation breakage | Ruff's `flake8-type-checking` with `runtime-evaluated-base-classes` covers most of it | C4 |

### Where my searches found nothing (the ★ milestones)

| Gap | Evidence it's open | Evidence it matters |
|---|---|---|
| **Fork-assumption linter + migration measurement** | No linter found. [vLLM's docs](https://docs.vllm.ai/en/latest/design/multiprocessing.html) say even detecting a `__main__` guard "does not appear to be straightforward", so they fall back to runtime checks | 3.14 changed the Linux default to forkserver. Broken in the wild: [MIT krb5 #9197](https://mailman.mit.edu/pipermail/krb5-bugs/2026-February/013527.html), [oslo.concurrency](https://ftp.fau.de/gentoo-portage/dev-python/oslo-concurrency/files/0001-Fix-multiprocessing-tests-under-Python-3.14.patch) (state not inherited, local functions as targets), [FSL pyfeeds](https://git.fmrib.ox.ac.uk/fsl/pyfeeds/-/merge_requests/46) (logging handlers relied on fork) |
| **f-string → t-string migration + processor verification** | No codemod (only the reverse, [t2string](https://pypi.org/project/t2string/)). No t-string taint rule in ruff or Bandit. [Semgrep can't parse t-strings](https://github.com/semgrep/semgrep/issues/12030) yet. The PEP mentions linters only as future work | PEP 750's stated goal is preventing injection. Ruff `S608` only *flags* SQL built from strings and can't fix it. Libraries now exist to target: [sql-tstring, tdom, tstr](https://the-gigi.github.io/gigi-zone/posts/2026/04/pep-talk-02-pep-750/) |

**How to use this list:** these projects are reference implementations, not competition. Build your version first, then read theirs. The two ★ milestones (K2, C5) are the only pieces where my searches found nothing equivalent, and I can't rule out private or brand-new tools.

---

## 0. What changed in revision 2

| Area | Revision 1 said | Corrected to | Source |
|---|---|---|---|
| Python version | Lazy imports "accepted for 3.15" | 3.15 has **shipped**: lazy imports, `frozendict`, UTF-8 by default, Tachyon sampling profiler, `abi3t`. Both projects now target 3.15 | [3.15 What's New](https://docs.python.org/3.15/whatsnew/3.15.html), [release page](https://www.python.org/downloads/release/python-3150/) |
| Instagram GC | Fix was `gc.freeze()`, numbers unclear | Two posts. **Post 1**: disabling GC gave ~10% capacity and ~8 GB RAM saved per machine. It needed `gc.set_threshold(0)` because `msgpack` silently re-enabled the GC, plus an `atexit` trick to skip the final GC at shutdown. **Post 2**: `gc.freeze()`, added in 3.7 | [Arpit Bhayani summary](https://arpitbhayani.me/videos/disabling-python-garbage-collection-instagram), [gc docs](https://docs.python.org/3/library/gc.html) |
| 3.14 GC revert | "Reverted in 3.14.5" | Confirmed, with detail: 3.14.5 shipped 10 May 2026. Peak RSS was up to 2.7x with the incremental GC. A real Django case: `migrate` OOM-killed on Heroku | [3.14 What's New](https://docs.python.org/3/whatsnew/3.14.html), [Adam Johnson](https://adamj.eu/tech/2026/04/20/django-python-3.14-incremental-gc/), [discuss.python.org](https://discuss.python.org/t/reverting-the-incremental-gc-in-python-3-14-and-3-15/107014) |
| Free-threading readiness | "~51% of the top-360 packages" (third-party) | **Removed**, couldn't verify. Use the live [hugovk tracker](https://hugovk.dev/free-threaded-wheels/) (top 360 packages that include compiled code). Late-2025 snapshots showed about 128–130 of 360 | [psutil blog](https://psutil.readthedocs.io/latest/blog/2025/wheels-for-free-threaded-python.html) |
| Free-threading overhead | "~5–10%" | 3.14 HOWTO: about 5–10%. The current HOWTO: about **1% on macOS ARM and about 8% on x86-64 Linux**. Importing a C extension that doesn't declare free-threading support **turns the GIL back on** | [free-threading HOWTO](https://docs.python.org/3.15/howto/free-threading-python.html) |
| Snyk leak | "a third-party HTTP client" | The service was **Fugue's "reflector"**, and the leak was in **`requests`**, used through an internal storage library. Switching to `urllib` kept memory flat at about 8.5–9.3% instead of climbing from 8% to 20% | [Snyk](https://snyk.io/blog/diagnosing-and-fixing-memory-leaks-in-python/) |
| Rust rewrite numbers | "609 ms → 23 ms → 2.9 ms" | 609/23 **weren't on the source page**. The verified figure is **293 ms → 2.9 ms per iteration (~100x)**, profiled with `py-spy --native` | [engineered.at](https://engineered.at/articles/making-python-100x-faster-with-less-than-100-lines-of-rust) |
| Ultralytics attack | "cache poisoning + leftover token" | Full chain: `pull_request_target`, then **a shell injection through the git branch name** in a composite action, then token theft, then (likely) a poisoned pip cache, then the publishing workflow's actor check was removed, then Trusted Publishing shipped releases with *valid attestations*. A leftover token shipped two more with none | [Woodruff (zizmor author)](https://blog.yossarian.net/2024/12/06/zizmor-ultralytics-injection) |
| Type checkers | "Pyrefly alpha" | **Pyrefly 1.0 was released 12 May 2026**. It's the default checker for Instagram and is used by PyTorch and JAX. Astral's **ty** is still beta. **OpenAI announced it is acquiring Astral** (uv, ruff, ty) on 19 Mar 2026; the tools remain open source | [Pyrefly 1.0](https://github.com/facebook/pyrefly/releases/tag/1.0.0), [JetBrains](https://blog.jetbrains.com/pycharm/2026/03/openai-acquires-astral-what-it-means-for-pycharm-users/) |
| Back Market Celery | Cited as fact | The page returned 403, so this is now **marked as a secondary summary** | — |
| **Project design** | Kiln assumed `fork`, `/proc`, Memray | **You're on Windows.** None of those exist on Windows, so Kiln runs in **WSL2**. Cartographer runs natively on Windows. See §6 | [Memray platforms](https://bloomberg.github.io/memray/supported_environments.html) |

---

## 1. Method and source quality

I used first-party engineering posts, PEPs and the official CPython docs as primary sources. Secondary write-ups are only used when the primary page was unreachable, and they're marked. In revision 2 I removed claims I couldn't verify:

- The "51% free-threaded" figure.
- The Dropbox and Reddit free-threading claims.
- The 609/23 ms timings.

Instagram's own blog still refuses connections, so the Instagram numbers come from a detailed secondary summary that matches the official `gc` docs.

---

## 2. Field notes: what went wrong at real companies

### 2.1 Instagram: the GC breaks copy-on-write memory sharing
- **Setup:** A master process loads Django, then forks workers that should share memory pages through copy-on-write (CoW).
- **Problem:** Every object access changes its refcount, which is a write. Every GC pass also writes `gc_refs` into tracked objects. Instagram called this "copy-on-read." Per worker, shared memory fell from about 250 MB to 140 MB within seconds of serving requests.
- **Fix 1, disable the GC:**
  - `gc.disable()` wasn't enough. GDB showed that **`msgpack` called `gc.enable()`** when it loaded, so they used `gc.set_threshold(0)` instead.
  - Interpreter shutdown runs one final GC pass. They skipped it with `atexit.register(os._exit, 0)`, registered first so that it runs *last* (atexit runs in LIFO order).
  - Result: about **10% more capacity** and about **8 GB RAM saved per machine**. Shared memory per worker went from about 140 MB to 225 MB.
- **Fix 2, `gc.freeze()` (Python 3.7):** It moves all existing objects into a permanent generation that the GC never scans. The docs give the recipe: *`gc.disable()` early in the parent, `gc.freeze()` right before `fork()`, `gc.enable()` early in the child.*
- **Later:** Meta's Cinder fork added immortal objects, which went upstream as **PEP 683 in 3.12**.
- **What to learn:** refcounting, generational GC, `fork()` and CoW, how `atexit` ordering works, and the fact that any library can change global interpreter state.

### 2.2 Python 3.14's incremental GC was reverted (2026)
- 3.14.0–3.14.4 shipped an incremental collector. Pauses were roughly 10x shorter, but in one core developer's test **peak RSS was 2.7x higher** when the program created a lot of cyclic garbage.
- **Real case (Adam Johnson, Django):** `manage.py migrate` was OOM-killed on a 512 MB Heroku dyno, climbing to 1,033 MB before SIGKILL. The cause was temporary model classes in reference cycles (model ↔ field). The incremental GC couldn't keep up. The workaround was calling `gc.collect()` after each migration.
- **3.14.5 (10 May 2026)** and 3.15 went back to the 3.13 generational GC. Any future incremental GC (PEP 848) has to go through a formal PEP.
- **What to learn:** the trade-off between pause time and peak memory, reference cycles in frameworks, and `gc.get_stats`, `gc.set_threshold` and `gc.callbacks`.

### 2.3 Dropbox: type-checking 4 million lines
- They went from about 420K annotated lines (end of 2016) to almost 4M in about three years.
- **Pain points:** Clean runs took over 15 minutes. A **"tangle" of several hundred modules in import cycles** made incremental runs take about a minute.
- **Fixes:** a remote cache, the **mypy daemon** (most incremental runs take seconds) and **mypyc** (about 4x faster clean runs).
- **Lessons:**
  - Type core libraries first, or `Any` leaks everywhere.
  - Owner-driven annotation guided by coverage reports beat automatic runtime collection (PyAnnotate).

### 2.4 Meta: Pyre → Pyrefly 1.0; the tooling moves to Rust
- Pyrefly is written in Rust and checks about 1.8M lines/sec. It reached **1.0 on 12 May 2026** with over 90% typing-spec conformance. It's the default checker for Instagram, and PyTorch and JAX use it.
- Astral's ruff, uv and ty (beta) follow the same pattern. OpenAI announced an Astral acquisition in Mar 2026, and the tools stay open source.
- **What to learn:** how checkers work (binding, inference, narrowing, incremental invalidation) and why different checkers disagree on the same code.

### 2.5 Startup time and lazy imports: PEP 810, shipped in 3.15
- The PEP reports **50–70% faster startup** and **30–40% memory savings** for CLIs and services. It cites Meta, Hudson River Trading and PySide.
- **Syntax:** `lazy import json`, `lazy from json import dumps`. These are allowed at **module level only**; inside functions, class bodies, `try` blocks or with `*` they are a SyntaxError. `with` blocks are allowed. `__lazy_modules__ = [...]` gives you compatibility with older versions. There are global controls too: `-X lazy_imports`, `PYTHON_LAZY_IMPORTS`, `sys.set_lazy_imports_filter()`.
- **Semantics:** The name is bound to a proxy. The module is imported on first use, using the import state *at that moment*. A failed import is retried on the next access.
- **Gotchas:**
  - Errors surface at first use instead of at the import line.
  - Plugins that register themselves on import may never register.
  - `lazy from m import X` still runs all of `m`.
- **Related in 3.15:** `import` lines in `.pth` files are deprecated (PEP 829), and frame pointers are on by default (PEP 831), which helps profilers.

### 2.6 asyncio: blocking calls on the event loop
- A PDF-ingestion service called sync code (boto3, `requests`, ORMs, big JSON parses) inside `async def`. Moving those calls to `asyncio.to_thread()` gave **about 31–36% more throughput and about 24–27% lower p99**.
- **Tools:**
  - `pyleak` detects blocking calls in tests.
  - `python -m asyncio ps PID` and `pstree PID` (**3.14+**) read another process's tasks *without running code in it*.
  - `asyncio.capture_call_graph()` / `print_call_graph()` (3.14+) show the same thing from inside the process.
- 3.14 also lets multiple event loops run in parallel across threads on the free-threaded build.

### 2.7 Celery and pre-fork workers: memory multiplied by concurrency *(secondary sources)*
- **Back Market:** containers were killed at their memory limit and Kubernetes didn't report an OOM event. "Worker lost" was the only signal.
- **Wikimedia:** each forked child still grew to about 310 MB.
- **SquadStack:** the gevent pool caused deadlocks because some code wasn't thread-safe.
- **Fixes:** `max-tasks-per-child`, `max-memory-per-child`, and choosing the pool to fit the workload.

### 2.8 Memory leaks in long-running services
- **Snyk/Fugue:** The "reflector" service leaked through `requests`. They diffed `tracemalloc` snapshots on each loop and filtered by package. The fix was the stdlib `urllib`.
- **Bloomberg Memray** traces native (C/C++) allocations too. It runs on **Linux and macOS only**, so on Windows use WSL.
- **New debugging tools:**
  - **3.14:** `sys.remote_exec()` and `python -m pdb -p PID` (PEP 768).
  - **3.15:** the **Tachyon** sampling profiler (PEP 799): `python -m profiling.sampling attach PID`. It samples at up to 1 MHz, has wall/cpu/gil/exception modes, and produces flamegraphs. It works on Windows with admin rights or SeDebugPrivilege. The profiler and the target must be the *same minor version and the same build type*.

### 2.9 Concurrency is changing: free-threading, subinterpreters, forkserver
- **Free-threaded build:**
  - Officially supported since 3.14 (PEP 779).
  - Overhead is now about 1% on macOS ARM and about 8% on x86-64 Linux.
  - Importing a C extension that doesn't declare free-threading support turns the GIL back on.
  - Check with `sys._is_gil_enabled()`, and force it with `PYTHON_GIL=0`.
  - Limitations: sharing one iterator across threads isn't safe, and touching another thread's `frame.f_locals` can crash.
- **3.15:** a stable ABI for free-threaded builds (`abi3t`, PEP 803).
- **Subinterpreters (3.14):** `concurrent.interpreters` and `InterpreterPoolExecutor`.
- **multiprocessing (3.14):** On Linux the default start method is now **`forkserver`** instead of `fork`, so some code now hits `NameError` or pickling errors. Windows and macOS stay on `spawn`.

### 2.10 Native speed: Rust and C extensions
- **Pydantic v2:** moved its core to Rust (pydantic-core, PyO3). The maintainers report 5–50x speedups, about 17x for a typical model.
- **Tea and Bits case:** they profiled with `py-spy --native` and rewrote one hot function in Rust, going from **293 ms to 2.9 ms per iteration**. The rest of the gain came from cutting allocations at the language boundary.
- **The catch:** many small calls across the boundary cancel out the speedup.

### 2.11 Supply chain: the Ultralytics attack and PyPI in 2025
- **Ultralytics (Dec 2024):**
  1. A `pull_request_target` workflow called a composite action that ran `git pull origin ${{ head_ref }}` without quoting. An attacker opened a PR whose **branch name contained `$(...)`**, which ran a script.
  2. The script dumped runner memory and stole tokens, including the cache URL. The pip cache was most likely poisoned.
  3. A commit removed the `github.actor` check on the publish workflow.
  4. Trusted Publishing then shipped 8.3.41 and 8.3.42 with **valid Sigstore attestations**.
  5. 8.3.45 and 8.3.46 went out with a leftover API token and had **no attestations**.
  - **Lesson:** attestations prove *where* a package was built, not that it's safe.
- **PyPI in 2025:**
  - Incidents: a phishing campaign and tokens stolen from GitHub Actions.
  - Response: over 2,000 malware reports, 66% handled within 4 hours.
  - Adoption: over 50,000 projects use Trusted Publishing, and 17% of uploads carry attestations.
  - New defenses: typosquat checks, expired-domain checks, ZIP-upload hardening.

### 2.12 The language keeps changing (3.14 → 3.15)
- **3.14:**
  - t-strings (PEP 750) return a `Template` that keeps fixed text and values apart, so values can be escaped to prevent injection.
  - Deferred annotations with `annotationlib` (PEP 649/749).
  - `except A, B:` without parentheses (PEP 758).
  - A SyntaxWarning for control flow that leaves `finally` (PEP 765).
  - `compression.zstd`.
- **3.15:**
  - `lazy` imports (PEP 810).
  - **`frozendict`** (PEP 814) and a built-in `sentinel` type (PEP 661).
  - **`*`/`**` unpacking in comprehensions** (PEP 798).
  - **UTF-8 by default** (PEP 686), which especially matters on Windows.
  - `math.integer` (PEP 791).
  - Typing: TypedDict `extra_items` (PEP 728), `TypeForm` (PEP 747).
  - The JIT is about 7–8% faster on x86-64 Linux.
  - Better "did you mean" errors.

---

## 3. The knowledge map these problems add up to

| Area | Topics you must be able to explain cold |
|---|---|
| Data model | dunder protocols, descriptors (`property`, bound methods, `__slots__`), MRO/`super()`, metaclasses vs `__init_subclass__`, `__getattr__` vs `__getattribute__`, hash/eq contract, iterators/generators, context managers, decorators with `ParamSpec` |
| Runtime internals | bytecode (`dis`), frames, refcounting + generational GC, immortal objects, interning, specializing interpreter, JIT status, `atexit`/finalization order |
| Import system | finders/loaders, `sys.modules`, circular imports, import-time side effects, **lazy imports**, `.pth`/`.start` files, entry points |
| Concurrency | GIL vs free-threading (and what re-enables the GIL), races, asyncio internals + cancellation + `TaskGroup`, start methods (fork/forkserver/spawn), subinterpreters |
| Memory & perf | `tracemalloc`, Memray, py-spy, **Tachyon**, `cProfile`/`profiling.tracing`, pyperf, CoW + `gc.freeze`, `__slots__` |
| Typing | gradual typing, PEP 695 generics, Protocols, TypedDict, overloads, narrowing, variance, stubs; mypy vs pyright vs pyrefly vs ty |
| Native code | C API, buffer protocol, PyO3/maturin, mypyc, stable ABI (`abi3`, `abi3t`), boundary cost |
| Packaging & security | `pyproject.toml`, wheels vs sdists, uv + lockfiles, hash pinning, Trusted Publishing, attestations (and their limits), CI injection, typosquatting, t-strings, `pickle`/`eval` |
| Testing & ops | pytest, Hypothesis, remote debugging (PEP 768), `asyncio ps`, structured logging |

---

## 4. Project A: **Kiln**, a pre-fork job server built from scratch

**Goal:** Build a small job and request server in the style of gunicorn plus Celery. Every milestone reproduces a real incident, measures it, then fixes it.
**Runs on:** WSL2 Ubuntu (`fork`, `/proc`, Memray). Use `uv python install 3.13 3.14.4 3.14.5 3.15 3.15t`.

| # | Milestone | Incident (§2) | You'll master | Reference to compare after |
|---|---|---|---|---|
| K1 | Master/worker supervisor: fork, signals, graceful reload, worker recycling (`max_jobs`, `max_rss`), detecting OOM-killed workers (SIGKILL vs exit codes) | Celery OOM (2.7) | `os.fork`, `signal`, `selectors`, process reaping | gunicorn arbiter; Tomayko's "Unicorn is Unix" |
| K2 | Pluggable start methods (fork, forkserver, spawn), plus **★ a fork-assumption checker**: static rules for lambdas or nested targets, arguments that can't be pickled, globals changed after import, missing `__main__` guards. Confirm each finding by running under all three methods. Calibrate on the pre-fix commits of krb5, oslo.concurrency and FSL pyfeeds | forkserver default (2.9) | pickling (`__reduce__`, `copyreg`), `__main__` semantics, `ast`/`symtable` | `multiprocessing` source; vLLM's runtime checks |
| K3 | Copy-on-write lab: measure PSS/USS per worker with no change, `gc.disable()`, `set_threshold(0)`, and the docs' `disable → freeze → enable` recipe. Catch `msgpack` re-enabling the GC with a `gc.callbacks` hook. Add the `atexit(os._exit)` shutdown trick | Instagram (2.1) | refcount vs GC writes, finalization order, `/proc/smaps_rollup` | Bolna / SSD Nodes guides; gunicorn `--preload` |
| K4 | Concurrency backends: thread pool on **3.15t**, `concurrent.interpreters` pool, asyncio. Benchmark CPU- vs IO-bound jobs; detect which extension re-enables the GIL; plant a data race in your own code | Free-threading (2.9) | GIL semantics, `sys._is_gil_enabled()`, thread safety of `dict`/`list`/iterators | pytest-run-parallel; PyVFT (race detector) |
| K5 | Async HTTP front end with backpressure, plus an **event-loop lag watchdog** that dumps the stack of whatever is blocking. Inspect it live with `python -m asyncio ps/pstree` | Blocked event loops (2.6) | selectors, Tasks/Futures, `TaskGroup`, cancellation, `to_thread` | aiodebug, loopmon; uvicorn |
| K6 | Leak lab: planted leaks (`__del__` cycles, unbounded `lru_cache`, closures holding large objects), a `/debug/tracemalloc` endpoint; diagnose them with Memray, **Tachyon** `attach` and `pdb -p` | Snyk/Fugue, Bloomberg (2.8) | `tracemalloc` diffs, `weakref`, sampling vs tracing profilers, PEP 768 | Memray docs; Tachyon docs |
| K7 | GC regression replay: a cycle-heavy workload modeled on Django's model↔field cycles on 3.14.4 vs 3.14.5 vs 3.15; a GC pause histogram from `gc.callbacks` | 3.14 GC revert (2.2) | generational vs incremental GC, `gc.get_stats`, thresholds | pygcprofiler, gcmon; Adam Johnson's post |
| K8 | Rust hot path: a PyO3 module for payload validation that declares free-threading support; measure the cost of crossing the language boundary; build with maturin | Pydantic v2, hot-path rewrites (2.10) | profile first, C API concepts, buffer protocol, `abi3`/`abi3t` | pydantic-core |
| K9 | Matrix report: run K3–K8 across interpreters and builds, with repeated runs and confidence intervals, rendered as HTML with t-strings | All of the above | experiment design, statistics, `string.templatelib` | asv |

**Done when:** It handles 10K jobs under load, reloads with zero dropped jobs, every milestone has a before/after write-up, and K2 catches all three real breakages.

---

## 5. Project B: **Cartographer**, a Python code-intelligence and safety toolkit

**Goal:** Build a CLI and library that reads *someone else's* repo and reports on its structure, startup cost, typing health, injection risks, dependency changes and CI risks.
**Runs on:** native Windows. C6's dynamic runs happen only in a throwaway container with no network.

| # | Milestone | Incident (§2) | You'll master | Reference to compare after |
|---|---|---|---|---|
| C1 | Import graph: parse with `ast`, resolve modules the way `importlib` does, find cycles and tangles (strongly connected components), suggest which edges to cut | Dropbox's tangle (2.3) | the import system, `sys.modules`, graph algorithms | grimp / import-linter, tach |
| C2 | Startup profiler and lazy-import advisor: parse `-X importtime`, find import-time side effects, suggest `lazy import` candidates, then measure the real win on 3.15 | PEP 810 (2.5) | module execution, `.pth`/`.start` files, side-effect detection | importcost, lazysafe |
| C3 | Rule engine with an entry-point plugin API. Rules: blocking call inside `async def`, control flow leaving `finally`, mutable defaults | 2.6, 2.12 | visitors, decorators, descriptors, `__init_subclass__` registries, entry points | ruff ASYNC rules, flake8-async, flake8's plugin API |
| C4 | Typing: annotation coverage per module and where `Any` leaks in. Run Cartographer itself under mypy, pyright, pyrefly 1.0 and ty and write up where they disagree. Compile hot modules with mypyc | Dropbox, Meta (2.3, 2.4) | generics (PEP 695), Protocols, ParamSpec, overloads, TypeForm, `annotationlib` (PEP 649) | mypy reports; Pytifex; the typing conformance suite |
| C5 | **★ t-string migration:** find f-strings, `%` and `.format()` flowing into SQL, shell or HTML calls (taint flow); a LibCST codemod that rewrites them to t-strings with the right adapter (DB-API `paramstyle`, sql-tstring, tdom); a Hypothesis **processor verifier** that checks interpolated data never changes the parsed structure | PEP 750 (2.12) | `tokenize`, string grammar, `string.templatelib`, LiteralString (PEP 675), dataflow | ruff S608 (finds the problem, doesn't fix it); sql-tstring, tdom |
| C6 | Dependency upgrade diff: for `pkg 1.4 → 1.5`, build a capability manifest (static `ast` + dynamic `sys.addaudithook` in a sandbox) and check provenance (attestation present, same publisher). Test against an Ultralytics-style replica | Ultralytics, PyPI 2025 (2.11) | wheels/sdists, `zipfile`/`tarfile` safety, audit events (PEP 578), the PyPI Integrity API, async fan-out | Upgrade X-Ray, GuardDog, trustcheck |
| C7 | CI workflow auditor: parse `.github/workflows` and composite actions; flag `pull_request_target`, unquoted `${{ github.head_ref }}` in `run:`, shared caches in release jobs, missing environment gates | Ultralytics (2.11) | YAML parsing, data-flow rules | zizmor |
| C8 | Ship: uv, pytest + Hypothesis, wheels for 3.13–3.15t, Trusted Publishing with attestations, Cartographer running on its own CI | Packaging practice (2.11) | build backends, publishing, versioning | any well-run PyPA project |

**Done when:** Pointed at a real mid-size repo, it surfaces at least one real cycle, one startup win, one injection-risky sink with a working rewrite, and one dependency or CI risk.

---

## 6. Environment setup (Windows)

- **Kiln:** WSL2 Ubuntu. Tachyon, `asyncio ps` and `pdb -p` need `ptrace_scope=0` or `CAP_SYS_PTRACE`.
- **Cartographer:**
  - Native Windows with `uv python install 3.13 3.14 3.15 3.15t`.
  - C6's dynamic tracing runs **only** in a disposable container with no network.
  - Keep your own scripts outside the unpack directories.
- Profilers must match the target's minor version and build type (GIL vs free-threaded). Profiling another process on Windows needs admin rights or SeDebugPrivilege.

---

## 7. Coverage check

| Area | Kiln | Cartographer |
|---|---|---|
| Data model & OOP (descriptors, metaclasses, protocols) | partial (K2 `__reduce__`) | **primary** (C3, C4, C5) |
| Runtime internals (refcount, GC, finalization) | **primary** (K3, K7) | – |
| Import system / lazy imports | partial (K2 `__main__`) | **primary** (C1, C2) |
| Processes, signals, start methods | **primary** (K1, K2) | – |
| Threads / GIL / free-threading / subinterpreters | **primary** (K4) | – |
| asyncio | **primary** (K5) | secondary (C6 fan-out) |
| Memory & profiling | **primary** (K3, K6) | secondary (C2) |
| Static analysis (`ast`, `symtable`, dataflow) | secondary (K2) | **primary** |
| Typing | secondary | **primary** (C4, C5) |
| Strings, grammar, t-strings | secondary (K9) | **primary** (C5) |
| Native extensions | **primary** (K8) | secondary (mypyc) |
| Packaging & supply chain | – | **primary** (C6–C8) |
| Security (injection, audit hooks, CI) | – | **primary** (C5–C7) |
| Testing & statistics | **primary** (K9) | **primary** (Hypothesis in C5, C8) |

Every row of the knowledge map in §3 is now covered by at least one milestone.

**Suggested order:** **Cartographer first.** It runs on Windows and covers the syntax and language surface that most questions touch. **Kiln second**, for runtime depth. If you want variety, alternate between the two: odd weeks on Cartographer, even weeks on Kiln.

---

## 8. Sources

**Engineering posts**
- [Instagram: Dismissing Python GC](https://instagram-engineering.com/dismissing-python-garbage-collection-at-instagram-4dca40b29172) (unreachable; read via [Arpit Bhayani's summary](https://arpitbhayani.me/videos/disabling-python-garbage-collection-instagram))
- [Instagram: Copy-on-write friendly Python GC](https://instagram-engineering.com/copy-on-write-friendly-python-garbage-collection-ad6ed5233ddf) (unreachable)
- [Dropbox: Type checking 4 million lines of Python](https://dropbox.tech/application/our-journey-to-type-checking-4-million-lines-of-python)
- [Meta: Introducing Pyrefly](https://engineering.fb.com/04/2025/13/developer-tools/introducing-pyrefly-a-new-type-checker-and-ide-experience-for-python/) · [Pyrefly 1.0 release](https://github.com/facebook/pyrefly/releases/tag/1.0.0)
- [Adam Johnson: Django and 3.14's incremental GC](https://adamj.eu/tech/2026/04/20/django-python-3.14-incremental-gc/)
- [Snyk: Diagnosing and fixing memory leaks in Python](https://snyk.io/blog/diagnosing-and-fixing-memory-leaks-in-python/)
- [Detecting event loop blocking in asyncio](https://deepankarm.github.io/posts/detecting-event-loop-blocking-in-asyncio/)
- [Making Python 100x faster with <100 lines of Rust](https://engineered.at/articles/making-python-100x-faster-with-less-than-100-lines-of-rust)
- [Pydantic V2 plan](https://pydantic.dev/articles/pydantic-v2)
- [Back Market: Celery in Kubernetes](https://engineering.backmarket.com/a-story-of-kubernetes-celery-resource-and-a-rabbit-ec2ef9e37e9f) (403; secondary) · [SquadStack: Celery in production](https://medium.com/squad-engineering/celery-in-production-three-more-years-of-fixing-bugs-2ee462cef39f) · [Wikimedia T182350](https://phabricator.wikimedia.org/T182350)
- [Bloomberg Memray (InfoQ)](https://www.infoq.com/news/2022/04/bloomberg-memray-python-profiler) · [Memray supported environments](https://bloomberg.github.io/memray/supported_environments.html)

**Security**
- [William Woodruff: Ultralytics injection analysis](https://blog.yossarian.net/2024/12/06/zizmor-ultralytics-injection)
- [PyPI: Ultralytics attack analysis](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)
- [PyPI in 2025: Year in Review](https://blog.pypi.org/posts/2025-12-31-pypi-2025-in-review)

**Official docs / PEPs / ecosystem**
- [What's New in 3.15](https://docs.python.org/3.15/whatsnew/3.15.html) · [3.15.0 release](https://www.python.org/downloads/release/python-3150/)
- [What's New in 3.14](https://docs.python.org/3/whatsnew/3.14.html) · [Reverting the incremental GC (discuss)](https://discuss.python.org/t/reverting-the-incremental-gc-in-python-3-14-and-3-15/107014)
- [gc module docs](https://docs.python.org/3/library/gc.html)
- [PEP 810: Explicit lazy imports](https://peps.python.org/810)
- [Free-threading HOWTO](https://docs.python.org/3.15/howto/free-threading-python.html) · [Free-threaded wheels tracker](https://hugovk.dev/free-threaded-wheels/) · [psutil FT wheels post](https://psutil.readthedocs.io/latest/blog/2025/wheels-for-free-threaded-python.html)
- [asyncio CLI introspection tools](https://docs.python.org/3/library/asyncio-tools.html) · [asyncio call graph](https://docs.python.org/3/library/asyncio-graph.html)
- [profiling.sampling (Tachyon)](https://docs.python.org/3.15/library/profiling.sampling.html)
- [JetBrains: OpenAI acquires Astral](https://blog.jetbrains.com/pycharm/2026/03/openai-acquires-astral-what-it-means-for-pycharm-users/)
- [PyCon DE 2026: Are we free-threaded ready?](https://pycon.de/archive/2026/talks/are-we-free-threaded-ready-looking-at-where-free-threaded-python-fails/)

**Prior-art check (revision 3)**
- [Pytifex on PyPI](https://pypi.org/project/pytifex/) · [lazysafe on PyPI](https://pypi.org/project/lazysafe/) · [importcost on PyPI](https://pypi.org/project/importcost/)
- [GuardDog](https://github.com/datadog/guarddog) · [Packj](https://github.com/ossillate-inc/packj) · [Datadog malicious packages dataset](https://redirect.github.com/DataDog/malicious-software-packages-dataset)
- [No Snake Oil: Verifying Python Package Builds (arXiv 2607.21888)](https://arxiv.org/html/2607.21888)
- [PEP 578: Runtime Audit Hooks](https://peps.python.org/pep-0578/)
- [Ruff rules (ASYNC)](https://docs.astral.sh/ruff/rules/) · [GitHub Actions scanner comparison](https://www.themoonlight.io/review/unpacking-security-scanners-for-github-actions-workflows)
- [CPython Memory Insights](https://memory.python.org/) · [pytest-benchmem](https://pypi.org/project/pytest-benchmem/)


**Prior-art check (revision 4)**
- [Upgrade X-Ray](https://apify.com/cybernative-ai/upgrade-xray.md) · [Socket dependency overview](https://socket.dev/blog/introducing-dependency-overview-comments) · [OpenSSF package-analysis](https://pkg.go.dev/github.com/ossf/package-analysis) · [trustcheck](https://github.com/Halfblood-Prince/trustcheck)
- [asv usage](https://asv.readthedocs.io/en/v0.5/using.html) · [pytest-run-parallel](https://sources.debian.org/src/python-pytest-run-parallel/0.8.2-1/PKG-INFO) · [gcmon](https://pypi.org/project/gcmon) · [pygcprofiler](https://pypi.org/project/pygcprofiler/) · [PyVFT](https://pypi.org/project/pyvft/) · [Peeka](https://github.com/wwulfric/peeka)
- [vLLM multiprocessing design](https://docs.vllm.ai/en/latest/design/multiprocessing.html) · [krb5 #9197](https://mailman.mit.edu/pipermail/krb5-bugs/2026-February/013527.html) · [oslo.concurrency 3.14 patch](https://ftp.fau.de/gentoo-portage/dev-python/oslo-concurrency/files/0001-Fix-multiprocessing-tests-under-Python-3.14.patch) · [FSL pyfeeds MR 46](https://git.fmrib.ox.ac.uk/fsl/pyfeeds/-/merge_requests/46)
- [PEP 750](https://peps.python.org/pep-0750/) · [Semgrep t-string parsing issue #12030](https://github.com/semgrep/semgrep/issues/12030) · [t2string](https://pypi.org/project/t2string/) · [PEP 750 library roundup](https://the-gigi.github.io/gigi-zone/posts/2026/04/pep-talk-02-pep-750/) · [Ruff rules](https://docs.astral.sh/ruff/rules/)
