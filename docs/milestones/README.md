# Kiln Roadmap and Milestones

> Milestones are done **in order**. Each one builds on the last. Each one has a spec in this folder that follows [`../templates/milestone-spec.md`](../templates/milestone-spec.md), a lab report in `docs/labs/` and a code tour in `docs/tours/`.
> Procedure and Definition of Done: [`../standards/milestone-workflow.md`](../standards/milestone-workflow.md).

## Status

| # | Milestone | Spec | Status | Branch | Lab report | Tour |
|---|---|---|---|---|---|---|
| K0 | Foundations: environment, tooling, first primers | [K0](K0-foundations.md) | ✅ done | `milestone/k0-foundations` | — (setup log: appendix of `tours/K0.md`) | [tour](../tours/K0.md) |
| K1 | Supervisor: master/worker, signals, queue, reload, recycling, OOM detection | [K1](K1-supervisor.md) | ✅ done | `milestone/k1-supervisor` | `K1-supervisor.md` | `K1.md` |
| K2 | Start methods + ★ fork-assumption checker | [K2](K2-start-methods-forkcheck.md) | ✅ done | `milestone/k2-start-methods` | `K2-forkcheck.md` | `K2.md` |
| K3 | Copy-on-write lab (Instagram) | [K3](K3-copy-on-write.md) | 👀 awaiting learner review | `milestone/k3-cow` | `K3-cow.md` | `K3.md` |
| K4 | Concurrency backends + free-threading | [K4](K4-concurrency-backends.md) | 📝 specified | `milestone/k4-concurrency` | `K4-concurrency.md` | `K4.md` |
| K5 | Async gateway + event-loop watchdog | [K5](K5-gateway-watchdog.md) | 📝 specified | `milestone/k5-gateway` | `K5-blocked-loop.md` | `K5.md` |
| K6 | Leak lab | [K6](K6-leak-lab.md) | 📝 specified | `milestone/k6-leaks` | `K6-leaks.md` | `K6.md` |
| K7 | GC regression replay (3.14 incremental GC) | [K7](K7-gc-regression.md) | 📝 specified | `milestone/k7-gc` | `K7-gc-regression.md` | `K7.md` |
| K8 | Rust hot path (PyO3) | [K8](K8-rust-hot-path.md) | 📝 specified | `milestone/k8-rust` | `K8-rust.md` | `K8.md` |
| K9 | Matrix report (stats + t-string HTML) | [K9](K9-matrix-report.md) | 📝 specified | `milestone/k9-matrix` | `K9-matrix.md` | `K9.md` |
| K10 | Capstone: acceptance + retrospective | [K10](K10-capstone.md) | 📝 specified | `milestone/k10-capstone` | `K10-capstone.md` | `K10.md` |

Legend: 📝 specified · 🚧 in progress · 👀 awaiting learner review · ✅ done

> **Note for implementers:** where a milestone spec says "step N adds X to spec/0M", the spec pass has **already applied** that change, so the spec files are the source of truth. Check that the text is there rather than adding it again, and update it only if implementation shows it was wrong (with an ADR).

## Dependency graph

```
K0 ──▶ K1 ──┬──▶ K2 ──────────────────────────────┐
            ├──▶ K3 ──▶ (gcwatch) ──▶ K7 ─────────┤
            ├──▶ K4 ──────────────────────────────┤
            ├──▶ K5 ──▶ K6 ───────────────────────┼──▶ K9 ──▶ K10
            └──▶ K8 ──────────────────────────────┘
```

- **K1 is the trunk.** Everything else needs a working master/worker system.
- K2–K8 could in principle go in any order after K1. We use **numeric order** because each one's primers assume the earlier ones. For example K3's copy-on-write primer assumes K2's fork/spawn primer. **Don't reorder without an ADR.**
- K6 uses K5's gateway for `/debug/*` endpoints. The `kiln debug` CLI is the fallback.
- K7 reuses K3's `memory/gcwatch.py`.
- K9 aggregates the ExperimentRecords produced by K3–K8.

## Rough size (implementation steps)

| Milestone | Steps | Relative size | Why |
|---|---|---|---|
| K0 | 7 | M | mostly docs and tooling, plus the Python primer starter set |
| K1 | 10 | **XL** | the core system: event loop, protocol, supervisor |
| K2 | 9 | L | three launchers plus a static analyzer plus a corpus |
| K3 | 6 | M | measurement-heavy, little new code |
| K4 | 8 | L | four executors, a C extension, a race demo |
| K5 | 8 | L | HTTP parser, backpressure, watchdog |
| K6 | 6 | M | leaks and tooling walkthroughs |
| K7 | 5 | M | workload plus cross-version runs |
| K8 | 6 | M | Rust toolchain, PyO3, boundary benchmarks |
| K9 | 6 | M | stats, runner, renderer |
| K10 | 4 | S | acceptance runs and retrospective |

## Knowledge-map coverage (research §3 → milestone)

| Area | Primary milestone(s) | Key primers |
|---|---|---|
| Data model (dunders, descriptors, `__reduce__`, context managers, decorators) | K0 (basics), K1, K2 | `python/dunder-methods`, `python/decorators`, `concepts/pickling` |
| Runtime internals (refcount, GC, immortal objects, finalization, atexit) | K3, K6, K7 | `concepts/refcounting`, `concepts/garbage-collector`, `concepts/finalization-and-atexit` |
| Import system (`sys.modules`, `__main__`, side effects) | K2 | `concepts/import-system`, `concepts/main-module` |
| Processes, signals, start methods | K1, K2 | `concepts/fork`, `concepts/signals`, `concepts/start-methods` |
| Threads, GIL, free-threading, subinterpreters | K4 | `concepts/gil`, `concepts/free-threading`, `concepts/subinterpreters`, `concepts/data-races` |
| asyncio | K4, K5 | `concepts/event-loops`, `concepts/asyncio-internals`, `concepts/backpressure` |
| Memory & profiling | K3, K6 | `concepts/memory-metrics`, `concepts/tracemalloc`, `concepts/sampling-vs-tracing` |
| Native code | K4 (C ext), K8 | `concepts/c-extensions`, `concepts/ffi-boundary-cost`, `concepts/stable-abi` |
| Testing & statistics | K0, K9 | `concepts/benchmarking-statistics`, `python/pytest-basics` |
