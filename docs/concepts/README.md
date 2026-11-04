# Concept Primers: Index and Reading Order

> Primers explain systems and runtime ideas from zero ([standards/documentation.md §2](../standards/documentation.md#2-concept-primers-docsconcepts)). Each one is written **in its milestone, before the code that uses it**. Read them in this order, since later primers assume earlier ones.
> Status: ⏳ planned · ✍️ written · ✅ reviewed by the learner

| # | Primer (`docs/concepts/<slug>.md`) | Milestone | Status |
|---|---|---|---|
| 1 | [`terminal-and-wsl`](terminal-and-wsl.md) | K0 | ✍️ |
| 2 | [`git-basics`](git-basics.md) | K0 | ✍️ |
| 3 | [`what-is-a-process`](what-is-a-process.md) | K0 | ✍️ |
| 4 | [`files-and-file-descriptors`](files-and-file-descriptors.md) | K0 | ✍️ |
| 5 | [`what-is-a-server`](what-is-a-server.md) | K0 | ✍️ |
| 6 | [`fork`](fork.md) | K1 | ✍️ |
| 7 | [`fork-and-threads`](fork-and-threads.md) | K1 | ✍️ |
| 8 | [`signals`](signals.md) | K1 | ✍️ |
| 9 | [`exit-status`](exit-status.md) | K1 | ✍️ |
| 10 | [`zombies-and-reaping`](zombies-and-reaping.md) | K1 | ✍️ |
| 11 | [`sockets-and-socketpairs`](sockets-and-socketpairs.md) | K1 | ✍️ |
| 12 | [`framing`](framing.md) | K1 | ✍️ |
| 13 | [`non-blocking-io-and-selectors`](non-blocking-io-and-selectors.md) | K1 | ✍️ |
| 14 | [`event-loops`](event-loops.md) (selectors part; K5 adds the asyncio part) | K1, K5 | ✍️ |
| 15 | [`state-machines`](state-machines.md) | K1 | ✍️ |
| 16 | [`delivery-semantics`](delivery-semantics.md) | K1 | ✍️ |
| 17 | [`memory-metrics`](memory-metrics.md) (RSS; extended in K3 with PSS/USS/shared/private) | K1, K3 | ✍️ |
| 18 | [`cgroups-and-oom-killer`](cgroups-and-oom-killer.md) | K1 | ✍️ |
| 19 | `observability` (deferred: the logging format is taught in spec/06 and python/logging-basics for now) | K1 | ⏳ |
| 20 | `testing-multiprocess-code` (deferred: the code tour's stop on tests/support/kiln_helpers.py covers it for now) | K1 | ⏳ |
| 21 | [`start-methods`](start-methods.md) | K2 | ✍️ |
| 22 | [`pickling`](pickling.md) | K2 | ✍️ |
| 23 | [`main-module`](main-module.md) | K2 | ✍️ |
| 24 | [`import-system`](import-system.md) | K2 | ✍️ |
| 25 | [`fd-passing`](fd-passing.md) | K2 | ✍️ |
| 26 | [`reparenting-and-orphans`](reparenting-and-orphans.md) | K2 | ✍️ |
| 27 | [`ast-and-symtable`](ast-and-symtable.md) | K2 | ✍️ |
| 28 | [`static-vs-dynamic-analysis`](static-vs-dynamic-analysis.md) | K2 | ✍️ |
| 29 | [`virtual-memory-and-pages`](virtual-memory-and-pages.md) | K3 | ✍️ |
| 30 | [`copy-on-write`](copy-on-write.md) | K3 | ✍️ |
| 31 | [`smaps`](smaps.md) | K3 | ✍️ |
| 32 | [`refcounting`](refcounting.md) | K3 | ✍️ |
| 33 | [`garbage-collector`](garbage-collector.md) | K3 | ✍️ |
| 34 | [`gc-freeze`](gc-freeze.md) | K3 | ✍️ |
| 35 | [`immortal-objects`](immortal-objects.md) | K3 | ✍️ |
| 36 | [`finalization-and-atexit`](finalization-and-atexit.md) | K3 | ✍️ |
| 37 | [`global-interpreter-state`](global-interpreter-state.md) | K3 | ✍️ |
| 38 | `benchmarking-statistics` (deferred to K9: K3's lab reports medians and bootstrap CIs computed in its driver, explained in the lab report) | K3, K9 | ⏳ |
| 39 | [`threads`](threads.md) | K4 | ✍️ |
| 40 | [`gil`](gil.md) | K4 | ✍️ |
| 41 | [`free-threading`](free-threading.md) | K4 | ✍️ |
| 42 | [`subinterpreters`](subinterpreters.md) | K4 | ✍️ |
| 43 | [`data-races`](data-races.md) | K4 | ✍️ |
| 44 | [`thread-safety-of-builtins`](thread-safety-of-builtins.md) | K4 | ✍️ |
| 45 | [`c-extensions`](c-extensions.md) | K4 | ✍️ |
| 46 | [`cpu-vs-io-bound`](cpu-vs-io-bound.md) | K4 | ✍️ |
| 47 | [`amdahls-law`](amdahls-law.md) | K4 | ✍️ |
| 48 | `coroutines-and-tasks` | K5 | ⏳ |
| 49 | `cancellation` | K5 | ⏳ |
| 50 | `taskgroup` | K5 | ⏳ |
| 51 | `http-1-1-basics` | K5 | ⏳ |
| 52 | `backpressure` | K5 | ⏳ |
| 53 | `blocking-the-event-loop` | K5 | ⏳ |
| 54 | `memory-leaks-in-gc-languages` | K6 | ⏳ |
| 55 | `tracemalloc` | K6 | ⏳ |
| 56 | `weakref` | K6 | ⏳ |
| 57 | `sampling-vs-tracing-profilers` | K6 | ⏳ |
| 58 | `remote-debugging-pep768` | K6 | ⏳ |
| 59 | `flamegraphs` | K6 | ⏳ |
| 60 | `generational-gc` | K7 | ⏳ |
| 61 | `incremental-gc` | K7 | ⏳ |
| 62 | `pause-time-vs-throughput-vs-memory` | K7 | ⏳ |
| 63 | `reference-cycles-in-frameworks` | K7 | ⏳ |
| 64 | `profiling-before-optimizing` | K8 | ⏳ |
| 65 | `c-api-basics` | K8 | ⏳ |
| 66 | `ffi-boundary-cost` | K8 | ⏳ |
| 67 | `pyo3-and-maturin` | K8 | ⏳ |
| 68 | `stable-abi-abi3-abi3t` | K8 | ⏳ |
| 69 | `bootstrap` | K9 | ⏳ |
| 70 | `confidence-intervals` | K9 | ⏳ |
| 71 | `experiment-design` | K9 | ⏳ |
| 72 | `t-strings-and-injection` | K9 | ⏳ |

Incident write-ups (the research §2 stories, retold for beginners and linked from `# INCIDENT(<id>):` comments) live in [`../research/incidents.md`](../research/incidents.md), written in K0.
