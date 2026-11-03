# 00 — Kiln Overview: What We Are Building and Why

> **Status:** Approved spec (spec pass, 2026-11-03). **Audience:** you, the learner, plus any implementer.
> **Read this first.** Everything else in `docs/spec/` adds detail to this page.

---

## 1. Kiln in one paragraph

Kiln is a small **job server** written in Python. A *job* is a request like "run the function `resize_image` with these arguments". Programs called *clients* send jobs to Kiln. Kiln has one boss process, the **master**, and several helper processes called **workers**. The master keeps a waiting line of jobs (the **queue**) and hands each job to a free worker. The worker runs the function and sends the result back. The master stores the result until the client asks for it.

This is the same basic design as **gunicorn**, which runs Python web apps, and **Celery**, which runs Python background jobs. Instagram, Dropbox, Wikimedia and many others run them in production.

We build Kiln **from scratch** to learn, not to replace those tools. Each milestone recreates a real problem that a real company hit in production (see [the research notes](../research/python-mastery-research.md) §2). We make the problem happen, measure it, fix it, measure again, and then compare our fix with how the professional tools solved it.

## 2. Why this project teaches so much

A job server sits right where several hard topics meet:

| To make Kiln work, you need to understand... | Because... |
|---|---|
| Processes, `fork`, signals, exit codes | the master creates workers, supervises them and replaces them when they die |
| File descriptors, sockets, pipes, `selectors` | the processes talk to each other and to clients through these |
| Pickling, `__main__`, the import system | the three ways to start a worker (fork, forkserver, spawn) break programs in different ways |
| Reference counting, the garbage collector, copy-on-write memory | sharing memory between forked workers is the Instagram story |
| Threads, the GIL, free-threading, subinterpreters | these are the different ways one worker can run several jobs at once |
| `asyncio` | the HTTP front door has to handle thousands of connections without blocking |
| `tracemalloc`, profilers, remote debugging | long-running servers leak memory, and you need to find out where |
| Rust + PyO3 | it shows when native code actually speeds things up, and when it doesn't |
| Statistics and experiment design | "it got faster" means nothing without repeated runs and confidence intervals |

## 3. Goals

1. **G1, learning (primary).** After finishing all milestones, you can explain every row of the knowledge map in research §3 that Kiln covers, *without notes*. You can also point at the exact Kiln code and lab report that demonstrates each one.
2. **G2, a working system.** Kiln really works. It runs 10,000 jobs under load, reloads its workers with **zero dropped jobs**, survives workers being killed, and detects when the operating system killed a worker for using too much memory.
3. **G3, reproduced incidents.** Every milestone from K1 to K9 contains a lab that reproduces its incident, measures it, applies a fix and measures again. Results are recorded in a lab report with real numbers.
4. **G4, one novel tool.** K2's **fork-assumption checker** (★) finds code that only works with the `fork` start method. It catches all three real-world breakages in our calibration corpus.
5. **G5, everything documented.** A reader who is **new to Python** can understand every file by reading the docs and comments alone. This is a hard requirement. It is defined in [`../standards/documentation.md`](../standards/documentation.md) and checked automatically where possible.

## 4. Non-goals (things Kiln deliberately does **not** do)

Writing these down settles a lot of "should we also...?" questions before they come up.

