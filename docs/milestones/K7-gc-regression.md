# K7 — GC Regression Replay: Python 3.14's Incremental Garbage Collector

> **Status:** 📝 specified · **Depends on:** K1 (death classification, OOM detection), K3 (`memory/gcwatch.py`, `memory/procfs.py`, `concepts/garbage-collector.md`, `concepts/refcounting.md`), K6 (`gc_stats`/`gc_collect` debug ops) · **Branch:** `milestone/k7-gc` · **Size:** M (5 steps)
> **Incident:** research §2.2 (3.14.0–3.14.4 shipped an incremental GC. In a core developer's test, peak RSS was up to 2.7× higher. Django `manage.py migrate` was OOM-killed on a 512 MB Heroku dyno, climbing to 1,033 MB. 3.14.5 and 3.15 went back to the 3.13 generational GC)
> **Reference implementations (read *after* building):** [pygcprofiler](https://pypi.org/project/pygcprofiler/), [gcmon](https://pypi.org/project/gcmon), [Adam Johnson's post](https://adamj.eu/tech/2026/04/20/django-python-3.14-incremental-gc/), the [discuss.python.org revert thread](https://discuss.python.org/t/reverting-the-incremental-gc-in-python-3-14-and-3-15/107014)

---

## 1. The story (plain English)

Most Python objects are freed the moment nothing refers to them. That's **reference counting**, which you met in K3. One kind of garbage escapes it, though: a **reference cycle**. If object A refers to B and B refers back to A, both counts stay above zero forever, even when your program can't reach either of them any more. Python's **cycle garbage collector** (the "GC") finds these islands and frees them. To find them it has to *walk* over objects, and while it walks your program is paused.

For years CPython used a **generational** collector. New objects are checked often, and objects that survive get checked rarely. Most of the time that's fast, but a full collection of a big heap can pause the program for a long while. Python 3.14 shipped an **incremental** collector to fix those long pauses, by doing the work in small slices, and the pauses really did become about 10× shorter. There was a trade-off nobody fully expected. When a program creates cyclic garbage *faster than the slices can clean it up*, memory piles up. In one test, peak memory was **2.7× higher**.

Adam Johnson hit this with Django. Running database migrations creates many temporary model classes, and each one is tangled in cycles: a model points to its fields, and each field points back to its model. On a 512 MB Heroku machine, `manage.py migrate` climbed to over 1 GB and was **killed by the kernel**. His workaround was to call `gc.collect()` after each migration. CPython then **reverted** the incremental GC in 3.14.5 (10 May 2026) and in 3.15, and any future attempt (PEP 848) must go through a formal PEP.

**In this milestone we will** build a Django-style workload that creates model↔field cycles the way migrations do, and run it on 3.13 (generational), 3.14.4 (incremental), 3.14.5 (reverted) and 3.15. We'll measure **peak memory** and a **histogram of GC pauses** on each, try the `gc.collect()` workaround, and, if the blow-up reproduces, get a process **OOM-killed inside a 512 MiB box** exactly like the Heroku dyno, then watch Kiln's K1 supervisor diagnose it.

## 2. What you will learn

- **Explain** why reference counting can't free cycles, and how the cycle GC finds them (tracking containers, `gc_refs`, unreachable sets). *(Runtime internals)*
- **Explain** generational collection (young/old, thresholds, `gc.get_threshold()`, `gc.get_count()`) and the generational hypothesis. *(Runtime internals)*
- **Explain** incremental collection, and the three-way trade-off between **pause time**, **throughput** and **peak memory**. *(Runtime internals, Memory & perf)*
- **Predict** which workloads suffer under an incremental GC: high rates of *cyclic* garbage. *(Memory & perf)*
- **Use** `gc.callbacks` to measure pause durations and `gc.get_stats()` to count collections. *(Runtime internals)*
- **Explain** why frameworks create cycles (model↔field, parent↔child, class objects themselves) and why `type()`-created classes are always cyclic garbage. *(Data model)*
- **Explain** what peak RSS means and how to measure it two ways (`VmHWM`, `ru_maxrss`), including the KiB trap. *(Memory & perf)*

## 3. Entry criteria

- K1: death classification and cgroup OOM evidence (spec/04 §4.1–4.2), plus `scripts/dev.sh lab k1-oom`'s cgroup helper (spec/07 §6).
- K3: `memory/gcwatch.py` exists with at least a `gc.callbacks` hook; `memory/procfs.py` reads `VmHWM` and `VmRSS`; the `garbage-collector` and `refcounting` primers exist.
- K6: the `gc_stats`/`gc_collect` debug ops (spec/06 §5).
- `kiln doctor` lists 3.13, 3.14.4, 3.14.5 and 3.15 as installed. If not, gate G7.1 applies.

## 4. Scope

### In scope
- `labs/k7/django_like_cycles.py`: the workload, which runs as a child process on the interpreter under test.
- `labs/k7/gc_replay.py`: the driver. It runs on 3.15, launches the children, samples memory from outside, and writes records.
- A `PauseRecorder` in `src/kiln/memory/gcwatch.py` (extended from K3, or created if K3 only built the "who enabled the GC" detector).
- The screening sweep, the full matrix, the `gc.collect()` workaround variant, and the 512 MiB OOM run.
- A Kiln replay: the same workload as the `labs.cycles` task inside Kiln workers, with OOM detection by the supervisor.

### Out of scope (and why)
- Running real Django. It would add a large dependency (ADR-005), and the workload mimics the pattern that matters. Running the real `migrate` under 3.14.4 vs 3.14.5 is a Stretch item and would need an ADR.
- Tuning GC thresholds as a "fix". It's mentioned in the primer; the lab focuses on the documented workaround.
- The free-threaded GC (3.15t has a different, non-generational collector design). That's noted in the primer and left to Stretch.

## 5. Design

### 5.1 The workload (`labs/k7/django_like_cycles.py`)

The workload is a plain script with zero Kiln imports, so that it runs identically on every interpreter, including pinned patch versions with no Kiln venv. It loads `kiln/memory/gcwatch.py` **by file path** with `importlib.util.spec_from_file_location`. That module must therefore stay stdlib-only and 3.13-syntax, and a `# WHY:` comment at its top says so.

```python
class Field:                                  # like django.db.models.Field
    def __init__(self, name: str, payload_bytes: int) -> None:
        self.name = name
        self.model = None                     # filled in below → creates the field → model edge
        self.payload = bytearray(payload_bytes)   # makes each cycle "weigh" something measurable

class Options:                                # like Model._meta
    def __init__(self) -> None:
        self.model = None                     # options → model edge
        self.fields: list[Field] = []         # options → fields edges

class ModelBase:                              # base for dynamically created model classes
    pass

class StateApps:                              # like django.db.migrations.state.StateApps
    def __init__(self) -> None:
        self.models: dict[str, type] = {}
        self.state = None                     # apps ↔ state cycle

class ProjectState:
    def __init__(self) -> None:
        self.apps = StateApps()
        self.apps.state = self

def run_migration(i: int, models: int, fields: int, payload_bytes: int) -> None:
    state = ProjectState()
    for m in range(models):
        opts = Options()
        cls = type(f"M{i}_{m}", (ModelBase,), {"_meta": opts})   # a brand-new class object
        opts.model = cls                                           # class ↔ options cycle
        for f in range(fields):
            fld = Field(f"f{f}", payload_bytes)
            fld.model = cls                                        # field → class
            opts.fields.append(fld)                                # options → field
        state.apps.models[cls.__name__] = cls
    # returning drops `state`: everything above is now unreachable but cyclic → only the GC can free it
```

- **CLI:** `--migrations M --models K --fields F --payload-bytes S --workaround none|collect-each --pauses on|off --result-fd N`.
- **Loop:** `for i in range(M): run_migration(i, K, F, S)`, and if `--workaround collect-each`, it calls `gc.collect()` after each migration (Adam Johnson's workaround).
- **Output:** at the end, the child writes one JSON object to `--result-fd`, a pipe from the driver. It contains:
  - `vmhwm_bytes` (from `/proc/self/status`) and `ru_maxrss_bytes` (`resource.getrusage(RUSAGE_SELF).ru_maxrss * 1024`, with a `# GOTCHA:` comment: it's KiB on Linux, spec/10 §7)
  - `wall_s`
  - `gc_threshold`, `gc_count`, `gc_stats` (`gc.get_stats()`)
  - `gc_isenabled`
  - `python` info
  - when `--pauses on`: the pause samples and the per-generation counts (§5.2)
- **Why classes?** `type(...)` creates a class object, and class objects are *always* in reference cycles. For example, `cls.__mro__` contains `cls` itself. So every model class is garbage that only the cycle GC can free, just like Django's rendered migration models. The primer `reference-cycles-in-frameworks.md` demonstrates this with `gc.get_referrers`.

### 5.2 `PauseRecorder` (`src/kiln/memory/gcwatch.py`)

```python
class PauseRecorder:
    """Measures every GC pause via gc.callbacks (start → stop)."""
    def install(self) -> None: ...                 # appends self._cb to gc.callbacks
    def uninstall(self) -> None: ...
    samples_ns: list[int]                          # one per collection (capped at 200,000 → then histogram only; spec/10 §6)
    by_generation: dict[int, int]                  # collections per generation value reported by CPython
    collected_total: int; uncollectable_total: int
    def histogram(self) -> dict[str, int]: ...     # bucket upper bounds in µs (below)
    def summary(self) -> dict: ...                 # count, total_ms, p50_us, p99_us, max_us (nearest-rank)
```

- **Callback:** `cb(phase, info)`.
  - `phase == "start"` stores `perf_counter_ns()`.
  - `phase == "stop"` computes the duration and records `info["generation"]`, `info["collected"]` and `info["uncollectable"]`.
  - The callback must not allocate much. It appends to a preallocated-style list, and the primer explains why allocating inside a GC callback is risky (it can trigger more collections).
- **Buckets** (µs): `10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000, 500000, +Inf`.
- **Incremental GC caveat:** on 3.14.0–3.14.4 a "collection" seen by callbacks may be **one increment**, not a full pass, and the `generation` values may mean something different. G7.3 records exactly what each version reports. The lab compares **pause distributions** and **total GC time** rather than collection counts across versions, and says why.
- **Kiln integration:** when `debug.enabled`, workers install a `PauseRecorder` at boot, and `gc_stats` returns its histogram in `pause_histogram` (spec/06 §5 already defines the field).

### 5.3 The driver (`labs/k7/gc_replay.py`, lab id `k7-gc`)

- It runs under the default dev interpreter (3.15) and launches `"<interp path>" labs/k7/django_like_cycles.py ...` with `subprocess.Popen(pass_fds=[w])`. The interpreter path comes from `uv python find <version>`.
- **Sampler:** while the child runs, the driver reads `/proc/<child>/status` `VmRSS` every 200 ms (spec/10 §7, "sample from a separate process"), storing the `t_s` and `rss_bytes` series.
- **Safety cgroup:** every K7 child runs inside a cgroup with `memory.max = 2560M`, `memory.swap.max = 0`, using the spec/07 §6 mechanism, which `scripts/dev.sh lab` wraps. That enforces ADR-023's 3 GiB budget (2.5 GiB for the child plus headroom). A child killed by this cap is recorded as `outcome: "budget_exceeded"`. That counts as evidence of a blow-up, but it's reported separately from the 512 MiB experiment.
- **Outcome per run:** `ok` / `oom_killed` (SIGKILL + an `oom_kill` delta in `memory.events`) / `killed_other` / `crashed`. Classification reuses `kiln.master.supervisor`'s pure classification function if it's importable without side effects. Otherwise it applies the same rules, with a comment pointing at spec/04 §4.1.
- **Records:** one ExperimentRecord per run, with experiment `gc-replay`, variant `<interp>-<workaround>`, params `{migrations, models, fields, payload_bytes, workaround, cgroup_max}`, and the metrics `peak_rss_bytes` (value: max of VmHWM and the sampled maximum), `rss_bytes` (samples) + `t_s`, `gc_pause_us` (samples), `gc_total_ms`, `wall_s`, `outcome`.

### 5.4 Choosing workload parameters (a defined search, not trial-and-error)

The exact sizes that trigger the blow-up aren't known in advance, so step 3 runs a **screening sweep** on 3.14.4 vs 3.14.5 only, at 3 repeats per cell:

| Parameter | Grid |
|---|---|
| `models` K | 20, 200 |
| `fields` F | 10, 50 |
| `payload_bytes` S | 0, 1024, 16384 |
| `migrations` M | fixed 200 |

- **Order:** cells run from the smallest estimated garbage per migration (K·F·S) to the largest, so the budget cap is approached gradually.
- **Selection rule:** choose the **first** cell where median peak RSS on 3.14.4 is ≥ 1.5× that on 3.14.5 *and* 3.14.5's peak is ≤ 400 MiB. That second condition leaves room for the 512 MiB experiment. If several cells qualify at the same size rank, pick the one with the larger ratio.
- **If no cell qualifies:** the blow-up is declared **not reproduced with this workload**. The report states the largest ratio observed, with its cell and CI, discusses why (machine, the exact 3.14.4 behaviour seen in G7.2, workload shape), and the full matrix (§7) runs on the cell with the largest ratio. **Never** tune parameters beyond the grid to force a result. That would be cherry-picking, and the report must explain why.

### 5.5 The 512 MiB OOM run

Only if §5.4 found a qualifying cell:
1. Run the selected cell with the cgroup at `memory.max = 512M` (the Heroku dyno size), 5 repeats on each interpreter.
2. Record `outcome`. Expectation: `oom_killed` on 3.14.4, `ok` on 3.14.5, 3.15 and 3.13.
3. Repeat on 3.14.4 with `--workaround collect-each`. Expectation: `ok`.

### 5.6 The Kiln replay (ties back to K1)

- The task `labs.cycles(n)` (spec/03 §7) gets three optional kwargs added, `models=20, fields=10, payload_bytes=0`, and `n` means "number of migrations". This is an additive change to spec/03 §7, made in step 5. The task calls the same `run_migration` logic, implemented in `src/kiln/labs/tasks.py` (or a helper module it imports). It mirrors `labs/k7/django_like_cycles.py`. The duplication is deliberate and documented, because the standalone script must run without Kiln installed. A test asserts that the two implementations build identical object graphs, by counting objects per type after one migration.
- **Procedure:** `KILN_PY=3.14.4 ./scripts/dev.sh lab k7-gc --kiln` starts Kiln (`--workers 2 --labs --debug`) inside a 512 MiB cgroup, and submits N `labs.cycles` jobs with the §5.4 parameters as kwargs.
- **What it shows:**
  - worker RSS climbing (heartbeats)
  - `gc_stats` pause histograms per worker
  - on 3.14.4, a worker getting OOM-killed, which Kiln logs as `worker_died how=killed signal=SIGKILL suspected_oom=true oom_evidence="cgroup memory.events oom_kill N→N+1"` (spec/04 §4.1–4.2, spec/06 §2.2), with its job `LOST(worker_lost)`
  - the same run on 3.14.5 completing
- If Kiln can't run on 3.14.4 (for example a missing venv, or the dependency sync failing for a pinned patch version), the replay uses 3.14.0–3.14.3 per G7.1's fallback. Failing that, it's marked `not measured` with the reason.

## 6. Steps

1. **Workload + `PauseRecorder` + version gates.**
   - *Docs first:* `concepts/generational-gc.md`, `concepts/incremental-gc.md`, `concepts/reference-cycles-in-frameworks.md`, `python/gc-callbacks.md`, `python/dynamic-classes-with-type.md`, `python/metaclass-basics.md`.
   - *Gates:* G7.1–G7.5.
   - *Tests:* `PauseRecorder` records ≥ 1 pause after a forced `gc.collect()`, with a sane duration; the workload's object graph after one migration has exactly K classes, K Options and K·F Fields (counted with `gc.get_objects()` filtered by type) and is fully collected after `gc.collect()` (checked with `weakref.ref` to one class).
2. **Driver + sampler + safety cgroup + records.**
   - *Tests:* a small run (M=5) produces a valid ExperimentRecord (`kiln.bench.records.validate`); a deliberately oversized run inside a 64 MiB test cgroup is classified `oom_killed` (marked `lab` because it needs the cgroup).
3. **Screening sweep + parameter selection** (§5.4). Publish the sweep table.
4. **Full matrix + workaround + the 512 MiB OOM run** (§7, §5.5).
5. **Kiln replay (§5.6) + lab report + tour + reference comparison.**
   - *Docs:* `concepts/pause-time-vs-throughput-vs-memory.md`.

## 7. Lab design

- **Question:** does the 3.14.4 incremental GC really raise peak memory for framework-style cyclic garbage compared with 3.13, 3.14.5 and 3.15? What does it buy in pause times? Does `gc.collect()` after each unit of work fix it?
- **Implementer's hypothesis:**
  - On the selected workload, 3.14.4 has a higher peak RSS than 3.14.5, 3.13 and 3.15, which should be roughly equal to each other.
  - 3.14.4 has a **lower max/p99 GC pause** but similar or higher total GC time.
  - `collect-each` brings the 3.14.4 peak down to about the generational level, at some cost in wall time.
  - The research's "2.7×" came from a different test, so we expect *a* ratio > 1 and not that particular number.
- **Independent variables:**
  - interpreter ∈ {3.13, 3.14.4, 3.14.5, 3.15}
  - workaround ∈ {none, collect-each}
  - `cgroup memory.max` ∈ {2560M (safety only), 512M (OOM experiment)}
- **Dependent variables:**
  - peak RSS (bytes; the max of VmHWM and the sampled maximum, both recorded)
  - the RSS-over-time curve
  - GC pause p50/p99/max (µs) and total GC time (ms)
  - collections per reported generation
  - wall time (s)
  - outcome
- **Controls:**
  - the same parameters (§5.4 selection), M=200
  - a fresh process per run (spec/10 §3)
  - the ext4 snapshot (ADR-003)
  - CPU pinning to 2 cores (`taskset -c 2-3`, recorded in `host.pinned_cpus`), since the workload is single-threaded
  - other heavy apps closed
- **Repeats and statistics:** per [spec/10](../spec/10-measurement-methodology.md). Memory metrics: ≥ 5 repeats, if the CV < 2% justification holds (spec/10 §3); otherwise ≥ 10. Timing metrics (wall, pauses): ≥ 10 repeats. 3 warm-ups. Round-robin interleaving across interpreters. Comparisons use ratio-of-medians bootstrap CIs (spec/10 §4.3), with 3.14.5 as the baseline A.
- **Procedure:** `./scripts/dev.sh lab k7-gc --sweep` (step 3), then `./scripts/dev.sh lab k7-gc --matrix`, then `./scripts/dev.sh lab k7-gc --oom` (§5.5), then `./scripts/dev.sh lab k7-gc --kiln` (§5.6). Every variant writes records to `results/K7/gc-replay/`, and the cited runs are copied to `results/published/K7/`.
- **Result tables** (headers only):
  - *Screening sweep:* `K | F | S | 3.14.4 peak MiB (median of 3) | 3.14.5 peak MiB | ratio | qualifies?`
  - *Peak memory:* `interp | workaround | peak RSS MiB median [95% CI] | ratio vs 3.14.5 [CI] | outcome counts`
  - *GC pauses:* `interp | collections (by reported gen) | total GC ms [CI] | p50 µs | p99 µs | max µs`
  - *Wall time:* `interp | workaround | wall s [CI] | ratio vs 3.14.5 [CI]`
  - *512 MiB experiment:* `interp | workaround | runs | oom_killed | ok | peak MiB before kill`
  - *Kiln replay:* `interp | jobs submitted | SUCCEEDED | LOST | worker deaths (how, suspected_oom, oom_evidence) | max worker RSS MiB`
  - *Version facts:* `interp | gc.get_threshold() | len(gc.get_stats()) | callback info keys | generation values seen`
- **Charts** (rendered in K9, sketched as tables here): RSS-over-time curves per interpreter, and a pause histogram per interpreter.

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G7.1 | uv can install exactly 3.14.4 and 3.14.5 | `uv python install 3.14.4 3.14.5 && uv python find 3.14.4` | for the incremental side, use the newest of 3.14.0–3.14.3 that uv offers (research §2.2 says all of 3.14.0–3.14.4 shipped incremental; G7.2 confirms it on whatever we pick). If none is available, build 3.14.4 from the python.org source tarball into `~/.local/cpython-3.14.4` (`./configure --prefix=... && make -j && make install`, with build deps from spec/07 §2 plus `libssl-dev zlib1g-dev libffi-dev libbz2-dev libreadline-dev libsqlite3-dev liblzma-dev`). Record an ADR either way, and update ADR-004's lab interpreter list |
| G7.2 | 3.14.4 uses the incremental GC and 3.14.5 reverted to generational | (a) read the 3.14.5 changelog entry (`python -m pydoc` doesn't have it, so use the "What's New / changelog" page) and quote it; (b) compare on each interpreter: `gc.get_threshold()`, `len(gc.get_stats())`, `gc.get_stats()` keys, and `help(gc.collect)` text; (c) behaviour: the number of callbacks per forced `gc.collect()` | if 3.14.5 did **not** revert, add an errata entry and reframe the lab as "3.13 & 3.15 (generational) vs 3.14.x (incremental)", using the newest 3.14 that's still incremental vs 3.13/3.15 |
| G7.3 | `gc.callbacks` receive `phase` + `info` with `generation`, `collected`, `uncollectable` on every version, including the incremental one | print the info dicts for one forced collection per version | adapt `PauseRecorder` to the real keys and document the per-version meaning |
| G7.4 | `ru_maxrss` is in KiB on Linux | compare with `VmHWM` in the same process | record it; trust VmHWM |
| G7.5 | 3.15 uses the generational GC (research §2.2) | same as G7.2 (b) and (c) on 3.15 | record it; the hypothesis for 3.15 changes accordingly |
| G7.6 | the cgroup `memory.max` applies to the driver's child and `memory.events oom_kill` increments on a kill | a 64 MiB test cgroup with an allocating child | follow spec/07 §6's gate: judge "oom_killed" by SIGKILL + suspected OOM alone, and record it |

## 9. Acceptance criteria

- **AC-7.1** `PauseRecorder` and the workload pass their tests (`tests/unit/test_gcwatch_pauses.py`, `tests/lab/test_k7_workload.py`), and `gcwatch.py` stays stdlib-only and 3.13-syntax (`tests/unit/test_gcwatch_standalone.py` loads it by path under 3.13).
- **AC-7.2** The version-facts table is complete for all four interpreters (or the G7.1 fallback interpreter), with real output.
- **AC-7.3** The screening sweep is published, and the selection follows §5.4's rule exactly. If it isn't reproduced, the report says so with the largest observed ratio.
- **AC-7.4** The full matrix (peak memory, pauses, wall time × interpreter × workaround) is published to `results/published/K7/`, with CIs and spec/10 interpretation wording.
- **AC-7.5** If §5.4 qualified, the 512 MiB experiment shows the outcome counts per interpreter, and the Kiln replay shows the supervisor classifying an OOM kill with `suspected_oom=true` (plus an `oom_evidence` showing the `oom_kill` increment, if G7.6 passed). Otherwise both are marked `not measured: blow-up not reproduced` with a link to AC-7.3's evidence.
- **AC-7.6** The Docs deliverables exist and `./scripts/dev.sh docs-check` passes.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/generational-gc.md` | the generational hypothesis, generations, thresholds and counts, how a collection finds unreachable cycles (`gc_refs` subtraction, a simplified walkthrough with a 3-object diagram), `gc.freeze` recap (link K3) |
| Concept | `docs/concepts/incremental-gc.md` | what "incremental" means; slicing the work; why garbage can accumulate when the allocation rate is greater than the collection rate; the 3.14 history and revert; PEP 848 as the path forward; the free-threaded GC noted |
| Concept | `docs/concepts/pause-time-vs-throughput-vs-memory.md` | the three-way trade-off with analogies, and how other runtimes choose (the JVM's G1/ZGC, Go's GC) at a high level; how to tell which one your service cares about |
| Concept | `docs/concepts/reference-cycles-in-frameworks.md` | why classes are cyclic; model↔field, parent↔child, closures, exceptions ↔ tracebacks ↔ frames; `gc.get_referrers` demo; breaking cycles with `weakref` |
| Python | `docs/python/gc-callbacks.md` | `gc.callbacks`, phases, the info dict, why callbacks must be cheap |
| Python | `docs/python/dynamic-classes-with-type.md` | `type(name, bases, namespace)`, the equivalence with a `class` statement, `__mro__`, why it's cyclic |
| Python | `docs/python/metaclass-basics.md` | light: "a class is an object; its type is its metaclass"; `type` is the default; how Django's `ModelBase` metaclass relates (conceptually only; no Django code) |
| Tour | `docs/tours/K7.md` | stops: workload → PauseRecorder → driver/sampler/cgroup → selection rule → `labs.cycles` + supervisor classification |
| Lab | `docs/labs/K7-gc-regression.md` | per the template; every §7 table; the version facts; honest reproduction status |

## 11. Reference comparison assignment

1. **Adam Johnson's post:** compare his measurement method (what tool? peak RSS how?) and workaround with ours. *Question:* does our workload's per-migration garbage resemble his? What would make it more faithful?
2. **The discuss.python.org revert thread:** *Questions:* what benchmark showed 2.7×? What did the core developers consider as alternatives to reverting (threshold tuning? a hybrid?), and why did they revert?
3. **pygcprofiler / gcmon:** how they hook GC events (`gc.callbacks` like us, or something else?) and what they report. *Question:* what does either show that our `PauseRecorder` doesn't, and does it matter for this incident?
4. **CPython `Python/gc.c`** (3.14.4 vs 3.14.5 tags): the function that decides how much work an increment does. *Question:* reading the code, what quantity sets the size of each increment, and why could it fall behind a cycle-heavy allocator?

## 12. Risks and pitfalls

- **The blow-up doesn't reproduce.** This is plausible, because the exact trigger is unknown. §5.4 makes the outcome honest and repeatable either way. An unreproduced result is a valid lab outcome, but only when it's documented with evidence.
- **Measuring memory in-process perturbs it.** The sampler is a separate process (spec/10 §7). The child reads VmHWM only once, at the end.
- **Allocator retention.** RSS may stay high after the GC frees objects (pymalloc arenas). That's why we compare *peak* memory, not end memory. The primer explains it, linking K6.
- **Pinned interpreters lack a Kiln venv.** That's why the standalone workload has no Kiln imports, and why `gcwatch.py` is loaded by path.
- **3.14.4 is a known-buggy release.** Use it only inside the K7 lab, never as a dev interpreter.
- **Budget.** The safety cgroup enforces ADR-023 automatically. Never run sweeps outside it.

## 13. Checkpoint quiz (seed questions)

1. Why can't reference counting free two objects that refer to each other?
2. Why is every class created with `type(...)` garbage that only the cycle GC can free?
3. What does the generational hypothesis say, and why does it make the common case fast?
4. In one sentence: what problem was the incremental GC solving, and what problem did it create?
5. Pause time, throughput, peak memory: which did 3.14.0–3.14.4 improve, and which did it worsen for Django migrations?
6. Why does calling `gc.collect()` after each migration fix the peak? What does it cost?
7. `ru_maxrss` says 1,048,576. How many MiB is that on Linux, and why is that easy to get wrong?
8. Why do we compare *peak* RSS rather than RSS at the end of the run?
9. A worker was SIGKILLed inside a 512 MiB cgroup and `memory.events oom_kill` went from 0 to 1. What does Kiln log, and what happens to its job?
10. Why must the sweep never be extended "until it reproduces"?

## 14. Stretch / later

- A real Django `migrate` on a generated project with many migrations under 3.14.4 vs 3.14.5 (needs a `lab-k7 = ["django"]` group and an ADR).
- Run the workload on 3.15t and explain the free-threaded GC's different design and numbers.
- A threshold-tuning experiment: can `gc.set_threshold` on 3.14.4 bring the peak down without the per-migration `gc.collect()`?
