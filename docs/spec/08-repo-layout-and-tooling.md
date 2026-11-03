# 08 — Repository Layout and Tooling

> **Status:** Approved spec. **Implemented in:** K0, with folders filled in as milestones go.
> **Concepts:** package, module, `src` layout, virtual environment, lockfile, linter, formatter, type checker, test runner. The K0 Python notes are `python/modules-and-packages.md` and `python/virtual-environments.md`.

---

## 1. The full tree (end state)

The milestone in brackets is the one that creates each item.

```
Kiln/
├── README.md                     [spec] what Kiln is, the 60-second tour, where to start reading
├── pyproject.toml                [K0]  project metadata, dependency groups, tool config (§2)
├── uv.lock                       [K0]  exact versions of every dependency (committed)
├── .python-version               [K0]  "3.15" — default interpreter for uv
├── .gitignore  .gitattributes    [K0]  (§4)
├── kiln.example.toml             [K1]  annotated example config (every key, with comments)
│
├── src/kiln/                     [K0+] the package (module map: spec/01 §5)
├── tests/
│   ├── conftest.py               [K0]  shared fixtures (spec/09 §4)
│   ├── unit/                     [K0+] fast, single-process tests
│   ├── integration/              [K1+] real master + workers in subprocesses
│   ├── lab/                      [K1+] lab smoke tests (marked `lab`; short versions of each experiment)
│   └── snippets/                 [K0]  runs every docs/concepts/snippets/*.py and docs/python/snippets/*.py
├── labs/                         [K1+] lab workloads and drivers, one folder per milestone
│   ├── k1/  oom_demo.py, reload_under_load.py
│   ├── k2/  confirm_harness.py
│   ├── k3/  preload_graph.py, cow_experiment.py, rogue_gc_enabler.py
│   ├── k4/  gil_reenabler/ (C ext: with_gil.c, without_gil.c → kiln_gil_labs.with_gil / .without_gil), wrapper_pkg/, scan_gil.py, race_demo.py, cpu_io_bench.py
│   ├── k5/  blocking_endpoints.md, loop_lag_experiment.py, http_load.py, faulthandler_watchdog.py
│   ├── k6/  run_leaks.py, sampler.py, slope.py, diagnose_{tracemalloc,memray,tachyon,pdb}.md  (leak tasks live in src/kiln/labs/tasks.py)
│   ├── k7/  django_like_cycles.py, gc_replay.py
│   ├── k8/  boundary_bench.py
│   ├── k9/  suites.toml, core_load.py, startup.py
│   └── k10/ acceptance.py
├── corpus/                       [K2]  fork-checker calibration (ADR-017)
│   ├── real/<case>/              distilled.py, PROVENANCE.md, expected.json
│   ├── synthetic/<rule>/         pos_*.py, neg_*.py
│   └── upstream/                 git-ignored: populated by scripts/fetch-corpus.sh
├── rust/kiln_native/             [K8]  Cargo.toml, pyproject.toml (maturin backend), src/lib.rs
├── results/                      [K1+] ExperimentRecords (git-ignored, except published/) (ADR-022)
│   └── published/Kn/...          records cited by lab reports (committed)
├── scripts/
│   ├── dev.sh                    [K0]  the single entry point for every task (§3)
│   ├── check_docs.py             [K0]  the documentation checker (standards/documentation.md §10)
│   ├── bench-snapshot.sh         [K0]  rsync to ~/kiln-bench (spec/07 §7)
│   └── fetch-corpus.sh           [K2]  optional upstream checkouts
├── examples/
│   └── demo.py                   [K1]  the demo app used throughout the docs
└── docs/                         [spec] see docs/README.md for the reading order
    ├── README.md  glossary.md  faq.md  questions.md  decisions.md
    ├── spec/  standards/  milestones/  templates/
    ├── concepts/  (+ snippets/)  python/  (+ snippets/)
    ├── tours/  labs/
    └── research/  python-mastery-research.md, errata.md
```

### 1.1 Why the `src/` layout?

