# Concept Primers: Index and Reading Order

> Primers explain systems and runtime ideas from zero ([standards/documentation.md §2](../standards/documentation.md#2-concept-primers-docsconcepts)). Each one is written **in its milestone, before the code that uses it**. Read them in this order, since later primers assume earlier ones.
> Status: ⏳ planned · ✍️ written · ✅ reviewed by the learner

| # | Primer (`docs/concepts/<slug>.md`) | Milestone | Status |
|---|---|---|---|
| 1 | `terminal-and-wsl` | K0 | ⏳ |
| 2 | `git-basics` | K0 | ⏳ |
| 3 | `what-is-a-process` | K0 | ⏳ |
| 4 | `files-and-file-descriptors` | K0 | ⏳ |
| 5 | `what-is-a-server` | K0 | ⏳ |
| 6 | `fork` | K1 | ⏳ |
| 7 | `fork-and-threads` | K1 | ⏳ |
| 8 | `signals` | K1 | ⏳ |
| 9 | `exit-status` | K1 | ⏳ |
| 10 | `zombies-and-reaping` | K1 | ⏳ |
| 11 | `sockets-and-socketpairs` | K1 | ⏳ |
| 12 | `framing` | K1 | ⏳ |
| 13 | `non-blocking-io-and-selectors` | K1 | ⏳ |
| 14 | `event-loops` (selectors part; K5 adds the asyncio part) | K1, K5 | ⏳ |
| 15 | `state-machines` | K1 | ⏳ |
| 16 | `delivery-semantics` | K1 | ⏳ |
| 17 | `memory-metrics` (RSS; K3 extends with PSS/USS) | K1, K3 | ⏳ |
| 18 | `cgroups-and-oom-killer` | K1 | ⏳ |
| 19 | `observability` | K1 | ⏳ |
| 20 | `testing-multiprocess-code` | K1 | ⏳ |
| 21 | `start-methods` | K2 | ⏳ |
| 22 | `pickling` | K2 | ⏳ |
| 23 | `main-module` | K2 | ⏳ |
| 24 | `import-system` | K2 | ⏳ |
| 25 | `fd-passing` | K2 | ⏳ |
| 26 | `reparenting-and-orphans` | K2 | ⏳ |
| 27 | `ast-and-symtable` | K2 | ⏳ |
| 28 | `static-vs-dynamic-analysis` | K2 | ⏳ |
| 29 | `virtual-memory-and-pages` | K3 | ⏳ |
| 30 | `copy-on-write` | K3 | ⏳ |
| 31 | `smaps` | K3 | ⏳ |
| 32 | `refcounting` | K3 | ⏳ |
| 33 | `garbage-collector` | K3 | ⏳ |
| 34 | `gc-freeze` | K3 | ⏳ |
| 35 | `immortal-objects` | K3 | ⏳ |
| 36 | `finalization-and-atexit` | K3 | ⏳ |
| 37 | `global-interpreter-state` | K3 | ⏳ |
| 38 | `benchmarking-statistics` (first version; K9 extends it) | K3, K9 | ⏳ |
| 39 | `threads` | K4 | ⏳ |
| 40 | `gil` | K4 | ⏳ |
| 41 | `free-threading` | K4 | ⏳ |
| 42 | `subinterpreters` | K4 | ⏳ |
| 43 | `data-races` | K4 | ⏳ |
| 44 | `thread-safety-of-builtins` | K4 | ⏳ |
| 45 | `c-extensions` | K4 | ⏳ |
| 46 | `cpu-vs-io-bound` | K4 | ⏳ |
| 47 | `amdahls-law` | K4 | ⏳ |
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
