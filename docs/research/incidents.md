# Incident Registry

Kiln is built around **real production incidents**, problems that real companies hit with Python ([research notes §2](python-mastery-research.md#2-field-notes-what-went-wrong-at-real-companies)). This file gives each incident a short, stable **id**.

**How the ids are used.** When a line of Kiln code exists *because of* one of these incidents, either to reproduce it or to prevent it, the line carries a comment tag like this:

```python
# INCIDENT(instagram-cow): gc.freeze() right before fork, the recipe from research §2.1
```

The tag makes the connection between code and history searchable (`grep -rn "INCIDENT(" src labs`). `scripts/check_docs.py` (check DOC007) verifies that every id used in a tag has a heading in this file. Each heading below **is** the id, so the link anchor is the same id: `incidents.md#instagram-cow`.

To add an incident, add a `##` heading whose text is the new id (lowercase, hyphens), then a summary, a source link and the milestone. Don't rename existing ids, because code refers to them.

| Id | Short name | Kiln milestone |
|---|---|---|
| [instagram-cow](#instagram-cow) | GC breaks copy-on-write sharing | K3 |
| [gc-revert-314](#gc-revert-314) | 3.14 incremental GC reverted | K7 |
| [dropbox-typecheck](#dropbox-typecheck) | Type-checking 4M lines | — (Cartographer C4) |
| [pyrefly](#pyrefly) | Type checkers move to Rust | — (Cartographer C4) |
| [lazy-imports](#lazy-imports) | PEP 810 lazy imports | K9 (startup suite) |
| [blocked-loop](#blocked-loop) | Blocking calls on the event loop | K5 |
| [celery-oom](#celery-oom) | Pre-fork workers OOM-killed silently | K1 |
| [snyk-leak](#snyk-leak) | Memory leak in a long-running service | K6 |
| [forkserver-default](#forkserver-default) | 3.14 changed the default start method | K2 |
| [free-threading](#free-threading) | Free-threaded Python and the GIL | K4 |
| [rust-hot-path](#rust-hot-path) | Rewriting a hot function in Rust | K8 |
| [ultralytics](#ultralytics) | CI injection → poisoned releases | — (Cartographer C5–C7) |
| [language-changes](#language-changes) | 3.14 → 3.15 language changes | K9 (t-strings), K2/K9 |

---

## instagram-cow

Instagram's servers loaded Django once in a master process, then forked workers that were meant to *share* that memory (copy-on-write). But CPython writes to an object's memory even when you only *read* it (reference counts, plus the garbage collector's bookkeeping), so the shared pages were copied anyway, and shared memory per worker fell from ~250 MB to ~140 MB. Disabling the GC, and later `gc.freeze()`, won back roughly 10% capacity.
**Source:** [research §2.1](python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing) · **Reproduced in:** K3 (copy-on-write lab)

## gc-revert-314

Python 3.14.0–3.14.4 shipped an *incremental* garbage collector with much shorter pauses, but it could let memory grow to as much as 2.7× its old peak with cycle-heavy programs. A Django `migrate` command was OOM-killed on a 512 MB server. 3.14.5 went back to the old collector.
**Source:** [research §2.2](python-mastery-research.md#22-python-314s-incremental-gc-was-reverted-2026) · **Reproduced in:** K7 (GC regression replay)

## dropbox-typecheck

Dropbox type-checked about 4 million lines of Python. Hundreds of modules caught in circular imports (a "tangle") made checking slow until they added caching, a daemon and compilation with mypyc.
**Source:** [research §2.3](python-mastery-research.md#23-dropbox-type-checking-4-million-lines) · **Reproduced in:** not in Kiln (Cartographer C1/C4). Kiln uses `mypy --strict` (ADR-019)

## pyrefly

Meta's Pyrefly (1.0, May 2026) and Astral's ruff/uv/ty show Python tooling moving to Rust for speed. Different type checkers still disagree on the same code.
**Source:** [research §2.4](python-mastery-research.md#24-meta-pyre--pyrefly-10-the-tooling-moves-to-rust) · **Reproduced in:** not in Kiln (Cartographer C4). Kiln *uses* ruff and uv

## lazy-imports

Python 3.15 added `lazy import` (PEP 810). A module is only really imported the first time it's used, which the PEP reports can cut startup time by 50–70% for command-line tools. The catch is that import errors now appear at first use instead of at the import line.
**Source:** [research §2.5](python-mastery-research.md#25-startup-time-and-lazy-imports-pep-810-shipped-in-315) · **Reproduced in:** K9 (`core.startup` suite, gated to 3.15)

## blocked-loop

A PDF-ingestion service called slow, ordinary ("blocking") code inside `async def` functions, freezing the event loop that serves every request. Moving those calls to `asyncio.to_thread()` gave about 31–36% more throughput and 24–27% lower worst-case latency.
**Source:** [research §2.6](python-mastery-research.md#26-asyncio-blocking-calls-on-the-event-loop) · **Reproduced in:** K5 (gateway + event-loop watchdog)

## celery-oom

Celery's pre-fork workers multiplied memory use by the number of workers. At Back Market, containers hit their memory limit and the kernel killed workers with SIGKILL, and the only clue anyone saw was "worker lost". The fixes were recycling (`max-tasks-per-child`, `max-memory-per-child`) and detecting the cause of death.
**Source:** [research §2.7](python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources) · **Reproduced in:** K1 (supervisor: OOM detection in a memory-limited cgroup)

## snyk-leak

A long-running Fugue/Snyk service slowly leaked memory through the `requests` library, from about 8% to 20% of the machine. Diffing `tracemalloc` snapshots found it, and switching to the stdlib's `urllib` fixed it. Bloomberg's Memray traces native allocations too.
**Source:** [research §2.8](python-mastery-research.md#28-memory-leaks-in-long-running-services) · **Reproduced in:** K6 (leak lab; planted leaks *modelled after* this, see ADR-016)

## forkserver-default

Python 3.14 changed the Linux default way `multiprocessing` starts processes from `fork` to `forkserver`. Code that silently relied on `fork`, with no `__main__` guard, nested functions as targets, or logging set up only in the parent, broke in krb5, oslo.concurrency and FSL pyfeeds.
**Source:** [research §2.9](python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver), [ADR-017](../decisions.md#adr-017) · **Reproduced in:** K2 (start methods + the ★ fork-assumption checker)

## free-threading

Python 3.14 officially supports a "free-threaded" build without the GIL, so threads can run Python code in parallel. But importing a C extension that hasn't declared free-threading support switches the GIL back on, and code that was "accidentally safe" under the GIL can now race.
**Source:** [research §2.9](python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver) · **Reproduced in:** K4 (concurrency backends, gilwatch, the planted race)

## rust-hot-path

Teams speed up Python by rewriting a small hot function in Rust. Pydantic v2 got 5–50× faster this way, and one profiled function went from 293 ms to 2.9 ms per iteration. But many tiny calls across the Python↔Rust boundary can cancel out the gain.
**Source:** [research §2.10](python-mastery-research.md#210-native-speed-rust-and-c-extensions) · **Reproduced in:** K8 (Rust hot path)

## ultralytics

In December 2024, an attacker put a shell command inside a git branch name. An unsafe GitHub Actions workflow ran it, stole tokens, and the project's own release pipeline then published malicious versions to PyPI, complete with valid attestations.
**Source:** [research §2.11](python-mastery-research.md#211-supply-chain-the-ultralytics-attack-and-pypi-in-2025) · **Reproduced in:** not in Kiln (Cartographer C5–C7). Relevant to Kiln only as a reason it has no remote CI (ADR-025)

## language-changes

Python 3.14 and 3.15 added t-strings (PEP 750, templates that keep text and values apart, so values can be escaped safely), deferred annotations, `lazy import`, `frozendict`, UTF-8 by default and more. Code written for older versions can behave differently.
**Source:** [research §2.12](python-mastery-research.md#212-the-language-keeps-changing-314--315) · **Reproduced in:** K9 (HTML report built with t-strings), and in Kiln's version gates (ADR-004)