The package lives in `src/kiln/`, not `kiln/` at the top. With a top-level `kiln/`, running `pytest` from the repo root would import the *source folder directly* through the current directory, even if the installed package were broken or missing files. With `src/`, tests can only import Kiln the way users do, through the installed package. It's a small thing that prevents a whole class of "works on my machine" bugs.

## 2. `pyproject.toml` specification

### 2.1 Project metadata and build backend

```toml
[project]
name = "kiln"
version = "0.1.0"
description = "A pre-fork job server built from scratch to learn Python internals"
readme = "README.md"
requires-python = ">=3.13"
dependencies = []                     # ADR-005: stdlib only at runtime

[project.scripts]
kiln = "kiln.cli:main"                # creates the `kiln` command in the venv

[build-system]
requires = ["uv_build>=X.Y,<X.(Y+1)"] # X.Y = the uv minor version installed in K0; recorded in an ADR
build-backend = "uv_build"
```

**Why `uv_build` (and not hatchling)?** Kiln's main package is pure Python, in the standard `src/` layout, so it needs none of hatchling's plugins. `uv_build` comes from the same tool we already use for everything else (one tool to learn instead of two), and it's fast. The native code lives in **separate** small projects with their own backends: `rust/kiln_native` uses `maturin` and `labs/k4/gil_reenabler` uses `setuptools`. The main build therefore never has to compile anything. If `uv_build` turns out to lack something we need, switching to hatchling is a one-line ADR.

### 2.2 Dependency groups (PEP 735; ADR-005)

```toml
[dependency-groups]
dev    = ["pytest", "pytest-timeout", "hypothesis", "ruff", "mypy", "coverage"]   # coverage: see spec/09 §9 ADR proposal
lab-k3 = ["msgpack"]
lab-k4 = ["kiln-gil-labs"]            # our own C extension (ADR-015), built from labs/k4/gil_reenabler
lab-k6 = ["memray", "requests"]
lab-k8 = ["maturin", "kiln-native"]   # our Rust extension, built from rust/kiln_native
lab-k9 = []

[tool.uv]
default-groups = ["dev"]

[tool.uv.sources]
kiln-gil-labs = { path = "labs/k4/gil_reenabler" }
kiln-native   = { path = "rust/kiln_native" }
```

- Versions are **not** pinned in `pyproject.toml`. `uv.lock` pins them exactly.
- `uv sync` installs exactly the default groups and **removes** anything else from the venv. Lab groups are added explicitly: `uv sync --group lab-k3`. `scripts/dev.sh lab <id>` does this for you.
- `lab-k4` and `lab-k8` need a C compiler and Rust respectively (spec/07 §2, §9).

### 2.3 Ruff (linter + formatter)

```toml
[tool.ruff]
line-length = 100
target-version = "py313"
src = ["src", "tests", "labs", "scripts"]

[tool.ruff.lint]
select = [
  "E", "W",    # pycodestyle: basic style errors
  "F",         # pyflakes: unused imports/variables, undefined names
  "I",         # isort: import ordering
  "B",         # flake8-bugbear: likely bugs (mutable defaults, loop-variable closures…)
  "UP",        # pyupgrade: use modern syntax for py313
  "SIM",       # simplifications
  "C4",        # comprehension style
  "PT",        # pytest style
  "RUF",       # ruff-specific checks
  "D",         # pydocstyle: docstrings exist and are well-formed
  "ANN",       # annotations present
  "S",         # flake8-bandit: security smells (pickle, eval, shell=True…)
  "BLE",       # no blind `except Exception` without re-raise/log
  "T20",       # no stray print() in src (CLI output goes through a helper)
  "PTH",       # prefer pathlib
]
ignore = ["D203", "D213"]     # conflicting docstring layouts; we use D211 + D212 (Google)

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.per-file-ignores]
"tests/**"   = ["S101", "ANN", "D100", "D104"]   # assert is how pytest works; test docstrings are checked by check_docs.py instead
"labs/**"    = ["S", "T20", "BLE"]               # labs do dangerous things on purpose (LAB: tag required)
"scripts/**" = ["T20"]
"src/kiln/cli.py" = ["T20"]
```

