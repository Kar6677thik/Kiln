# Lab K1 — Supervisor: how workers die, zero-drop reload, the master's ceiling, hung workers

> **Milestone:** [K1](../milestones/K1-supervisor.md) · **Incident:** research §2.7 (Celery/pre-fork workers, Back Market's silent OOM kills) · **Status:** final · **Commit:** the final commit of `milestone/k1-supervisor` (the code was measured on top of `1fbd98f`) · **Run:** 2026-11-04, Python 3.15.1 (and 3.15.1t where stated)

## 1. The incident

Back Market ran Celery workers in Kubernetes containers with a memory limit. When the workers together used more memory than the limit, the Linux kernel's **OOM killer** chose one process and killed it instantly with SIGKILL. A process can't catch, delay or log SIGKILL. Kubernetes didn't even report an OOM event. The only trace in Celery was a vague "worker lost". Wikimedia had a related problem: each forked Celery child grew to about 310 MB. The usual cures are **recycling** (restart a worker after N jobs or past M megabytes) and better **detection**.

To recognise any of that, a supervisor must (1) notice a worker died, (2) tell *how* it died from the operating system's wait status, (3) decide what happens to the work it was holding, and (4) replace it. This is gunicorn's arbiter and Celery's pool in a nutshell. This lab checks that Kiln's version does all four, with real crashes and a real kernel OOM kill. It also measures two things the design implies: reloading every worker should lose *zero* jobs, and the single-threaded master should become the bottleneck at some throughput.

## 2. Your prediction

> ✍️ **Learner: fill this in BEFORE reading further.**
> (a) When a worker dies by `exit(1)`, by SIGSEGV, and by a SIGKILL nobody explains, what will Kiln log for each? Which ones are "OOM"?
> (b) If we replace all 4 workers every 2 seconds while 10,000 jobs are running, how many jobs are lost?
> (c) Roughly how many trivial jobs per second can one master handle? Does adding workers keep helping?
> (d) Does a worker running a 15-second pure-Python loop stop sending heartbeats? What about a C call that holds the GIL?
>
> _(your answer here)_

### Implementer's hypothesis

- **(a)** `exit1` → `crashed(exit_code=1)`, `segv` → `killed(SIGSEGV)`, `abort` → `killed(SIGABRT)`. A self-SIGKILL → `suspected_oom=true` but **not confirmed**, because no kernel OOM happened, so the cgroup's `oom_kill` counter shouldn't move. A real OOM inside a 512 MiB cgroup → `suspected_oom=true` **and** `oom_confirmed=true`. The job becomes `LOST`, and the master survives thanks to `oom_score_adj=500` on workers (ADR-029).
- **(b)** Zero lost. The queue lives in the master, and retiring workers finish what they hold (ADR-007). Latency should rise slightly during reloads.
- **(c)** Throughput rises with workers only until the master's one thread is at about 100% CPU, then flattens. Every job costs the master some JSON encoding and decoding and a few syscalls. A guess for the ceiling: some thousands per second.
- **(d)** The pure-Python loop keeps heartbeats flowing, because the interpreter switches threads every 5 ms. A GIL-holding C call stops them, and the master kills the worker after `heartbeat_timeout_s` (10 s). On 3.15t (no GIL) nothing stops.

## 3. Verified facts

| # | Claim (source) | Command run | Observed | ✅/❌ |
|---|---|---|---|---|
| G1.1 | `selectors.DefaultSelector` is epoll on WSL (K1 spec §8) | `python -c "import selectors; print(selectors.DefaultSelector)"` | `EpollSelector` | ✅ |
| G1.2 | `set_wakeup_fd` on a non-blocking socket writes the signal number byte | `docs/concepts/snippets/signals_wakeup.py` | bytes `[10, 12, 10]` = SIGUSR1, SIGUSR2, SIGUSR1 | ✅ |
| G1.3 | Forked children get different `random` values | `tests/integration/test_fork_launcher.py::test_random_differs_between_parent_and_child` | passes | ✅ |
| G1.4 | `systemd-run --user --scope -p MemoryMax=…` works (delegated memory controller) | primer `cgroups-and-oom-killer`, lab (a) | works; exit 137 for an over-limit child | ✅ |
| G1.5 | `/proc/self/cgroup` is one `0::/path` line and `memory.events` has `oom_kill` | lab (a) startup event `cgroup_detected` | readable; counter went `0→1` on the OOM kill | ✅ |
| G1.6 | `ctypes.PyDLL(None).sleep(2)` holds the GIL; `CDLL` releases it | `./scripts/dev.sh lab k1-gilprobe` ([3.15 record](../../results/published/K1/gil-probe/20261104T081839Z-py3.15-7378.json)) | counter thread: CDLL 194 ticks, PyDLL **1** tick, regex (n=28) 1 tick in 7.69 s | ✅ |
| G1.6t | …and on 3.15t nothing holds anything | same, `KILN_PY=3.15t` ([record](../../results/published/K1/gil-probe/20261104T081852Z-py3.15t-dfc8.json)) | CDLL 195, PyDLL **196**, regex 774 ticks | ✅ |
| G1.7 | `os._exit` in a forked child skips the parent's atexit handlers | `test_fork_launcher.py::test_child_does_not_run_parent_atexit` | passes | ✅ |
| G1.8 | Fork with a live thread emits a DeprecationWarning (3.12+) | `docs/concepts/snippets/fork_with_threads.py` | `DeprecationWarning: This process (pid=N) is multi-threaded, use of fork() may lead to deadlocks in the child.` | ✅ |
| G1.9 | The OOM killer works inside a 512 MiB cgroup without freezing WSL | lab (a) | worker killed in under a second; WSL stayed responsive | ✅ |
| G1.10 | `os.waitstatus_to_exitcode` is negative for signals | `docs/concepts/snippets/exit-status_1.py` | SIGKILL → `-9` | ✅ |
| new | A systemd scope stays alive after an OOM kill inside it | first run of lab (a) | ❌ systemd's default `OOMPolicy=stop` SIGTERMed the whole scope, master included → fixed with `-p OOMPolicy=continue` (errata E-18) | ❌→fixed |

## 4. Setup

- **Machine:** WSL2 Debian 13, kernel 6.6.87.2-microsoft-standard-WSL2, 14 CPUs, 7.2 GiB RAM visible to WSL (`kiln doctor`, [K0 setup log](../tours/K0.md#appendix-setup-log)).
- **Interpreters:** CPython 3.15.1 (GIL build) for everything. CPython 3.15.1t additionally for G1.6 and lab (d).
- **Code location:** the repo on `/mnt/c` (9P). None of these labs measures import or startup time, so the ext4 snapshot wasn't needed (ADR-003). Every record notes `source=9p`.
- **Commands:** each experiment is one command, and each writes ExperimentRecords to `results/K1/<experiment>/`. The cited ones are copied to `results/published/K1/`.

```bash
(wsl) $ ./scripts/dev.sh lab k1-gilprobe          # gate G1.6
(wsl) $ ./scripts/dev.sh lab k1-deaths            # (a) crash modes
(wsl) $ ./scripts/dev.sh lab k1-oom               # (a) real OOM in a 512 MiB systemd scope
(wsl) $ ./scripts/dev.sh lab k1-reload --repeats 3
(wsl) $ ./scripts/dev.sh lab k1-ceiling --repeats 3 --jobs 5000
(wsl) $ ./scripts/dev.sh lab k1-heartbeat         # and KILN_PY=3.15t ./scripts/dev.sh lab k1-heartbeat
```

The driver is [`labs/k1/k1_lab.py`](../../labs/k1/k1_lab.py). Each experiment starts a fresh `kiln serve examples.demo:app --labs` in its own process group and kills that group at the end.

## 5. Method

| Experiment | Varied | Measured | Fixed | Repeats |
|---|---|---|---|---|
| (a) deaths | `labs.crash how` ∈ exit1, segv, abort, sigkill_self | the `worker_died` log fields, the job state | 2 workers | 1 per mode (a classification is deterministic) |
| (a) OOM | — | `worker_died` fields, `memory.events` delta, master alive afterwards | `MemoryMax=512M`, `MemorySwapMax=0`, `OOMPolicy=continue`, `labs.alloc mb=700 hold=true` | 1 |
| (b) reload | no reload vs `--reload-every 2` | final state of each of 10,000 jobs, throughput, latency p50/p99, master CPU | 4 workers, `labs.sleep ms=5`, closed loop, concurrency 64 | 3 per variant, plus 1 negative control |
| (c) ceiling | workers ∈ {1,2,4,8,12} × concurrency ∈ {16,64,256} | steady-state throughput (jobs/s, 10th–90th percentile window), master CPU share | `labs.echo`, 5,000 jobs | 3 per cell |
| (d) heartbeat | task ∈ `burn_cpu 15000 ms`, `hold_gil 20 s` × build ∈ 3.15, 3.15t | max heartbeat age seen in `stats`, polled every 0.2 s; whether the worker was killed; job state | heartbeat every 1 s, timeout 10 s | 1 per cell |

- **Statistics:** spec/10 asks for ≥ 10 repeats and bootstrap CIs. K1 ran **3 repeats** per cell, to keep the 45-run ceiling sweep under 10 minutes. Below, we report median and min–max, not confidence intervals. K9 re-runs these suites with full statistics (see §11).
- **Latency:** in a closed loop, latency is measured from submit to the moment the wait reply arrives.

## 6. Results: before

"Before" here means *the unexplained, unmeasured situation*: a worker dies and nobody knows why. This is what the first OOM run showed, before the systemd fix.

| Observation (first OOM run, log `results` not recorded: the lab crashed) | Value |
|---|---|
| worker classification | `how=killed signal=SIGKILL suspected_oom=true oom_confirmed=true oom_evidence="cgroup memory.events oom_kill 0→1"` |
| what happened next | `signal_received signal=SIGTERM action=graceful_shutdown` in the master, from systemd, within the same second |
| master | shut itself down: the whole scope was stopped by systemd's default `OOMPolicy=stop` |

This is the Back Market trap in a second form. Kiln *did* detect the OOM kill correctly, but the platform around it (systemd) reacted to the OOM by stopping everything. Kubernetes and container runtimes make similar choices.

## 7. The fix

There was nothing to fix in Kiln's detection. The lab's environment needed `-p OOMPolicy=continue`, so that systemd lets the rest of the scope live after the kernel kills one process (errata E-18, spec/07 §6.1).

Building K1 also needed two real fixes in Kiln itself. The tests found both, and both explain results below:
1. **The retirement handshake** (ADR-034 §5). A worker that hits `max_jobs` now announces `retiring` and keeps serving until the master's `shutdown` arrives. Before that, about 1 test run in 3 lost a job that was already on its way to the worker.
2. **Drain before resolving a death** (ADR-034 §6). The master reads everything left in a dead worker's socket before declaring its jobs lost. Before that, a job that *finished* just before a retired worker exited was sometimes marked `LOST`, because the loop handled SIGCHLD before reading the result.

## 8. Results: after

### (a) Death classification ([record](../../results/published/K1/deaths/20261104T081854Z-py3.15-ea5a.json))

| how | expected classification | observed log fields | job state | ✅/❌ |
|---|---|---|---|---|
| exit1 | crashed(exit_code=1) | `how=crashed exit_code=1 signal=null suspected_oom=false` | LOST | ✅ |
| segv | killed(SIGSEGV) | `how=killed signal=SIGSEGV suspected_oom=false` | LOST | ✅ |
| abort | killed(SIGABRT) | `how=killed signal=SIGABRT suspected_oom=false` | LOST | ✅ |
| sigkill_self | killed(SIGKILL), suspected but unconfirmed OOM | `how=killed signal=SIGKILL suspected_oom=true oom_confirmed=false oom_evidence="cgroup memory.events oom_kill unchanged (0)"` | LOST | ✅ |
| real OOM, 700 MiB in a 512 MiB scope ([record](../../results/published/K1/oom/20261104T082031Z-py3.15-f72d.json)) | killed(SIGKILL), confirmed OOM | `how=killed signal=SIGKILL suspected_oom=true oom_confirmed=true oom_evidence="cgroup memory.events oom_kill 0→1"` | LOST (`killed: SIGKILL (suspected OOM)`) | ✅ |

After the OOM kill, the master answered `ping` and later exited with code 0 on a graceful stop. **SC-3 met.**

### (b) Zero-drop reload under load ([records](../../results/published/K1/reload/))

| condition | jobs | SUCCEEDED | LOST | CANCELLED | other | throughput (jobs/s), median of 3 | p50 (ms), median | p99 (ms), median | peak workers | peak Σ RSS |
|---|---|---|---|---|---|---|---|---|---|---|
| no reload | 10,000 ×3 | 10,000 ×3 | 0 | 0 | 0 | 706.2 (705.8–708.5) | 90.2 | 99.8 | 4 | not measured: no RSS sampling in this lab (K3 adds it) |
| reload every 2 s (7 reloads/run) | 10,000 ×3 | 10,000 ×3 | 0 | 0 | 0 | 698.0 (685.7–698.4) | 91.3 | 105.2 | 8 during each reload (4 old + 4 new) | not measured: no RSS sampling in this lab (K3 adds it) |
| negative control: grace 1 s, 3 s jobs | 40 | 28 | **12** | 0 | 0 | 1.0 | 8007.0 | 9015.9 | 4 | not measured: control run |

**SC-1 met:** 10,000 jobs under load (concurrency 64), 0 errors. **SC-2 met:** 21 reloads across 30,000 jobs, 0 `LOST` and 0 `CANCELLED`. The negative control proves the measurement *can* see drops: a 1-second grace period for 3-second jobs forces kills, and 12 jobs were lost.

### (c) The master's throughput ceiling ([records](../../results/published/K1/ceiling/), `labs.echo`, 5,000 jobs, median (min–max) of 3)

| workers | concurrency | jobs/s | master CPU% | p99 (ms) |
|---|---|---|---|---|
| 1 | 16 | 7024 (5701–7282) | 80.9 | 6.2 |
| 1 | 64 | 6106 (6065–6274) | 82.3 | 19.0 |
| 1 | 256 | 6186 (5240–6271) | 82.1 | 111.2 |
| 2 | 16 | 10233 (8832–11097) | 97.3 | 4.2 |
| 2 | 64 | 9701 (8273–10139) | 94.9 | 22.5 |
| 2 | 256 | 9617 (8822–10234) | 96.9 | 45.0 |
| 4 | 16 | 10466 (9689–11382) | 98.0 | 4.3 |
| 4 | 64 | 10276 (10190–10573) | 100.7 | 13.9 |
| 4 | 256 | 11725 (10973–12056) | 103.5 | 36.7 |
| 8 | 16 | 10785 (10093–11556) | 96.4 | 4.2 |
| 8 | 64 | 9826 (9810–11954) | 100.0 | 13.7 |
| 8 | 256 | 12374 (11727–13314) | 103.4 | 30.2 |
| 12 | 16 | 10285 (10085–13459) | 93.8 | 4.6 |
| 12 | 64 | 12813 (12459–13529) | 97.9 | 9.5 |
| 12 | 256 | 12402 (11898–12827) | 102.4 | 32.3 |

The "master CPU%" is the master's user+system CPU seconds from `/proc/<pid>/stat`, divided by the load's wall time. The CPU window is slightly wider than the load window, which is why some cells exceed 100% (see §11).

### (d) Heartbeats ([records](../../results/published/K1/heartbeat/))

| interpreter | task | max heartbeat gap (s) | killed? | reason | job state |
|---|---|---|---|---|---|
| 3.15 (GIL) | `labs.burn_cpu` 15 s | 1.01 | no | not killed: heartbeats continued | SUCCEEDED |
| 3.15 (GIL) | `labs.hold_gil` 20 s | 9.93 | yes | heartbeat_timeout | LOST |
| 3.15t (free-threaded) | `labs.burn_cpu` 15 s | 0.99 | no | not killed: heartbeats continued | SUCCEEDED |
| 3.15t (free-threaded) | `labs.hold_gil` 20 s | 1.00 | no | not killed: no GIL to hold | SUCCEEDED |

## 9. Analysis

- **(a) Matched the hypothesis exactly, and the "suspected vs confirmed" distinction carries the lesson.** A SIGKILL looks identical whether the kernel sent it or `kill -9` did. Only the cgroup counter turns suspicion into evidence. The surprise was outside Kiln: systemd's `OOMPolicy=stop` turned one OOM kill into "stop everything". That's a realistic production failure mode, and exactly the kind of signal Back Market lacked.
- **(b) Zero lost jobs, as predicted.** The cost was small: throughput −1.2% (698 vs 706 jobs/s, medians) and p99 +5% (105 vs 100 ms). Throughput here is limited by the *workers*, not the master. 4 workers each sleeping 5 ms (plus overhead) can do at most about 700 jobs/s, which matches the measurement. Each reload briefly ran 8 workers, so expect up to 2× the memory during a reload.
- **(c) The ceiling is the master, at about 10,000–12,000 trivial jobs/s.** With one worker the master isn't saturated (about 81% CPU, about 6–7K jobs/s): the single worker is the limit. From 2 workers upward the master sits at about 95–100% of one core, and throughput stops improving whatever we add. This is the price of ADR-007's design, where every job passes through one thread twice (submit and result, plus `wait`). It's the same reason gunicorn's master *doesn't* handle requests. Higher concurrency mostly buys latency: p99 rises from about 4 ms at 16 connections to 30–110 ms at 256, because requests wait in buffers (Little's law). A K8 question follows: how much of the master's time is JSON?
- **(d) Matched, including the 3.15t contrast.** A pure-Python loop never stopped the heartbeat (max gap 1.01 s with a 1 s interval). The GIL-holding C call silenced it until the master killed the worker at about 10 s. On the free-threaded build the same call blocked only its own thread. That's a free-threading lesson in miniature: the heartbeat-based hang detector has nothing to detect there (K4).

## 10. Reference comparison

Read after building. The sources are gunicorn's [`arbiter.py`](https://github.com/benoitc/gunicorn/blob/master/gunicorn/arbiter.py) (main branch, fetched 2026-11-04) and Celery/billiard's pool.

1. **Signal handling.** Current gunicorn queues signals in a `queue.SimpleQueue` and has its loop wait on it with a timeout (`wait_for_signals`), with no cap. (Older gunicorn versions used a pipe plus `select` and a 5-element queue, the classic self-pipe trick.) Kiln uses Python's `signal.set_wakeup_fd`, so the selector that watches our sockets also wakes on signals. One wait covers sockets, timers and signals. gunicorn's master doesn't serve sockets, so it doesn't need that.
2. **Reaping and exit codes.** gunicorn's `reap_workers` decodes `waitpid` with `WIFEXITED`/`WEXITSTATUS`/`WIFSIGNALED`. It treats `WORKER_BOOT_ERROR = 3` and `APP_LOAD_ERROR = 4` specially: both halt the server. For a SIGKILL it logs "Perhaps out of memory?", which is the same suspicion as our `suspected_oom`, but without the cgroup evidence. Kiln uses 3 the same way, keeps 4 as the master's "gave up" code, and backs off exponentially before giving up instead of halting at once.
3. **Hung workers.** gunicorn's `murder_workers` compares `time.monotonic()` with a timestamp the worker updates (`worker.tmp.last_update()`). It first sends SIGABRT, then SIGKILL if the worker is still stuck. Kiln uses heartbeat messages on the channel instead. Both need a separate thread (or loop) in the worker to keep running. gunicorn's SIGABRT first gives a worker the chance to dump a traceback, which Kiln could adopt in K6.
4. **Reload.** gunicorn spawns all new workers first, then `manage_workers` kills the surplus, oldest first, with SIGTERM, and waits up to `graceful_timeout`. Kiln retires one old worker per new READY worker. Neither can promise zero dropped *requests* in gunicorn's case, because its workers accept from a shared socket, and connections already queued in a dying worker's accept backlog can be cut. In Kiln the queue never leaves the master (ADR-007), which is what made SC-2 straightforward.
5. **Leaving the child.** gunicorn's child calls `sys.exit(0)` and re-raises `SystemExit`, relying on its call stack to end the process. Kiln's child ends with `os._exit` in a `finally` (ADR-028), so it can never unwind into master code or run the master's atexit handlers. `test_pidfile_survives_worker_deaths` pins this down.
6. **Celery/billiard.** The prefork pool notices a vanished child and fails its task with `WorkerLostError` ("Worker exited prematurely: signal 9 (SIGKILL)"). It recycles workers with `maxtasksperchild` and `max_memory_per_child`. Back Market saw only "worker lost" because nothing connected that SIGKILL to the cgroup's `oom_kill` counter, and Kubernetes reported no OOM event. Kiln's `oom_evidence` field is that missing link.
7. **"I like Unicorn because it's Unix."** We used the same Unix building blocks: `fork` for workers, signals as the control API (HUP, TTIN, TTOU, QUIT), a self-pipe-style wakeup, and `waitpid` for reaping.

## 11. Limitations and threats to validity

- **3 repeats instead of ≥ 10** (spec/10 §3), and no bootstrap CIs. Medians and min–max are honest but rough. The ceiling cells overlap a lot from 2 workers upward, so "flat" is the only safe conclusion, not the small differences between cells. K9 re-runs these with full statistics.
- **Master CPU% > 100% in some cells.** CPU time was read before and after `run_load()`, which includes opening and closing 256 connections, while the wall time covers only the load. Treat these numbers as "about one full core".
- **WSL2 laptop.** Other Windows activity can add noise, and no CPU pinning was used.
- **Load generator on the same machine.** The asyncio load generator competes with the master for CPU. Each process is single-threaded, so both fit on separate cores of 14, but the generator could become the limit before the master at very high rates.
- **OOM lab ran once.** The kill is deterministic (700 MiB in a 512 MiB scope), but its timing isn't.
- **Lab (d) samples heartbeat age every 0.2 s,** so the max gap is accurate to about ±0.2 s.

## 12. What you should now be able to explain

- [ ] What `os.fork()` copies, and why the child must close inherited fds and end with `os._exit` (*Processes*)
- [ ] How a signal becomes an event in the loop (wakeup fd), and why handlers only record (*Processes, signals*)
- [ ] How a wait status separates "exit code N" from "killed by signal S", and why an unexplained SIGKILL is only *suspected* OOM (*Processes*)
- [ ] Why TCP needs framing, and how the decoder handles partial reads (*Networking*)
- [ ] How one thread serves many sockets with a selector and output buffers (*Concurrency*)
- [ ] Why zero-drop reload is easy when the queue lives in the master, and what the two race fixes guard against (*Systems design*)
- [ ] Why the master is the throughput ceiling, and why more concurrency mostly adds latency (*Performance*)
- [ ] Why a GIL-holding C call silences the heartbeat but a Python loop doesn't, and what changes on 3.15t (*Concurrency, GIL*)
- [ ] At-most-once vs at-least-once, and when retries are safe (*Systems design*)

## 13. Checkpoint quiz

1. The deaths lab shows `oom_confirmed=false` for `sigkill_self` but `true` for the real OOM. Where does that difference come from?
   <details><summary>Answer</summary>

   From the cgroup's `memory.events` file. The kernel increments its `oom_kill` counter only when *it* kills a process for memory. Kiln reads the counter at startup and after every unexplained SIGKILL. A self-sent SIGKILL leaves it unchanged, while a real OOM kill moves it from 0 to 1.

   </details>
2. Why did the first OOM run end with the master shutting down, even though Kiln detected the OOM kill correctly?
   <details><summary>Answer</summary>

   The master was running inside a systemd scope, and systemd's default `OOMPolicy=stop` stops the whole unit (SIGTERM to every process) after any OOM kill in it. The master received SIGTERM and shut down gracefully. Running the scope with `-p OOMPolicy=continue` fixed the lab (errata E-18).

   </details>
3. In lab (b), throughput was about 700 jobs/s with reloads and without. What limited it, the master or the workers? How do you know?
   <details><summary>Answer</summary>

   The workers. Each `labs.sleep ms=5` job takes about 5.6 ms including overhead, and 4 workers can therefore do about 700 per second, which is what we measured. Lab (c) shows the master can handle about 10,000 per second, so it had plenty of spare capacity.

   </details>
4. Why does adding workers beyond 2 not raise throughput in lab (c)?
   <details><summary>Answer</summary>

   The master is already using about one full CPU core. Every job passes through its single thread (decode the submit, dispatch, decode the result, answer the wait), so more workers just wait for the master. That's the trade-off ADR-007 accepted in exchange for knowing exactly where every job is.

   </details>
5. Why did the negative control lose jobs, and why was it worth running?
   <details><summary>Answer</summary>

   With a 1-second grace period and 3-second jobs, a retiring worker can't finish in time, so the master SIGKILLs it and its in-flight jobs become `LOST`. It proves the measurement is capable of detecting drops. Without it, "0 lost" might only mean the test couldn't see losses.

   </details>
6. Before the "drain before resolving" fix, how could a job that had *finished* end up `LOST`?
   <details><summary>Answer</summary>

   The worker sent its result and exited. In the same pass of the master's loop, the signal pipe (SIGCHLD) was handled before the worker's socket. The master reaped the worker, saw a job still in flight, and declared it lost, while the result sat unread in the kernel's socket buffer. Reading the dead worker's channel first fixed it.

   </details>