| Non-goal | Why we skip it | What happens instead |
|---|---|---|
| **Durability.** Jobs survive a master crash | That needs a database or write-ahead log, which is a different project | If the master dies, all queued jobs and results are lost. The docs and the client errors say so |
| **Multiple machines** | Distributed systems are a separate subject | Single host only. Everything binds to `127.0.0.1` |
| **Authentication / TLS** | Security of a network service isn't a learning goal here | localhost-only, and debug endpoints are off by default ([ADR-021](../decisions.md#adr-021)) |
| **Pickle payloads** (sending arbitrary Python objects) | `pickle` can run arbitrary code, and it ties the payload format to the start method | Jobs carry **JSON only** ([ADR-009](../decisions.md#adr-009)) |
| **Priorities, scheduling, cron, job chains** | They add a lot of features without much learning | One FIFO (first-in, first-out) queue |
| **Hot code upgrade of the master itself** (unicorn's `USR2`) | Too niche | `SIGHUP` reloads *workers* only |
| **Windows-native execution** | `fork`, `/proc` and the signals we use don't exist on Windows | Kiln runs in **WSL2 Debian** ([ADR-003](../decisions.md#adr-003)) |
| **Production readiness** | It's a learning project | We want correct, measurable and explainable code, not feature-complete code |
| **Cancelling a job that is already running** | Killing a running Python function safely is very hard (and that difficulty is a lesson in itself) | Only *queued* jobs can be cancelled. Running jobs can only be timed out, which kills the worker |

## 5. Success criteria ("Kiln is done when...")

These come from research §4 and have been made measurable. **K10 (Capstone)** checks all of them.

| ID | Criterion | How it is measured |
|---|---|---|
| SC-1 | Handles **10,000 jobs** under load | `kiln bench load --jobs 10000 --concurrency 64` finishes with 0 errors. The lab report records throughput and p50/p99 latency |
| SC-2 | **Zero dropped jobs** during reload | The same load test sends `SIGHUP` 5 times during the run. Every submitted job reaches a terminal state, with **no** `LOST` or `CANCELLED` outcomes |
| SC-3 | Detects **OOM-killed** workers | In the K1 lab, a worker is killed by the kernel's OOM killer inside a memory-limited cgroup. Kiln logs it as `killed by SIGKILL (suspected OOM)` and marks its job `LOST(worker_lost)` |
| SC-4 | A **before/after write-up** for every milestone | Each of K1–K9 has a `docs/labs/Kn-*.md` filled in according to the lab template, with real numbers |
| SC-5 | K2 catches all three **real breakages** | The checker reports at least one correct finding on each of the krb5, oslo.concurrency and FSL pyfeeds distilled cases, and each finding is confirmed by a run under spawn/forkserver |
| SC-6 | **Documentation complete** | `scripts/check_docs.py` passes, and every concept named in a milestone has a primer |
| SC-7 | **Tests pass** on 3.13, 3.14, 3.15 and 3.15t | `scripts/dev.sh test-matrix` is green. Version-gated tests are skipped with a reason on older versions |

## 6. The milestones at a glance

The full roadmap, with dependencies, is in [`../milestones/README.md`](../milestones/README.md).

| # | Name | One-line summary |
|---|---|---|
| K0 | Foundations | Set up the environment and tooling, write the first Python and Linux primers, and build `kiln doctor` |
| K1 | Supervisor | The master and worker processes: fork, signals, the queue, reload, recycling, OOM detection |
| K2 | Start methods + ★ fork-checker | fork / forkserver / spawn launchers, plus a static checker for fork-only assumptions |
| K3 | Copy-on-write lab | Instagram's GC and copy-on-write problem: measure shared memory, apply `gc.freeze` |
| K4 | Concurrency backends | thread / subinterpreter / asyncio executors, free-threading, finding what re-enables the GIL, a planted race |
| K5 | Async gateway + loop watchdog | A hand-written asyncio HTTP front door with backpressure, plus a detector for a blocked event loop |
| K6 | Leak lab | Planted memory leaks found with tracemalloc, Memray, Tachyon and `pdb -p` |
| K7 | GC regression replay | The 3.14 incremental-GC memory blowup, replayed across interpreter versions |
| K8 | Rust hot path | A PyO3 validator, and measuring the cost of crossing the language boundary |
| K9 | Matrix report | Every benchmark across every interpreter, with confidence intervals, as an HTML report built with t-strings |
| K10 | Capstone | Acceptance tests for SC-1…SC-7 and a final retrospective |

## 7. How you (the learner) use this repo

The implementer writes the code ([ADR-001](../decisions.md#adr-001)). Your job is to **understand** it. For each milestone:

1. **Read** the milestone spec (`docs/milestones/Kn-*.md`), especially "Story" and "What you will learn".
2. **Predict.** Each lab report has a box labelled *"Your prediction"*. Fill it in **before** you look at the results. Guessing wrong and then finding out why is the fastest way to learn.
3. **Read the primers** the milestone links to (`docs/concepts/`, `docs/python/`).
4. **Follow the code tour** (`docs/tours/Kn.md`). It tells you which file to open first, then next, and what to look for in each.
5. **Run the lab yourself.** Copy and paste the commands in the lab report and check you get similar numbers.
6. **Take the checkpoint quiz** at the end of the milestone. The answers are hidden in collapsible sections.
7. **Ask.** Anything still unclear is a documentation bug. Write it down in `docs/questions.md`. The next implementation round must answer it in the docs, not only in chat.

## 8. Where things live

| Path | Contents |
|---|---|
| `docs/spec/` | **What** the system is (this spec) |
| `docs/milestones/` | **The order** we build it in, and what each step delivers |
| `docs/decisions.md` | **Why** we chose what we chose (Architecture Decision Records) |
| `docs/standards/` | **The rules** for documentation, code and workflow |
| `docs/concepts/` | Primers on systems and runtime concepts (fork, GC, GIL...) |
| `docs/python/` | Primers on Python language features (classes, `with`, decorators...) |
| `docs/tours/` | Guided reading order through the code, one per milestone |
| `docs/labs/` | Experiment reports (hypothesis → numbers → conclusion) |
| `docs/glossary.md` | Every term, in plain English |
| `docs/research/` | The original research notes plus an errata list |
| `src/kiln/` | The code (created in K0) |
| `labs/` | Lab workloads and scripts (created per milestone) |
| `corpus/` | Code samples the K2 fork-checker is calibrated against |