### 2.4 mypy

```toml
[tool.mypy]
strict = true
python_version = "3.13"
files = ["src/kiln"]
exclude = ["src/kiln/bench/report/", "src/kiln/worker/executors/interpreters.py"]   # gated modules: checked separately
warn_unreachable = true
```

**Gated modules** use syntax or APIs newer than 3.13: t-strings in `bench/report/`, and `concurrent.interpreters` in `interpreters.py`. A 3.13-targeted mypy run would report syntax errors there. So `scripts/dev.sh typecheck` runs mypy **twice**:
1. Core: as configured above.
2. Gated: `mypy --python-version 3.15 src/kiln/bench/report src/kiln/worker/executors/interpreters.py`, run with the 3.15 interpreter.

Each new gated module must be added to both lists, and `check_docs.py` verifies that the two lists match.

### 2.5 pytest

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config -m 'not lab and not slow'"
timeout = 30
markers = [
  "unit: fast, in-process, no subprocesses",
  "integration: starts real Kiln processes",
  "lab: short version of a milestone experiment (run with -m lab)",
  "slow: > 10 s; excluded by default",
  "snippets: runs documentation snippets",
  "requires_ft: needs a free-threaded build (sys._is_gil_enabled exists and returns False)",
  "requires_314: needs Python >= 3.14",
  "requires_315: needs Python >= 3.15",
  "requires_gcc: needs a C compiler (build-essential)",
  "requires_cgroup: needs a writable cgroup v2 subtree (OOM labs)",
  "requires_native: needs the kiln_native Rust extension built for this interpreter (K8)",
]
```

`tests/conftest.py` turns the `requires_*` markers into skips, with a reason like `"needs Python >= 3.14 (running 3.13.9)"`.

### 2.6 coverage

```toml
[tool.coverage.run]
source = ["kiln"]
branch = true
parallel = true                 # subprocess workers write their own data files
concurrency = ["thread", "multiprocessing"]
[tool.coverage.report]
fail_under = 85
omit = ["*/bench/report/*", "*/executors/interpreters.py", "*/labs/*"]
```

See [09-testing-strategy §9](09-testing-strategy.md#9-coverage).

## 3. `scripts/dev.sh`: one entry point for everything

`scripts/dev.sh` is a bash script (`set -euo pipefail`, every path quoted; see spec/07 §10). It:
- chooses the interpreter from `$KILN_PY` (default `3.15`)
- sets `UV_PROJECT_ENVIRONMENT="$HOME/.cache/kiln/venvs/py$KILN_PY"` (spec/07 §4)
- runs the subcommand

`./scripts/dev.sh help` prints the table below.

| Subcommand | What it runs | Notes |
|---|---|---|
| `doctor [--milestone Kn]` | `uv run kiln doctor` | spec/07 §12 |
| `sync [--group G]` | `uv sync` (+ groups) | creates or updates the venv |
| `fmt` | `ruff format .` + `ruff check --fix --select I .` | rewrites files |
| `lint` | `ruff check .` + `ruff format --check .` (+ `shellcheck scripts/*.sh` if installed) | |
| `typecheck` | the two mypy runs (§2.4) | |
| `test [pytest args]` | `uv run pytest` | default markers exclude `lab` and `slow` |
| `docs-check` | `uv run python scripts/check_docs.py` | |
| `check` | `lint` + `typecheck` + `test` + `docs-check` | **must be green before every commit** (milestone-workflow §2e) |
| `test-matrix` | `test` once per interpreter in 3.13, 3.14, 3.15, 3.15t, each in its own venv | prints a summary table |
| `coverage` | `coverage run -m pytest -m "not lab"` → `coverage combine` → `coverage report` | |
| `bench-snapshot` | `scripts/bench-snapshot.sh` | spec/07 §7 |
| `lab <id> [args]` | syncs the lab's group, then runs `labs/<kN>/...` with `--results-dir "$REPO/results"` | ids are listed in each milestone spec, e.g. `k1-oom`, `k3-cow`, `k9-matrix` |
| `run <file.py> [args]` | `uv run python <file.py> [args]` with the chosen interpreter and venv | used by primers' "See it for yourself" sections |
| `serve-demo [kiln serve args]` | `uv run kiln serve examples.demo:app --labs` | handy for manual exploration |
| `clean` | removes `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.coverage*` | **never** touches `results/` or venvs |

## 4. `.gitignore` and `.gitattributes`

```gitignore
__pycache__/
*.py[cod]
.venv/
.pytest_cache/ .mypy_cache/ .ruff_cache/ .hypothesis/
.coverage* htmlcov/
build/ dist/ *.egg-info/
*.so                                # compiled extensions (K4, K8)
rust/kiln_native/target/
results/*
!results/published/
corpus/upstream/
memray-*.bin  *.prof  *.svg.tmp
```

`.gitattributes`: see [spec/07 §11](07-environment.md#11-line-endings).

## 5. The import-boundary test

`tests/unit/test_import_boundaries.py` enforces the dependency rule from [spec/01 §5](01-architecture.md#5-package-layout-module-map) **without** any third-party tool:

1. Walk every `.py` file under `src/kiln/`.
2. Parse it with `ast.parse`, and collect `Import` and `ImportFrom` nodes, resolving relative imports (`from ..protocol import framing`) to absolute names.
3. Map each file to its **layer**: `protocol`, `master`, `worker`, `gateway`, `forkcheck`, `launch`, `memory`, `bench`, `labs`, `native`, or `core` (`config`, `app`, `cli`, `doctor`, `logsetup`, `__init__`).
4. Check against an allow-table kept in the test file, for example `protocol → {}` (stdlib only), `master → {protocol, config, app, memory, launch, logsetup}`, `gateway → {protocol, config, bench.records, logsetup}`, and `master ↛ worker`, `worker ↛ master`.
5. On a violation, print `src/kiln/master/arbiter.py:12 imports kiln.worker.main — master must not import worker (spec/01 §5)`.

The test docstring explains *why* layering matters: it keeps each part understandable on its own, and it stops the master from accidentally running worker code.

## 6. Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Modules and packages | `snake_case`, short nouns | `framing.py`, `supervisor.py` |
| Classes | `PascalCase` | `FrameDecoder`, `WorkerHandle` |
| Functions, methods, variables | `snake_case`, verbs for functions | `classify_exit`, `jobs_done` |
| Constants | `UPPER_SNAKE` | `MAX_FRAME_BYTES_DEFAULT` |
| Private (module-internal) | leading `_` | `_drain_wakeup_fd` |
| Enum members | `UPPER_SNAKE` | `JobState.TIMED_OUT` |
| Units in names | always suffix the unit | `timeout_s`, `rss_bytes`, `lag_ms`, `started_mono` |
| Time variables | `_mono` for `time.monotonic()`, `_wall_ms` for wall-clock | `submitted_mono`, `finished_wall_ms` |
| Log events | `snake_case`, past tense or a noun | `worker_died`, `loop_stall` |
| Test files and functions | `test_<module>.py`, `test_<behaviour_as_sentence>` | `test_frame_split_at_every_offset_decodes_identically` |
| Lab ids | `k<N>-<slug>` | `k3-cow`, `k1-oom` |
| Branches | `milestone/k<N>-<slug>` | `milestone/k2-start-methods` |
| Docs files | `kebab-case.md` | `zombies-and-reaping.md` |
| Concept / Python note slugs | the file name without `.md` | `CONCEPT(zombies-and-reaping)` |
| Results files | `results/<milestone>/<experiment>/<UTC timestamp>-<interp tag>.json` | `results/K3/cow-gc-modes/20261103T191204Z-py3.15.json` |
| Config keys | `snake_case`, grouped by TOML section | `worker.max_rss_mb` |
| Env vars | `KILN_<SECTION>__<KEY>` | `KILN_WORKER__MAX_JOBS` |
