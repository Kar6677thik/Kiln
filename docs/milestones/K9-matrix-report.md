# K9 — Matrix Report: Every Benchmark, Every Interpreter, Honest Statistics, HTML via t-strings

> **Status:** 📝 specified · **Depends on:** K3–K8 (their lab drivers and ExperimentRecords), K0 (`bench/records.py`), spec/10 (methodology) · **Branch:** `milestone/k9-matrix` · **Size:** M (6 steps)
> **Incident:** all of research §2 ("K9 — Matrix report: run K3–K8 across interpreters and builds, with repeated runs and confidence intervals, rendered as HTML with t-strings"), plus PEP 750's stated goal (§2.12): t-strings exist so that interpolated values can be **escaped**, which prevents injection
> **Reference implementations (read *after* building):** [asv](https://asv.readthedocs.io/en/v0.5/using.html) (airspeed velocity: multi-interpreter benchmark runs + HTML), [pyperf](https://pyperf.readthedocs.io/) (CPython's benchmark harness: calibration, worker processes, stats), and the [PEP 750 examples](https://peps.python.org/pep-0750/)

---

## 1. The story (plain English)

By now every milestone has produced numbers: shared memory under four GC modes, throughput of four executors, blocked-loop latency, leak slopes, GC pauses, nanoseconds per Rust call. Each was measured carefully, but each in its own corner. Two questions remain. **Do these results hold across Python versions**: 3.13, 3.14, 3.15 and free-threaded 3.15t? And **can we present them so a reader can trust them**?

Trust takes three things. **Repeats**: run each measurement many times, because a single run is mostly noise. **Fair ordering**: interleave the variants, so a laptop that slows down halfway through doesn't make one variant look worse. **Confidence intervals**: report a range ("between 12% and 19% faster") instead of a falsely precise number. That's how serious benchmark suites such as asv and pyperf work, and this milestone builds a small version of one.

The report itself is a web page. Building HTML from data is a classic way to introduce a security bug: if a value like a test name ever contains `<script>`, gluing strings together with f-strings lets it run as code. That's **injection**, the same family as SQL injection. Python 3.14 added **template strings** (t-strings, PEP 750) for exactly this reason. A t-string looks like an f-string, but instead of producing a finished string it hands you the pieces, the fixed text and the values *separately*, so your code can escape every value before joining them.

**In this milestone we will** build `kiln bench matrix`, a runner that executes every lab across every interpreter in a fair, interleaved, resumable order. We'll write the statistics with nothing but the standard library. And we'll render a self-contained HTML report with charts, using t-strings and our own escaping processor, plus a property-based test that *proves* no value can ever change the page's structure.

## 2. What you will learn

- **Explain** why benchmark results need repeats, warm-ups, interleaving and fresh processes, and **recognize** drift and noise in data. *(Testing & statistics)*
- **Explain** the bootstrap and confidence intervals intuitively, and **interpret** a ratio CI correctly ("no detectable difference" when it includes 1.0). *(Testing & statistics)*
- **Design** an experiment matrix: factors, levels, baselines, and why the run order matters. *(Testing & statistics)*
- **Explain** t-strings: the `Template` and `Interpolation` objects, how they differ from f-strings, and how a processor function turns them into safe output. *(Strings, grammar, t-strings; Security)*
- **Explain** context-dependent escaping (text vs attribute vs `<script>`) and why some contexts must be *forbidden* rather than escaped. *(Security)*
- **Use** `subprocess` to run code under different interpreters, and `random.Random(seed)` for reproducible randomness. *(Testing & ops)*
- **Explain** PEP 810 lazy imports and measure the startup win on 3.15 (the `core.startup` suite). *(Import system)*

## 3. Entry criteria

- K3–K8 are done, with their lab drivers in `labs/k3/` … `labs/k8/` producing valid ExperimentRecords (spec/10 §2).
- `kiln.bench.records.validate` exists (K0).
- All interpreters from ADR-004 are installed (or their documented fallbacks from K7 G7.1).
- `./scripts/dev.sh bench-snapshot` works (spec/07 §7).

## 4. Scope

### In scope
- **The one-run protocol** (§5.1): K3–K8 drivers get a single-run mode, so the runner controls repeats and ordering.
- `labs/k9/suites.toml`: the declarative suite catalogue (spec/08 §1).
- `src/kiln/bench/runner.py`: plan, interleave, execute, resume, environment checks, and `kiln bench matrix` (spec/05 §4).
- `src/kiln/bench/stats.py`: summary statistics, bootstrap CIs, ratio CIs and interpretation text, per ADR-018 and spec/10 §4.
- `src/kiln/bench/report/` (3.14+ only): `html.py` (the t-string processor), `svg.py` (charts), `pages.py` (page templates), `__init__.py` (version gate), and `kiln report` (spec/05 §4).
- The processor verifier (Hypothesis) and an injection test corpus.
- A new suite, `core.startup`: CLI startup time across interpreters, plus lazy imports on 3.15.
- One full matrix run and its published report.

### Out of scope (and why)
- Tracking regressions across commits over time (asv's main feature). That's a Stretch item, because one snapshot answers K9's question.
- JavaScript in the report: interactive charts, sorting. A static page is enough and has no script injection surface.
- numpy, scipy, matplotlib and jinja2 (ADR-005, ADR-018).

## 5. Design

### 5.1 The one-run protocol (retrofit for K3–K8 drivers)

Every lab driver that K9 uses must support:

```
<driver> --one-run --variant <name> --repeat-index <i> --results-dir <dir> [--param key=value ...]
```

- It performs **exactly one measured run** of that variant and writes **exactly one** ExperimentRecord. There are no warm-ups; the runner handles those (§5.3). It exits 0 on success and non-zero on failure, with the reason on stderr.
- `--param` overrides the driver's defaults. Every param it actually used is recorded in `record.params`.
- `--list-variants` prints the variant names, one per line, so the runner can validate `suites.toml`.
- Step 1 adds this mode to: `labs/k3/cow_experiment.py`, `labs/k4/cpu_io_bench.py`, `labs/k5/loop_lag_experiment.py`, `labs/k6/run_leaks.py`, `labs/k7/gc_replay.py` and `labs/k8/boundary_bench.py`. The drivers' existing "full lab" modes keep working. Under the hood they become loops over `--one-run`, so both paths share one code path.

### 5.2 `labs/k9/suites.toml`

```toml
# Every suite = one driver × a set of variants × a set of interpreters.
[defaults]
repeats = 10          # spec/10 §3 minimum for timings
warmups = 3
cooldown_s = 1.0
baseline_interp = "3.15"

[suite."k3.cow"]
driver = "labs/k3/cow_experiment.py"
variants = ["default", "disabled", "threshold0", "freeze"]
interpreters = ["3.13", "3.14", "3.15", "3.15t"]
repeats = 5                                   # memory metric; spec/10 §3 allows ≥5 with CV<2% justification
metrics = [ { name = "pss_per_worker_bytes", per_run = "median" },
            { name = "shared_per_worker_bytes", per_run = "median" } ]
question = "How much memory do forked workers share under each GC strategy?"

[suite."k4.executors"]
driver = "labs/k4/cpu_io_bench.py"
variants = ["sync-cpu", "threads-cpu", "interpreters-cpu", "asyncio-io", "threads-io", "sync-io"]
interpreters = ["3.15", "3.15t"]
params = { concurrency = 8 }
metrics = [ { name = "throughput", per_run = "value" }, { name = "latency", per_run = "p99" } ]
question = "Which executor wins for CPU-bound vs IO-bound jobs, with and without the GIL?"

# … k5.blocking, k6.leaks, k7.gc, k8.boundary, core.load, core.startup: same shape (step 1 writes them all)
```

| Field | Meaning |
|---|---|
| `driver` | repo-relative path. It must support §5.1 |
| `variants` | must be a subset of `driver --list-variants` (validated at plan time) |
| `interpreters` | uv version strings (ADR-004), including `3.15t` and the pinned `3.14.4`/`3.14.5` |
| `params` | fixed for the whole suite, and passed as `--param` |
| `metrics[].per_run` | how to reduce a record's metric to **one number per run**: `value` (a single-value metric), or `median`/`mean`/`p50`/`p90`/`p99`/`max` over its `samples` |
| `repeats`, `warmups`, `cooldown_s` | per-suite overrides |
| `baseline_interp` / `baseline_variant` | the "A" in ratio comparisons (spec/10 §4.3) |
| `question` | shown as the section heading in the report |
| `profile` | optional `quick` overrides (fewer repeats, fewer variants) for smoke runs. `kiln bench matrix --profile quick` |

**Suite list (complete):**

| Suite | Driver | Interpreters | Main metrics |
|---|---|---|---|
| `k3.cow` | `labs/k3/cow_experiment.py` | 3.13, 3.14, 3.15, 3.15t | PSS and shared bytes per worker |
| `k4.executors` | `labs/k4/cpu_io_bench.py` | 3.15, 3.15t (+3.13 for threads/asyncio-only variants) | throughput, p99 |
| `k5.blocking` | `labs/k5/loop_lag_experiment.py` | 3.15, 3.15t | req/s, p99, stalls |
| `k6.leaks` | `labs/k6/run_leaks.py` | 3.13, 3.15 | RSS slope (bytes/job) |
| `k7.gc` | `labs/k7/gc_replay.py` | 3.13, 3.14.4, 3.14.5, 3.15 | peak RSS, GC p99 pause, wall |
| `k8.boundary` | `labs/k8/boundary_bench.py` | 3.13, 3.15, 3.15t | ns/call |
| `core.load` | `labs/k9/core_load.py` (new; wraps `kiln bench load --task labs.echo`) | 3.13, 3.14, 3.15, 3.15t | jobs/s, p99, master CPU s/1k jobs |
| `core.startup` | `labs/k9/startup.py` (new; §5.7) | 3.13, 3.14, 3.15 | `kiln --help` wall ms; import time |

### 5.3 The runner (`src/kiln/bench/runner.py`, `kiln bench matrix`)

```
kiln bench matrix [--suite S ...] [--interpreters V ...] [--repeats N] [--profile quick]
                  [--plan-id ID] [--resume ID] [--dry-run] [--seed N] [--allow-9p]
```

1. **Load** `suites.toml` with `tomllib`, apply the CLI filters, and validate: the drivers exist, the variants come from `--list-variants`, and the interpreters can be found with `uv python find <v>`. Every error is reported at once, with exit 2.
2. **Plan:** build *cells* = (suite, variant, interpreter). Write `results/K9/matrix/<plan_id>/plan.json` with the cells, repeats, warm-ups, seed (default `20261103`), git commit and creation time. `plan_id` = UTC timestamp + 4 hex.
3. **Order** (spec/10 §3 interleaving, plus randomization):
   - **Warm-up phase:** for each cell, `warmups` runs written to a scratch directory and **discarded**.
   - **Measured phase:** `R` rounds. In round *r*, every cell runs **once**, in an order given by `random.Random(seed + r).shuffle(cells)`.
   - Round-robin guarantees each cell is spread evenly over the whole run time, which handles drift. The per-round shuffle removes any fixed "cell X always runs after cell Y" effect, such as caches warmed by the previous run.
   - The resulting order is saved in `plan.json`, so it's reproducible.
4. **Execute** each run as a **fresh subprocess** (spec/10 §3):
   ```
   env KILN_PY=<interp> scripts/dev.sh run <driver> --one-run --variant <v> --repeat-index <r> --results-dir results/K9/matrix/<plan_id>/runs --param ...
   ```
   `scripts/dev.sh run` picks the interpreter's venv (spec/08 §3). For pinned interpreters without a Kiln venv (K7's 3.14.4/3.14.5), the driver itself is the one that launches them (K7 §5.3), so `KILN_PY` stays at 3.15 for that suite and the interpreter goes in as `--param interp=3.14.4`. `suites.toml` marks this with `interp_mode = "param"`.
5. **Between runs:** sleep `cooldown_s`, then read `/proc/loadavg`. If the 1-minute load is > 2.0, wait up to 30 s for it to fall. If it's still high, run anyway and append `"high_load_before_run=<x>"` to the record's `notes`, which the report surfaces as a warning.
6. **Environment guard:** if the repo root is under `/mnt/` (9P), refuse with exit 2 and the message `run from the ext4 snapshot: ./scripts/dev.sh bench-snapshot && cd ~/kiln-bench` (ADR-003), unless `--allow-9p` is set, in which case every record gets `host.notes += "source=9p"` and the report shows a red banner.
7. **Failures:** a failed run is recorded in `plan.json` under `failures` with its stderr tail, and the runner continues. A cell with fewer than half its repeats successful is reported as `insufficient data` and gets no CI.
8. **Resume:** `--resume <plan_id>` skips any (cell, repeat) that already has a valid record. Ctrl-C finishes the current run, saves progress and exits 130.
9. **`--dry-run`:** prints the cell count, the total runs, and the estimated duration (median `duration_s` per cell from earlier plans if available, else `unknown`). It runs nothing.

### 5.4 Statistics (`src/kiln/bench/stats.py`, stdlib only)

| Function | Definition |
|---|---|
| `summarize(xs) -> Summary` | n, median, mean, stdev (`statistics.stdev`, n ≥ 2), CV, min, max |
| `percentile(xs, p) -> float` | nearest-rank method (spec/10 §6), documented with a worked example in the docstring |
| `bootstrap_ci(xs, stat=median, resamples=10_000, alpha=0.05, seed=20261103) -> (lo, hi)` | percentile bootstrap (spec/10 §4.2). n < 5 → raises `InsufficientData` |
| `ratio_ci(a, b, ...) -> (point, lo, hi)` | `median(b)/median(a)`, with a and b resampled **independently** each round (spec/10 §4.3) |
| `interpret(point, lo, hi, *, lower_is_better: bool, metric_label: str) -> str` | produces exactly spec/10 §4.3's wording ("B is faster/smaller: about 33% less (95% CI 29–38%)" / "no detectable difference at this sample size") |
| `drift(xs_in_run_order) -> float` | Spearman rank correlation between repeat index and value (implemented by hand). |ρ| > 0.6 triggers a "possible drift" warning in the report |

The direction (`lower_is_better`) comes from the metric's unit: `bytes`, `s`, `ms`, `us` and `ns` mean lower is better, and `jobs/s` and `req/s` mean higher is better. Overrides go in `suites.toml` (`better = "lower"|"higher"`).

### 5.5 The HTML processor (`src/kiln/bench/report/html.py`, Python ≥ 3.14)

```python
from string.templatelib import Template, Interpolation

class SafeHTML(str):
    """A string we promise is already-safe markup. Only produced by html() itself and svg.py."""

def html(template: Template) -> SafeHTML: ...
```

**Rules** (each has a test):

| Situation | Behaviour |
|---|---|
| Static text parts of the template | emitted unchanged (they're written by us, the trusted author) |
| Interpolation with `conversion` `r`/`s`/`a` | apply `repr`/`str`/`ascii` first |
| Interpolation with a `format_spec` | apply `format(value, spec)` next, for example `{ratio:.2f}` |
| Value is a `SafeHTML` | inserted as-is |
| Value is a `Template` | rendered recursively with `html()`, so nested templates are trusted structure with their own interpolations escaped |
| Value is a `list`/`tuple` | each element is rendered by these same rules, then concatenated |
| Value is `None` | the empty string |
| Anything else | `html.escape(str(value), quote=True)`, which escapes `& < > " '` |
| An interpolation directly after `=` with no opening quote (an unquoted attribute) | **`ValueError("unquoted attribute value; write attr=\"{x}\"")`**. Unquoted attributes can be broken out of with a space, which escaping doesn't prevent |
| An interpolation inside `<script>…</script>` or `<style>…</style>` (detected by scanning the preceding static text for an unclosed raw-text element) | **`ValueError("interpolation inside <script>/<style> is not supported")`**. HTML escaping is the wrong escaping there |

- **The primer must show the f-string version of the same page being exploited** with a test string like `</td><script>alert(1)</script>`, and the t-string version not being exploited.

### 5.6 Charts (`src/kiln/bench/report/svg.py`)

- `bar_chart_with_ci(rows: list[BarRow], *, title, unit) -> SafeHTML`: horizontal bars, one per (variant, interpreter), each with a median bar and a CI whisker. A linear scale from 0 to `max(hi) × 1.1`. Tick labels are computed by a "nice numbers" helper (1/2/5 × 10ⁿ).
- `histogram(buckets: list[tuple[str, int]], *, title) -> SafeHTML`: for GC pauses (K7) and loop lag (K5).
- `line_chart(series: list[Series], *, title, x_label, y_label) -> SafeHTML`: RSS over time (K6, K7).
- Every chart is built **with `html()` t-strings**, so labels are escaped, and includes `<title>` and `<desc>` for accessibility. Every chart is followed by a plain HTML **data table** of the same numbers, so readers (and screen readers) never depend on reading pixels.
- Colours: a fixed palette of 8 colour-blind-safe colours, defined once as a constant with a comment on its source.

### 5.7 The `core.startup` suite (`labs/k9/startup.py`)

- **Variant `cli-help`:** wall time of `python -m kiln --help` (a fresh process; `perf_counter_ns` around `subprocess.run` in the driver), plus `-X importtime` parsed into "total import µs" and the top 10 modules.
- **Variant `cli-help-lazy`** (3.15 only): the same, with `kiln/cli.py` declaring `__lazy_modules__ = [...]` for its heavy, rarely-used imports (`kiln.bench.*`, `kiln.forkcheck`, `kiln.gateway`). That's PEP 810's compatibility form, and it's still valid 3.13 syntax (ADR-004's syntax floor). The baseline is the same CLI with lazy imports globally disabled (via the `-X lazy_imports=…` / `PYTHON_LAZY_IMPORTS` control, verified by G9.2).
- **Step 5** adds `__lazy_modules__` to `kiln/cli.py`, with `# PY:` and `# INCIDENT(lazy-imports):` comments, and a test that `kiln --help` output is identical with lazy imports on and off.

### 5.8 The report (`kiln report RESULTS_DIR -o report.html`)

- **Version gate:** `kiln report` on < 3.14 exits 2 with `kiln report needs Python ≥ 3.14 (t-strings, PEP 750); run: KILN_PY=3.15 ./scripts/dev.sh run -m kiln report ...`. The gated package `bench/report/` is imported lazily (ADR-004).
- **Input:** a plan directory (`results/K9/matrix/<plan_id>`) or any directory of records (they're grouped by `milestone/experiment/variant/interpreter`).
- **Page structure:** a single self-contained file with inline CSS, no JS and no external assets:
  1. Title, plan id, date, git commit + dirty flag, and the banner(s): 9P source, dirty tree, failures.
  2. **How to read this report**, for a beginner: what a median, a CI and a ratio CI mean, and the spec/10 §4.3 wording rules, with a mini worked example.
  3. **Environment**: host, kernel, CPUs, pinning, interpreters with builds, and `gil_enabled_at_end` per 3.15t run (any `true` gets a red warning).
  4. **Method**: warm-ups, repeats, interleaving, seed, cooldown, the bootstrap settings.
  5. **One section per suite**: the `question`, a summary table (`variant | interp | n | median [95% CI] | CV | ratio vs baseline [CI] | interpretation`), charts, warnings (CV > 5% "noisy", drift, insufficient data, high load), and links to the raw record files (relative paths).
  6. **Appendix**: the failures list, and the full `plan.json` embedded in a `<details>` block.
- **Output determinism:** with the same records and seed, the HTML is byte-identical. A test checks this, which keeps diffs reviewable.

## 6. Steps

1. **One-run protocol retrofit + `suites.toml` (all suites) + `labs/k9/core_load.py`.**
   - *Docs first:* `concepts/experiment-design.md`, `python/subprocess.md`.
   - *Tests:* every suite validates (drivers exist, `--list-variants` ⊇ the variants); each driver's `--one-run` writes exactly one valid record in a quick-profile smoke test (marked `lab`).
2. **`stats.py`.**
   - *Docs:* `concepts/bootstrap.md`, `concepts/confidence-intervals.md`, extending `concepts/benchmarking-statistics.md` (written in K3 per spec/10), `python/statistics-module.md`, `python/random-with-seed.md`.
   - *Tests:* known-answer tests on tiny hand-computed samples (median, nearest-rank percentiles); a Hypothesis property that the CI contains the point estimate; identical inputs give the same CI (seeded); `ratio_ci(a, a)` contains 1.0; `interpret` gives the exact spec/10 strings for three crafted cases; `drift` detects a monotone series.
3. **The runner (§5.3).**
   - *Tests:* the plan order is round-robin (every cell appears once per round) and reproducible from the seed; resume skips completed work; the 9P guard refuses under `/mnt/`; a failing driver is recorded and the run continues; `--dry-run` runs nothing (no processes spawned, checked by a stub driver that writes a marker file).
4. **The HTML processor + its verifier + charts (§5.5, §5.6).**
   - *Docs:* `concepts/t-strings-and-injection.md`, `python/t-strings.md`, `python/string-templatelib.md`.
   - *Gates:* G9.1, G9.3.
   - *Tests:*
     - every rule in §5.5's table
     - **the processor verifier** (Hypothesis, `max_examples=5000`): for any text `s`, `html(t'<p title="{s}">{s}</p>')`, parsed with `html.parser.HTMLParser`, yields exactly one `p` start tag with attribute `title == s`, a text node equal to `s`, one `p` end tag and nothing else
     - a fixed **injection corpus** (`tests/unit/report_injection_cases.txt`: `<script>`, `" onmouseover="`, `</p><img src=x onerror=1>`, `&amp;` round-trips, Unicode bidi characters, NUL) all pass the same property
     - a **contrast test** shows that the equivalent f-string *fails* the property on the corpus. It's marked as "expected to fail" and is there to teach the difference, not to check `html()`.
5. **Report pages + `core.startup` + lazy imports (§5.7, §5.8).**
   - *Gate:* G9.2.
   - *Docs:* `python/lazy-imports.md`.
   - *Tests:* determinism (byte-identical output); version-gate message on 3.13; warnings appear for crafted records (CV, drift, `gil_enabled_at_end: true` on 3.15t, `source=9p`).
6. **Full matrix run + published report + lab report + tour + reference comparison.**
   - Run `kiln bench matrix` from the ext4 snapshot. Publish `results/published/K9/<plan_id>/` (records + `plan.json` + `report.html`).

## 7. Lab design

- **Question:** across 3.13, 3.14(.4/.5), 3.15 and 3.15t, which K3–K8 findings hold, which change, and by how much, with honest uncertainty?
- **Implementer's hypothesis:** written per suite in the lab report *before* the run, by reading the earlier milestones' conclusions. For example: "K4 CPU threads scale only on 3.15t; K7 peak memory is higher only on 3.14.4; abi3 K8 numbers are similar on 3.13 and 3.15". The report then checks each one.
- **Independent variables:** suite × variant × interpreter (the factors in `suites.toml`).
- **Dependent variables:** each suite's metrics (§5.2), reduced per run via `per_run`.
- **Controls:**
  - the ext4 snapshot only
  - the same git commit for the whole plan (the runner refuses to resume if HEAD changed)
  - spec/10 §5 noise controls (heavy apps closed, power plan recorded)
  - CPU pinning only where the suite's own lab specifies it (K7, K8), passed as `params`
- **Repeats and statistics:** per [spec/10](../spec/10-measurement-methodology.md) and `suites.toml` (≥ 10 for timings; ≥ 5 for memory with the CV justification), warm-ups 3, round-robin + seeded shuffle (§5.3), bootstrap 10,000, seed 20261103.
- **Procedure:**
  1. `./scripts/dev.sh bench-snapshot && cd ~/kiln-bench`
  2. `kiln bench matrix --dry-run`. Record the estimate. If it's above 8 hours, run `--profile quick` first, then the full plan overnight.
  3. `kiln bench matrix`
  4. `KILN_PY=3.15 ./scripts/dev.sh run -m kiln report results/K9/matrix/<plan_id> -o results/K9/matrix/<plan_id>/report.html`
- **Result tables** (in the lab report; headers only):
  - *Plan summary:* `suites | cells | runs | failures | duration h | source | seed`
  - *Per suite:* `variant | interp | n | median [95% CI] | ratio vs baseline [CI] | interpretation | earlier milestone's conclusion holds?`
  - *Verifier:* `property | examples | result`, and `injection case | t-string result | f-string result`
  - *Startup:* `interp | variant | kiln --help ms [CI] | total import µs | ratio lazy/eager [CI]`

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G9.1 | `string.templatelib` exposes `Template` and `Interpolation`; iterating a `Template` yields `str` and `Interpolation` in source order; `Interpolation` has `value`, `expression`, `conversion`, `format_spec`; t-strings are 3.14+ syntax | REPL on 3.14 and 3.15 (paste into the Python note) | adapt to the real API (for example `template.strings`/`template.interpolations` pairs) and update the note and errata |
| G9.2 | PEP 810 on 3.15: `__lazy_modules__` makes listed imports lazy; a global switch (`-X lazy_imports=…` / `PYTHON_LAZY_IMPORTS`) can disable them for the baseline; `lazy import` syntax exists | `python3.15 -X importtime -c "import kiln.cli"` with and without the switch; read `python3.15 --help-xoptions` | use the real switch names. If `__lazy_modules__` doesn't exist as described, use the `lazy import` syntax inside a 3.15-only gated module `kiln/_lazy_cli.py`, and record an ADR |
| G9.3 | `html.parser.HTMLParser` reports attributes and data the way the verifier assumes (entity unescaping on `convert_charrefs=True`) | a unit test with known inputs | adjust the verifier's comparison (for example compare unescaped values) |
| G9.4 | `uv python find <v>` resolves every interpreter in `suites.toml`, including the pinned ones | the runner's validation step | apply K7's G7.1 fallback and record the substitution in `plan.json` |
| G9.5 | the full plan's duration is acceptable | `--dry-run` estimate | use the `quick` profile, or split the plan by suite and run over several sessions with `--resume` |

## 9. Acceptance criteria

- **AC-9.1** Every K3–K8 driver supports the one-run protocol, and `suites.toml` validates (`tests/lab/test_suites_toml.py`).
- **AC-9.2** `stats.py` passes its known-answer and property tests, and `interpret` produces the exact spec/10 §4.3 wording.
- **AC-9.3** The runner's ordering is round-robin, reproducible from the seed and resumable. The 9P guard works (`tests/unit/test_runner.py`).
- **AC-9.4** The processor verifier passes 5,000 Hypothesis examples plus the injection corpus. The unquoted-attribute and `<script>` cases raise `ValueError`. The f-string contrast test demonstrates the vulnerability.
- **AC-9.5** `kiln report` is deterministic (byte-identical output for the same input) and refuses < 3.14 with the exact message.
- **AC-9.6** A full (or documented-partial) matrix plan has run from the ext4 snapshot, and `results/published/K9/<plan_id>/report.html` is committed alongside its records.
- **AC-9.7** The lab report states, per suite, whether each earlier milestone's conclusion held across interpreters, using spec/10 wording.
- **AC-9.8** The Docs deliverables exist and `./scripts/dev.sh docs-check` passes.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/benchmarking-statistics.md` (extend K3's) | noise, the median vs the mean, CV, drift, why fresh processes, how to read the K9 report |
| Concept | `docs/concepts/bootstrap.md` | resampling with replacement, with a 5-number worked example by hand, why it works, its limits (small n, dependence) |
| Concept | `docs/concepts/confidence-intervals.md` | what a 95% CI does and doesn't mean (common misreadings), ratio CIs, "includes 1.0 → no detectable difference" |
| Concept | `docs/concepts/experiment-design.md` | factors, levels, baselines, controls, interleaving, randomization, warm-ups, pre-registering hypotheses and thresholds |
| Concept | `docs/concepts/t-strings-and-injection.md` | injection explained from zero; the f-string exploit demo; t-string processors; context-dependent escaping; why some contexts are forbidden; links to Cartographer C5 |
| Python | `docs/python/t-strings.md` | syntax `t"..."`, the difference from f-strings, conversions and format specs |
| Python | `docs/python/string-templatelib.md` | `Template`, `Interpolation`, iteration, writing a processor |
| Python | `docs/python/statistics-module.md` | `median`, `mean`, `stdev`, `quantiles`, `linear_regression` |
| Python | `docs/python/random-with-seed.md` | `random.Random(seed)`, why a private RNG instance, `choices` for resampling, reproducibility |
| Python | `docs/python/subprocess.md` | `subprocess.run`, `Popen`, `pass_fds`, environment, return codes, capturing output, timeouts |
| Python | `docs/python/lazy-imports.md` | PEP 810: `lazy import`, `__lazy_modules__`, global controls, gotchas (errors at first use, plugin registration) |
| Tour | `docs/tours/K9.md` | stops: suites.toml → runner plan/order → one-run driver contract → stats → html processor → svg → pages → startup suite |
| Lab | `docs/labs/K9-matrix.md` | per the template; links to the published report |

## 11. Reference comparison assignment

1. **asv** (`asv/runner.py`, `asv/statistics.py`, the HTML output): *Questions:* how does asv decide the number of repeats? Does it interleave? What statistic and interval does it report, and how does it decide a regression is "significant"?
2. **pyperf** (`pyperf/_runner.py`, `_worker.py`): *Questions:* why does pyperf spawn **worker processes** and calibrate loop counts? How does it compare with our one-run protocol and K8's ns/call loops? What do `pyperf system tune` settings do, and which of them are impossible in WSL (spec/10 §5)?
3. **PEP 750's examples** (an HTML processor): compare with ours. *Question:* how does the PEP treat attribute context, and what does our "forbid unquoted attributes and `<script>`" rule add?

## 12. Risks and pitfalls

- **Very long runs.** Always `--dry-run` first, and rely on `--resume`.
- **The machine changes mid-plan** (Windows updates, the laptop unplugged). The drift check and the load guard flag it. Record power state in `host.notes`.
- **A suite measured from 9P by accident.** That's why the guard refuses by default.
- **3.15t runs where the GIL silently came back.** The report checks `gil_enabled_at_end` and flags it (K4's lesson).
- **Treating the report as ground truth.** It's one machine, a WSL VM and a laptop. The report's "Limitations" section is mandatory.
- **Accidentally escaping trusted markup twice** (`&amp;lt;`). Only `SafeHTML` and nested `Template`s bypass escaping. The tests cover nested templates and lists.

## 13. Checkpoint quiz (seed questions)

1. Why does the runner interleave cells round-robin instead of running all repeats of one cell back to back?
2. Why shuffle the order *within* each round, and why with a fixed seed?
3. In your own words: what does the bootstrap do, and why does it need no assumption of a bell curve?
4. A ratio CI is [0.97, 1.05]. What may you say, and what must you not say?
5. Why is the median the default summary for timings?
6. What does a t-string give your code that an f-string doesn't?
7. Why does our processor *refuse* `<a href={url}>` instead of escaping it?
8. Why is the processor verifier a *property* test rather than a list of examples?
9. Why must `kiln report` output be byte-identical for the same input?
10. What does `__lazy_modules__` change about when `kiln.forkcheck` gets imported when you run `kiln --help`?

## 14. Stretch / later

- History mode: keep one plan per commit and chart regressions over time (asv's model).
- An open-data export of the matrix (CSV) alongside the HTML.
- Run the matrix with CPU pinning for *all* suites, and compare the CVs to quantify how much pinning reduces noise in WSL.
