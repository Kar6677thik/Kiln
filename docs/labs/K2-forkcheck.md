# Lab K2: What each start method costs, and does `forkcheck` find real fork breakages?

> **Milestone:** [K2](../milestones/K2-start-methods-forkcheck.md) · **Incidents:** krb5 `t_otp.py` (E-05), oslo.concurrency `test_lockutils` (E-06), FSL pyfeeds (E-07), plus git-repo and pexpect (E-08) · **Status:** final · **Commit:** the final commit of `milestone/k2-start-methods` (the code was measured on top of `384a69a`) · **Run:** 2026-11-04, Python 3.15.1

## 1. The incident

Python 3.14 changed the default way `multiprocessing` creates child processes on Linux, from **fork** to **forkserver** ([concepts/start-methods.md](../concepts/start-methods.md)). Code that had worked for a decade broke, often silently:

- **krb5:** the `t_otp.py` test started its helper daemon from a top-level `Process(...)` with no `if __name__ == "__main__":` guard. Under forkserver the child re-imported the script. The daemon never answered, and the test framework's `atexit` hook printed confusing "re-run this test" text. The fix pinned the start method to `fork` ([krb5 PR #1483](https://github.com/krb5/krb5/pull/1483)).
- **oslo.concurrency:** a test passed a *nested* function as a `Process` target. Fork never pickles the target; forkserver must, and a nested function can't be pickled ([concepts/pickling.md](../concepts/pickling.md)).
- **FSL pyfeeds:** logging was configured only in the parent. Under fork the children inherited the handlers; under forkserver their log lines simply vanished.

To us, the interesting part is that these are *assumptions*: code that silently relies on fork copying the parent. K2 does two things about it. First, Kiln itself gets all three start methods, so we can measure what each one copies and costs. Second, we build `kiln forkcheck`, a static checker that reads Python code and points at those assumptions, then confirms its findings by actually running the code under each method.

## 2. Your prediction

> ✍️ **Learner: fill this in BEFORE reading further.**
> (a) Rank fork, forkserver and spawn by how long a worker takes from "launch" to "ready". Roughly how many times slower is the slowest? If the app allocates 100 MiB when imported, how much memory does each of 4 workers *really* cost under each method?
> (b) Will a static checker (one that never runs the code) find all three real breakages? Which of its rules do you expect to raise false alarms?
>
> _(your answer here)_

### Implementer's hypothesis

- **(a) Time:** fork < forkserver ≪ spawn. Fork copies an already-running process; forkserver forks a small clean helper; spawn starts a new interpreter and re-imports everything. The forkserver's *own* first start costs about one spawn.
- **(a) Memory:** with the 100 MiB app, PSS per worker is lowest for fork (the master and the workers share the pages), forkserver is close (the server and the workers share them), and spawn is highest (each worker has its own 100 MiB). RSS will look the same everywhere and so be misleading.
- **(a) Filesystem:** running from `/mnt/c` (9P) hurts spawn most, because it reads many files at startup; fork reads none.
- **(b)** All three required real cases are detected (FA001+FA005 for krb5, FA002 for oslo, FA003 for pyfeeds), and each is confirmed by `--confirm`. Synthetic precision and recall meet the ≥ 0.9 bar. FA003 and FA004, the most heuristic rules, are the most likely to have false positives.

## 3. Verified facts

