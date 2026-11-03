# 10 — Measurement Methodology: How We Know a Number Means Something

> **Status:** Approved spec. **Implemented in:** K0 (`bench/records.py`), K1 (load generator), K3 (memory readers), K9 (`bench/stats.py`, runner, report).
> **Concepts:** sample, median, percentile, variance, noise, confidence interval, bootstrap, warm-up, RSS/PSS/USS. Primers: `concepts/benchmarking-statistics.md` (written in **K3**, the first measurement-heavy lab, and extended in K9) and `concepts/memory-metrics.md` (written in K1, extended in K3).

---

## 1. Why this document exists

"I changed X and it got 12% faster" is one of the most common false statements in software. Programs on a laptop run at different speeds from moment to moment: other apps, CPU frequency scaling, the WSL VM, cache state. If you run a benchmark once, you mostly measure **noise**. This spec defines how every Kiln lab measures, repeats, summarizes and compares, so that a claim in a lab report means something. It also makes all labs produce the same file format, so K9 can combine them ([ADR-022](../decisions.md#adr-022)).

## 2. The ExperimentRecord (one JSON file per run)

A **run** is one execution of one *variant* of one *experiment*. For example: "K3, experiment `cow-gc-modes`, variant `freeze`, on 3.15". Every run writes exactly one record.

```json
{
  "schema_version": 1,
  "milestone": "K3",
  "experiment": "cow-gc-modes",
  "variant": "freeze",
  "run_id": "20261103T191204Z-py3.15-7f3a",       // UTC timestamp + interpreter tag + 4 random hex
  "repeat_index": 4,                                // 0-based; warm-ups are NOT written (see §3)
  "started_at": "2026-11-03T19:12:04.120Z",         // wall clock, for humans
  "duration_s": 41.7,                               // monotonic, whole run
  "interpreter": {
    "version": "3.15.2",
    "implementation": "cpython",
    "build": "gil",                                 // "gil" | "freethreaded"
    "gil_enabled_at_end": true,                     // sys._is_gil_enabled() if it exists, else true
    "executable": "/home/Megatron/.local/share/uv/python/cpython-3.15.2-linux-x86_64-gnu/bin/python3.15",
    "jit_enabled": false                            // null if it can't be determined on this version
  },
  "host": {
    "kernel": "6.6.87.2-microsoft-standard-WSL2",
    "wsl": true,
    "cpus": 14,
    "cpu_model": "…from /proc/cpuinfo…",
    "mem_total_bytes": 8053063680,
    "pinned_cpus": [2, 3, 4, 5],                    // null if not pinned
    "notes": "source=ext4-snapshot; windows power plan=balanced"
  },
  "git": { "commit": "a1b2c3d", "dirty": false },
  "config": { "...": "the full effective Kiln config, if a server was involved" },
  "params": { "workers": 4, "preload_mb": 300, "touch_fraction": 0.5 },
  "metrics": {
    "pss_per_worker_bytes":   { "unit": "bytes", "samples": [61210624, 60948480, 61341696, 61079552] },
    "shared_per_worker_bytes":{ "unit": "bytes", "samples": [251658240, 251920384, 251527168, 251789312] },
    "throughput":             { "unit": "jobs/s", "value": 4180.2 },
    "latency":                { "unit": "ms", "samples": [0.81, 0.77, "…"], "summary": {"p50": 0.79, "p90": 1.4, "p99": 3.9, "max": 12.0} }
  },
  "notes": "free text: anything odd during the run"
}
```

The numbers above illustrate the *format* only. They aren't measurements.

### 2.1 Field rules

| Field | Rule |
|---|---|
| `schema_version` | integer. Bumped only by an ADR. K9 refuses unknown versions with a clear error |
| `milestone`, `experiment`, `variant` | `experiment` is kebab-case and stable. The lab report's Method section lists them |
| `metrics.<name>` | must have `unit`, and exactly one of `value` (a single number) or `samples` (a list of numbers). `summary` is optional and always recomputable from `samples` |
| Units | `bytes`, `s`, `ms`, `us`, `ns`, `jobs/s`, `req/s`, `count`, `ratio`, `percent`. Never `MB` (`bytes` is unambiguous, and the report converts for display) |
| `metrics` names | `snake_case`, ending in the unit where helpful (`rss_bytes`) |
| Validation | `kiln.bench.records.validate(record)` checks the schema with hand-written checks (no jsonschema library; ADR-005) and is called before writing |
| Writing | written atomically: write to `name.json.tmp`, then `os.replace`. A crash never leaves half a record |

### 2.2 Where records go

```
results/<milestone>/<experiment>/<run_id>.json        # every run (git-ignored)
results/published/<milestone>/<experiment>/...         # copies of the runs cited in a lab report (committed)
```

A lab report cites records by path. Every number in a lab report table must be traceable to a published record (standards/documentation.md §6: "numbers are never invented").

## 3. Repeats and warm-ups

| Setting | Default | Why |
|---|---|---|
| **Warm-up runs** | **3**, discarded and not written | the first runs pay one-off costs (disk cache, `.pyc` compilation, CPU frequency ramp-up) that aren't what we're measuring |
| **Measured repeats** per variant | **≥ 10** | below ~10, a bootstrap CI is too unstable to trust |
| **Interleaving** | variants are run in **round-robin order** (A B C A B C …), not A×10 then B×10. The K9 matrix runner also shuffles the order *inside* each round with a seeded `random.Random` (the seed is recorded), so a fixed position like "always first after a cold start" can't favour one variant | if the machine gets slower halfway through (thermal throttling, a Windows update), all variants are affected equally |
| **Fresh process per run** | yes, for anything involving startup, GC or memory | state from a previous run (heap shape, caches) would leak into the next |
| Memory experiments | ≥ 5 repeats is acceptable, **if** the lab report shows the spread is tiny (CV < 2%) | memory numbers are far less noisy than timings; the report must justify the lower count |

A lab may require more repeats (for example a latency tail, p99). It may never use fewer without writing the justification in the Method section.

## 4. Summarizing and comparing

### 4.1 Summary statistics (`kiln.bench.stats`, stdlib only; ADR-018)

| Statistic | When to use it |
|---|---|
| **Median** | the default "typical value". It isn't dragged around by a few slow outliers, which timing data always has |
| Mean ± stdev | reported alongside, and only meaningful when the data is roughly symmetric |
| **Coefficient of variation** (CV = stdev / mean) | a noise indicator. CV > 5% for timings means "noisy environment"; investigate before concluding anything |
| Min | for "best achievable" microbenchmarks only, and labelled as such |
| Percentiles p50/p90/p99/max | latency distributions (§6) |

### 4.2 Confidence intervals by bootstrap

A **confidence interval (CI)** says "given our noisy samples, the true typical value is probably between here and here". We use the **percentile bootstrap**:

1. You have n measured samples, for example 10 run times.
2. Make a "fake re-run" by drawing n samples **from your own samples, with replacement** (some get picked twice, some not at all). Compute the median of that resample.
3. Do step 2 **10,000 times** with a fixed seed (`random.Random(20261103)`) so the report is reproducible.
4. Sort the 10,000 medians. The 2.5th and 97.5th percentiles are the **95% CI** of the median.

Why bootstrap? It doesn't assume the data follows a bell curve, which timing data usually doesn't, and it's about 20 lines of plain Python. That makes it a good learning exercise.

### 4.3 Comparing two variants (A = before, B = after)

- Compute the **ratio of medians** `median(B) / median(A)`.
- Get its CI by bootstrap: resample A and B **independently** each round, and take the ratio of their medians.
- **Interpretation rules, used in every lab report:**

| CI of the ratio | Report it as |
|---|---|
| entirely below 1.0, e.g. [0.62, 0.71] | "B is faster/smaller: about 33% less (95% CI 29–38%)" |
| entirely above 1.0 | "B is slower/larger …" |
| **includes 1.0**, e.g. [0.95, 1.04] | "**no detectable difference** at this sample size". *Never* "B is 1% faster" |

- Also state the **practical** significance: a statistically real 0.3% change in a 5 ms job is irrelevant, and the analysis section should say so.

## 5. Noise control on a WSL2 laptop

We can't get a lab-grade machine, so we reduce noise where we can and **report honestly** what remains.

| Technique | How | Available here? |
|---|---|---|
| Close heavy apps (browsers, IDE indexing, games) | manual; the lab procedure lists it | ✅ |
| Windows power plan "Best performance", laptop plugged in | Windows Settings → Power | ✅ (recorded in `host.notes`) |
| Run from the ext4 snapshot | `dev.sh bench-snapshot` (spec/07 §7) | ✅ (required for timing labs) |
| **CPU pinning** | `taskset -c 2-5 <cmd>` binds the processes to those cores, which avoids migration between cores | ✅ (`util-linux`). `host.pinned_cpus` records it |
| Fixed CPU frequency / disable turbo | `cpupower frequency-set` | ❌ not inside WSL (the Windows host controls frequency). Mentioned so the learner knows real benchmark rigs do it |
| `isolcpus` kernel parameter (reserve cores for benchmarks) | boot parameter | ❌ not configurable for the WSL kernel |
| Disable ASLR | `setarch -R` | possible, but **not used**. Real programs run with ASLR, so we measure with it |
| Repeats + interleaving + CI | §3, §4 | ✅ the main defence |
| Watch for throttling | compare the first and last repeats of the same variant; a trend means drift | ✅ the report's "threats to validity" mentions any trend |

## 6. Latency measurements

- **Timers:** `time.perf_counter_ns()` for durations (the highest-resolution monotonic clock, as an integer in nanoseconds, so there's no float rounding). `time.monotonic()` for deadlines and timeouts. **Never `time.time()` for durations**, because the wall clock can jump (spec/01 §4 rule 5).
- **What a latency means must be stated.** For example, in the load generator, "submit latency" is from just before `sendall(submit)` to the `ok` reply being parsed, and "end-to-end latency" is from before `submit` to the `wait` reply with a terminal state.
- **Store full samples** when there are ≤ 200,000 of them per run (10,000 jobs easily fits). Percentiles are then computed exactly, using the nearest-rank method, which is documented in the stats module docstring.
- **Above 200,000 samples**, use a **log-linear bucket histogram** in the style of HDR: buckets with ~1% relative width from 1 µs to 60 s, stored as `{"bucket_upper_ns": count}`. Percentiles are reported as bucket upper bounds, with the precision stated.
- **Coordinated omission warning:** a load generator that waits for each reply before sending the next request under-reports latency when the server stalls, because it stops sending during the stall. Kiln's load generator has an **open-loop mode** (`--rate R`, send on a fixed schedule) next to closed-loop (`--concurrency C`). Lab reports say which mode they used. The K5 primer explains the difference.

## 7. Memory metrics: definitions

All of these come from Linux's `/proc` files, read by `kiln.memory.procfs`. "Page" means a 4 KiB unit of memory.

| Metric | Source | Meaning | Gotcha |
|---|---|---|---|
| **RSS** (Resident Set Size) | `/proc/<pid>/status` `VmRSS`, or `smaps_rollup` `Rss` | every page of this process currently in RAM, **including pages shared with other processes** | summing RSS over forked workers counts shared pages several times, so the total can exceed physical RAM. That's the K3 trap |
| **PSS** (Proportional Set Size) | `/proc/<pid>/smaps_rollup` `Pss` | each shared page is divided by the number of processes sharing it | **summing PSS over processes gives real total usage**. The best "fair share" number |
| **USS** (Unique Set Size) | `Private_Clean + Private_Dirty` from `smaps_rollup` | pages only this process uses | the memory you'd get back if this process exited |
| Shared clean / dirty | `Shared_Clean`, `Shared_Dirty` | shared pages unmodified since loading or forking (clean) vs. modified before sharing (dirty) | |
| Private clean / dirty | `Private_Clean`, `Private_Dirty` | **Private_Dirty growing in a worker = copy-on-write copies happening.** This is the Instagram signal | |
| Swap | `Swap` | pages pushed to disk | should be 0. If not, timing numbers are void |
| **VmHWM** ("high water mark") | `/proc/<pid>/status` | **peak RSS** over the process's life | resets only if you write `5` to `/proc/<pid>/clear_refs`, which we never do |
| **`ru_maxrss`** | `resource.getrusage(RUSAGE_SELF/CHILDREN).ru_maxrss` | peak RSS too, from the kernel's accounting | **the unit is KiB on Linux** (bytes on macOS). Always convert with `× 1024` and say so in a `# GOTCHA:` comment. `RUSAGE_CHILDREN` gives the max over *waited-for* children only |
| cgroup `memory.current` / `memory.peak` | `/sys/fs/cgroup/<path>/` | total for every process in the cgroup | what the OOM killer actually compares to `memory.max` |
| `tracemalloc` current/peak | `tracemalloc.get_traced_memory()` | bytes allocated **by Python** through its allocators | doesn't see C-library `malloc`s; Memray does (K6) |

**Sampling memory over time:** labs that show memory *growth* (K3 CoW decay, K6 leaks, K7 GC blow-up) sample at a fixed interval (default 200 ms) from a **separate sampler process**, not from the process being measured, because reading memory in-process can itself allocate. They store `[(t_s, value_bytes), ...]` as `samples` with `unit: "bytes"` plus a parallel `t_s` metric.

## 8. Throughput

- **Throughput** = completed jobs ÷ wall time of the measured window, in `jobs/s`.
- The measured window **excludes** ramp-up (the first 10% of jobs or 2 s, whichever is larger) and drain (from the moment the last submit is sent).
- The master's own CPU time (`/proc/<pid>/stat` utime + stime) is recorded alongside, because ADR-007 says the master can become the bottleneck. K1's lab measures exactly when it does.

## 9. What every lab report's Method section must state (checklist)

- [ ] Experiment and variant names (these match the records)
- [ ] Interpreter(s) and build(s)
- [ ] Warm-ups, repeats, interleaving, fresh process per run (yes/no)
- [ ] Source location (`ext4-snapshot` / `9p`) and CPU pinning
- [ ] Which metric answers the question, with its definition from §7 or §6
- [ ] Comparison method (§4.3) and the practical-significance threshold chosen *before* looking at results
- [ ] Paths of the published records