| # | Claim (source) | Command run | Observed | ✅/❌ |
|---|---|---|---|---|
| G2.1 | Default start method on Linux: `fork` on 3.13, `forkserver` on 3.14+ | `python -c "import multiprocessing as m; print(m.get_start_method())"` on each version | 3.13 `fork`; 3.14, 3.15, 3.15t `forkserver` | ✅ |
| G2.2 | `os.posix_spawn` passes only fds marked inheritable | `test_arbiter_e2e.py::test_worker_fds_are_only_its_own`, parametrised over all three methods | passes for spawn (the worker sees only its own channel) | ✅ |
| G2.3 | `socket.send_fds`/`recv_fds` work on AF_UNIX stream sockets | [`concepts/snippets/fd_passing.py`](../concepts/snippets/fd_passing.py) on 3.13, 3.15, 3.15t | parent fd 4 arrived as fd 8 in the child, and reading through it works | ✅ |
| G2.4 | `os.pidfd_open` works, and a pidfd becomes readable when a *non-child* exits | [`concepts/snippets/pidfd_orphan.py`](../concepts/snippets/pidfd_orphan.py) | readable about 0.5 s after the grandchild exited. The orphan was adopted by WSL's `Relay(...)` process (a subreaper), not pid 1 | ✅ |
| G2.5 | `runpy.run_path(entry, run_name="__main__")` lets spawn/forkserver children re-import the main module | `tests/integration/test_confirm.py` on 3.13, 3.14, 3.15 | children see the script as `__mp_main__` and exit cleanly, so runpy mode is the default; the temp-copy fallback is implemented and tested anyway | ✅ |
| G2.6 | krb5 PR #1483 and `t_otp.py` are retrievable | PR page and raw file fetched | reachable | ✅ |
| G2.7 | oslo.concurrency 7.2.0 test and the Debian/Gentoo patches are retrievable | patch pages | descriptions reachable, diffs not (Gentoo 404) → case reconstructed, [errata E-20](../research/errata.md#e-20-three-corpus-sources-could-not-be-inspected-k2-gates-g27-g28-pexpect) | ⚠️ |
| G2.8 | FSL pyfeeds MR 46 is reachable | `curl -sI …/merge_requests/46` | connection refused on 2026-11-03 and -04 → reconstructed (E-07, E-20) | ❌ |
| G2.9 | The `symtable` calls used behave the same on 3.13–3.15 | `tests/unit/forkcheck/test_symbols.py` on the matrix | same meaning, **but** 3.14+ adds `__annotate__` tables to `get_children()` → filtered by type ([errata E-19](../research/errata.md#e-19-from-314-symtable-scope-lists-also-contain-__annotate__-tables)) | ⚠️→fixed |
| G2.10 | Only `ast.Constant` is needed (no removed `ast.Num`/`ast.Str`) | a test scans the forkcheck package for the removed names | none used | ✅ |
| G2.11 | forkserver workers stay in the master's process group | `test_launchers_e2e.py::test_workers_ignore_sigint_and_share_the_process_group` (all methods) | same pgid; SIGINT ignored | ✅ |
| new | A module run with `python -m` can use `logging.getLogger(__name__)` | first forkserver test run | ❌ its `__name__` is `"__main__"`, so the logger sat outside the `kiln` tree and every forkserver line vanished ([ADR-035](../decisions.md#adr-035) §4) | ❌→fixed |

## 4. Setup

- **Machine:** WSL2 Debian 13, kernel 6.6.87.2-microsoft-standard-WSL2, 14 CPUs, 7.2 GiB RAM visible to WSL (`kiln doctor`).
- **Interpreter:** CPython 3.15.1 (GIL build).
- **Code location:** both. The servers in lab (a) ran once from an **ext4 copy** in `~/kiln-bench` (`PYTHONPATH=~/kiln-bench/src`, so `kiln` itself was read from ext4) and once from the repo on `/mnt/c` (**9P**). Each record's `host.notes` says which (`source=ext4-snapshot` / `source=9p`). The lab makes the copy itself because `rsync` isn't installed yet ([ADR-035](../decisions.md#adr-035) §6).
- **Commands:**

```bash
(wsl) $ ./scripts/dev.sh lab k2-startup     # (a) 12 variants × (3 warm-up + 10 measured) rounds, ~5 min
(wsl) $ ./scripts/dev.sh lab k2-calibrate   # (b) scores every rule, runs --confirm on each real case
(wsl) $ kiln forkcheck corpus/real/oslo-lockutils --confirm corpus/real/oslo-lockutils/distilled.py
```

The drivers are [`labs/k2/k2_lab.py`](../../labs/k2/k2_lab.py) and [`labs/k2/confirm_harness.py`](../../labs/k2/confirm_harness.py). The heavy app is [`labs/k2/heavy_app.py`](../../labs/k2/heavy_app.py): one 100 MiB `bytes` object filled with `os.urandom` at import time.

## 5. Method

| Experiment | Varied | Measured | Fixed | Repeats |
|---|---|---|---|---|
| (a) startup | `start.method` ∈ {fork, forkserver, spawn} × app ∈ {light `examples.demo`, heavy `heavy_app` (100 MiB)} × fs ∈ {ext4, 9P} | `boot_ms` from each `worker_ready` event (launch → ready, the master's monotonic clock); `start_ms` from `launcher_started`; Rss/Pss/USS of every worker, the master and the forkserver from `/proc/<pid>/smaps_rollup`, 0.5 s after the last `ready` | 4 workers, `app.preload = true`, log level INFO | 3 warm-up rounds (discarded) + **10 measured**, variants round-robin with a seeded shuffle inside each round (seed 20261104); a fresh server per run |
| (b) calibrate | — | for every rule: TP, FP, FN over the 76 synthetic files (`# expect:` comments, exact line) and the 5 real cases (`expected.json`); `--confirm` outcome per method per real case | `kiln.forkcheck` defaults | 1 (the checker is deterministic) |

- **Statistics:** per run, the median of the 4 workers' `boot_ms`; per variant, the median of the 10 runs with a 95% bootstrap CI (2,000 resamples, seed 2, spec/10 §4.2). Memory: medians of the 10 runs (CV of total PSS ≤ 2.1% in every variant).
- **USS** = `Private_Clean + Private_Dirty`: the memory that would be freed if that process exited ([concepts/memory-metrics.md](../concepts/memory-metrics.md)).

## 6. Results: before

"Before" is Python 3.13's world, where everything was forked and nobody noticed the assumptions. The confirmation runs in (b) reproduce what happened when the default changed. Here is the oslo case, untouched code, three start methods ([record](../../results/published/K2/calibrate/20261104T094517Z-py3.15-9b3b.json)):

| method | outcome | output |
|---|---|---|
| fork | ok | `lines written: ['child-0', 'child-1']` |
| forkserver | `exception:PicklingError` | `_pickle.PicklingError: Can't pickle local object <function FileBasedLockingTestCase.test_lock_internally_between_processes.<locals>.target at 0x…>` |
| spawn | `exception:PicklingError` | same |

And krb5, which is worse because it *passes*:

| method | outcome | output |
|---|---|---|
| fork | ok | `daemon: started` · `daemon: accept:otp-token` · `*** Success: OTP tests` |
| forkserver | ok | `daemon never answered` · `*** Success: OTP tests` · then a traceback from the child |
| spawn | ok | same as forkserver |

The exit code is 0 in every row, so a CI system that only checks exit codes sees three passing runs. This is exactly the krb5 story.

## 7. The fix

For Kiln itself there was nothing to fix in the startup costs: lab (a) is a measurement. Building the launchers did need two fixes, both found by tests:
1. **The forkserver's logger** ([ADR-035](../decisions.md#adr-035) §4): see the last row of §3.
2. **The forkserver's pid for tests and labs** now comes from the master's own `launcher_started` event (ADR-035 §3), not from the forkserver's log.

For the *user code* in the corpus, the fixes are what forkcheck's hints say: add the `__main__` guard (krb5), move the target to module level (oslo), configure logging in the child or use a `QueueHandler` (pyfeeds), apply patches inside the child (git-repo), or, as krb5 and pexpect did, pin `get_context("fork")` and accept the fork-only risk (FA008 reports that as *info*).

## 8. Results: after

### (a) What each start method costs ([records](../../results/published/K2/startup/), 4 workers, medians of 10 runs)

| method | app | fs | launch→ready ms, median [95% CI] | launcher start ms | PSS/worker MiB | USS/worker MiB | RSS/worker MiB | master PSS MiB | forkserver PSS MiB | total PSS MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| fork | light | ext4 | **5.4** [4.8, 6.5] | 0 | 6.4 | 3.2 | 22.8 | 7.3 | — | 32.9 |
| forkserver | light | ext4 | **11.8** [11.0, 13.5] | 89.8 | 6.9 | 4.3 | 21.2 | 16.2 | 7.3 | 51.3 |
| spawn | light | ext4 | **101.5** [92.8, 105.1] | 0 | 14.7 | 13.7 | 24.2 | 16.1 | — | 75.0 |
| fork | heavy | ext4 | **7.2** [6.6, 8.8] | 0 | 26.4 | 3.2 | 122.8 | 27.3 | — | 132.9 |
| forkserver | heavy | ext4 | **14.3** [11.4, 17.0] | 356.6 | 26.9 | 4.3 | 121.2 | 16.2 | 27.3 | 151.2 |
| spawn | heavy | ext4 | **348.9** [331.5, 359.9] | 0 | 114.7 | 113.7 | 124.1 | 16.1 | — | 475.0 |
| fork | light | 9P | 6.2 [4.7, 6.7] | 0 | 6.4 | 3.2 | 22.8 | 7.2 | — | 32.7 |
| forkserver | light | 9P | 14.6 [13.6, 16.7] | 538.1 | 6.9 | 4.3 | 21.3 | 16.1 | 7.3 | 51.0 |
| spawn | light | 9P | **428.4** [370.2, 522.6] | 0 | 14.7 | 13.7 | 24.1 | 16.1 | — | 75.0 |
| fork | heavy | 9P | 9.0 [6.6, 9.3] | 0 | 26.4 | 3.2 | 122.7 | 27.2 | — | 132.6 |
| forkserver | heavy | 9P | 17.5 [15.3, 20.5] | 926.7 | 26.9 | 4.3 | 121.3 | 16.1 | 27.3 | 151.1 |
| spawn | heavy | 9P | **676.5** [612.1, 725.3] | 0 | 114.7 | 113.7 | 124.1 | 16.1 | — | 475.0 |

"launcher start" is the forkserver's own start (fresh interpreter + preload + `ready`), paid once at startup and again after any forkserver death. For fork and spawn there is nothing to start.

### (b) Checker calibration ([record](../../results/published/K2/calibrate/20261104T094517Z-py3.15-9b3b.json))

Synthetic (76 files, at least 4 positive and 4 negative per rule) plus the 5 real cases:

| rule | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| FA001 unguarded-process-start | 8 | 0 | 0 | 1.0 | 1.0 |
| FA002 unpicklable-target | 7 | 0 | 0 | 1.0 | 1.0 |
| FA003 parent-only-logging | 6 | 0 | 0 | 1.0 | 1.0 |
| FA004 runtime-global-not-inherited | 4 | 0 | 0 | 1.0 | 1.0 |
| FA005 import-time-side-effect | 6 | 0 | 0 | 1.0 | 1.0 |
| FA006 parent-patch-not-inherited | 6 | 0 | 0 | 1.0 | 1.0 |
| FA007 unpicklable-argument | 5 | 0 | 0 | 1.0 | 1.0 |
| FA008 explicit-fork-reliance | 7 | 0 | 0 | 1.0 | 1.0 |

| case | must_fire found | confirmed? | fork | forkserver | spawn | what differs |
|---|---|---|---|---|---|---|
| krb5-t_otp (required) | 2/2 (FA001 `distilled.py:32`, FA005 `k5test_like.py:22`) | ✅ | ok | ok | ok | the daemon never answers, yet "Success" is still printed (§6) |
| oslo-lockutils (required) | 1/1 (FA002 `:47`) | ✅ | ok | PicklingError | PicklingError | the child never starts |
| fsl-pyfeeds (required) | 1/1 (FA003 `:32`) | ✅ | ok | ok | ok | the children's 4 log lines are missing; only the parent's 2 remain |
| git-repo-mock | 1/1 (FA006 `:58`) | ✅ | ok | ok | ok | the class-level `mock.patch` is gone in the children: "git not available" instead of `refs/heads/main` |
| pexpect-context | 1/1 (FA008 `:16`, info) | ✅ (trivially) | ok | ok | ok | only the line naming the start method differs; the code pins fork, so it keeps working |

**SC-5 met:** all three required cases are detected on their exact lines and confirmed. `kiln forkcheck corpus/real/<case> --confirm …` exits 1 with a `[confirmation: …]` suffix on the finding.

## 9. Analysis

- **(a) Time matched the hypothesis, by a wide margin.** On ext4 with the light app, fork takes 5 ms, forkserver 12 ms and spawn 100 ms: spawn is about 19× slower than fork. Forkserver's extra ~6 ms is the round trip through the server (`spawn_worker` → fork → `spawned`) plus the worker re-running `logsetup`. The forkserver's own first start (90 ms) costs about one spawn (100 ms), as predicted. With the heavy app it costs 357 ms ≈ one heavy spawn (349 ms), because both must create the 100 MiB.
- **(a) The heavy app shows where the time goes.** Fork and forkserver barely notice the 100 MiB (7 and 14 ms), because the import already happened in the parent and fork copies page *tables*, not pages ([concepts/fork.md](../concepts/fork.md)). Spawn pays the full import (generating 100 MiB of random bytes) in every worker: 349 ms.
- **(a) Memory: PSS tells the truth, RSS doesn't.** Every heavy worker shows RSS ≈ 122 MiB, whatever the method. RSS counts shared pages fully in every process. PSS divides them: under fork the 100 MiB is shared by 5 processes (the master + 4 workers), so each is charged 20 MiB on top of its ~6.4 MiB base. Forkserver is the same, with the forkserver as the fifth sharer instead of the master. Spawn's 114.7 MiB PSS ≈ its 113.7 MiB USS: nothing is shared. Total: **133 MiB (fork) vs 151 MiB (forkserver) vs 475 MiB (spawn)** for the same 4 workers.
- **(a) Forkserver's memory price is one extra interpreter** (~16–18 MiB), which shows up in two places. The forkserver process itself costs 7.3 MiB PSS (light), and the master costs 16 MiB instead of 7 MiB, because under forkserver and spawn the workers aren't copies of the master, so nothing of the master's is shared. With 4 light workers, that overhead (51 vs 33 MiB) is larger than the per-worker difference. With 40 workers it would be small.
- **(a) 9P hurts exactly what reads files.** Spawn's light-app start goes from 101 to 428 ms (4.2×), and the forkserver's own start from 90 to 538 ms. Fork's per-worker time doesn't change (6 vs 5 ms, CIs overlap), since a forked worker reads no files. ADR-003's rule (time startup only from ext4) is justified by this table.
- **(b) The checker met every target, but the synthetic 1.0s are optimistic.** The same author wrote the rules and the synthetic corpus, so the corpus probes the cases the rules were built for, sharing the same blind spots. The real cases are the honest test: 5/5 detected, on the exact lines, with no `must_not_fire` violations. FA003/FA004 produced no false positives here, against the hypothesis. Measuring their real noise needs code we didn't write (the upstream checkouts, §14 "Stretch" of the milestone).
- **(b) `--confirm` proves "behaviour changes", not "this finding is the cause".** pexpect is "confirmed" only because it *prints* the start method. git-repo and pyfeeds differ only in output, not exit status: a reminder that a bug like this passes CI unless someone compares output.

## 10. Reference comparison

Read after building. Sources: CPython 3.15.1's `Lib/multiprocessing/forkserver.py`, `spawn.py` and the stdlib `os.register_at_fork` call sites, as installed by uv.

1. **Reporting a child's exit.** CPython's forkserver reaps on SIGCHLD with `os.waitpid(-1, WNOHANG)`, converts the status with `os.waitstatus_to_exitcode(sts)` and sends just that integer to the client with `write_signed` (a fixed-size `struct`) over a per-child pipe. Kiln sends the **raw wait status** in a `child_exit` frame and decodes it in the master with the same `classify_exit` used for forked workers, so a forkserver worker's death keeps its details (core dump flag, signal name, OOM suspicion). CPython's caller only needs a `Popen.returncode`.
2. **Authentication.** CPython's forkserver listens on an `AF_UNIX` socket with an address (a file, or the abstract namespace), so any local process could connect: it runs an HMAC challenge with a 32-byte `authkey` (`answer_challenge`/`deliver_challenge`) and `chmod 0o600`s the socket file. Kiln uses a **socketpair** created before the server starts. It has no address, so the only process that can talk to the server is the one holding the other end: there is nothing to authenticate.
3. **Preparation data for spawn.** `spawn.get_preparation_data` sends `sys.path`, `sys.argv`, the original and current directory, the start method, the log level, the `authkey`, and the main module's name or path (`init_main_from_name`/`_path`), so the child can rebuild the parent's world and re-import `__main__`. Kiln sends only what a worker needs: its fd, id, generation and the config as JSON, plus `PYTHONPATH`. A Kiln worker never needs the user's main script, because tasks are found through the app spec (`module:attribute`), never through `__main__`.
4. **Orphans.** CPython's forkserver watches an "alive" pipe and exits when it hits EOF ("no more client processes left"), but it does **not** kill its children first: they live on as orphans. Kiln's forkserver SIGTERMs its children, waits 5 s, then SIGKILLs survivors (AC-2.3). In the other direction, if the forkserver dies, Kiln watches the orphans through pidfds.
5. **vLLM's runtime checks vs static detection.** Runtime checks see the truth for the paths that actually run (no false alarms), which is why `--confirm` exists. But they only find a problem after it has happened, often in CI on a new Python version. Static analysis earns its place *before* the upgrade: it read all 5 cases in milliseconds, including paths no test ran, and named the line and the fix. The two together beat either alone.
6. **`os.register_at_fork` in CPython.** `random` reseeds the child (`after_in_child=_inst.seed`), which is why `labs.random_probe` differs across forked workers. `threading` resets its state in the child. `logging` takes and re-initialises its handler locks around fork. `concurrent.futures.thread` takes its global shutdown lock and clears its queues. `asyncio` drops the running loop. `importlib.metadata` clears caches, and `multiprocessing.resource_tracker` resets itself. All of these exist only because fork copies a running process. Spawn and forkserver workers don't need them.

## 11. Limitations and threats to validity

- **Timing noise is high.** The per-run CV of launch→ready is 6–27% (spec/10 flags >5%). The times are small (5–15 ms for fork/forkserver), and WSL2 on a laptop adds scheduler noise. The CIs are wide, but the ordering fork < forkserver ≪ spawn never overlaps.
- **"Launch" starts when the master *asks*.** `boot_ms` includes the master's own work (socketpair, fork or posix_spawn call). For forkserver it includes the server round trip. That's the right measure for "how fast can Kiln replace a worker", not a pure kernel `fork` benchmark.
- **The heavy app is a best case for sharing.** One big `bytes` object is shared almost perfectly. Millions of small Python objects would not be, because touching their reference counts copies pages. That's K3's question.
- **Synthetic calibration is self-graded** (§9). Three of five real cases (pyfeeds, oslo, pexpect) are reconstructed, not copied, because their sources were unreachable ([E-20](../research/errata.md#e-20-three-corpus-sources-could-not-be-inspected-k2-gates-g27-g28-pexpect)). They encode the *pattern* reported for each bug; the real code may differ.
- **Upstream calibration not run.** `scripts/fetch-corpus.sh` can clone krb5 and oslo.concurrency, but the milestone lists it as stretch; not measured.
- **One interpreter.** Lab (a) ran on 3.15.1 only. Spawn startup time depends on the interpreter's own import time, which varies between versions.

## 12. What you should now be able to explain

- [ ] What fork, forkserver and spawn each copy into a worker, and what each costs in time and memory, with numbers (*Processes*)
- [ ] Why PSS and not RSS is the honest per-worker memory number (*Memory*)
- [ ] How an fd travels to another process (SCM_RIGHTS), and why its number changes (*Processes, Unix*)
- [ ] Why only a parent can `waitpid`, and how Kiln learns about a forkserver worker's death, and about an orphan's (*Processes*)
- [ ] Why a nested function can't be a spawn/forkserver target, and what pickling by reference means (*Python data model*)
- [ ] What the `__main__` guard protects against (*Import system*)
- [ ] What `ast` and `symtable` each tell a checker, and what static analysis can't know (*Tooling*)
- [ ] Precision vs recall, and why self-graded calibration is optimistic (*Measurement*)

## 13. Checkpoint quiz

1. Why does a heavy spawn worker have RSS ≈ PSS ≈ USS, but a heavy fork worker has RSS 123 MiB and PSS 26 MiB?
   <details><summary>Answer</summary>

   The spawned worker built its own 100 MiB, so every page is private: counted once (RSS), fully charged to it (PSS), and freed if it exits (USS). The forked worker's 100 MiB pages are shared with the master and the other 3 workers. RSS counts them fully anyway; PSS charges each of the 5 sharers one fifth (20 MiB); USS counts none of them, because they wouldn't be freed if this worker exited.

   </details>
2. The forkserver's first start costs about as much as one spawned worker. Why?
   <details><summary>Answer</summary>

   It *is* a spawned process: a fresh interpreter started with `posix_spawn`, which imports Kiln (and the app, with `app.preload`) before answering `ready`. After that, every worker is a cheap fork of that already-loaded server.

   </details>
3. Why does running from `/mnt/c` slow spawn by 4× but not fork?
   <details><summary>Answer</summary>

   A spawned worker starts a new interpreter that reads and imports dozens of files, and every file access over 9P is slow. A forked worker reads no files: it starts with a copy of a process that already imported everything.

   </details>
4. Under forkserver, why is the master's PSS 16 MiB instead of 7 MiB under fork?
   <details><summary>Answer</summary>

   Under fork the workers are copies of the master, so the master's pages are shared with 4 workers and it's charged only a share of each. Under forkserver (and spawn) the workers come from somewhere else, so nobody shares the master's pages, and it's charged for all of them.

   </details>
5. krb5's test printed "Success" under forkserver although the daemon never answered. What would have caught it?
   <details><summary>Answer</summary>

   Either comparing output across start methods (what `--confirm` does: it reports `differs`), or reading the code: FA001 flags the unguarded top-level `Process(...).start()`, and FA005 flags the `atexit` registration that runs again on re-import, before anything runs.

   </details>
6. All 8 rules scored precision 1.0 on the synthetic corpus. Why shouldn't you conclude forkcheck never raises false alarms?
   <details><summary>Answer</summary>

   The same author wrote the rules and the test files, so the files test what the rules were designed to handle. Precision on code nobody tailored to the checker (the upstream projects) is still unmeasured.

   </details>
