# K0: Foundations (environment, tooling, first primers)

> **Status:** 📝 specified · **Depends on:** nothing · **Branch:** `milestone/k0-foundations` · **Size:** M
> **Incident:** none. K0 builds the workshop every later milestone works in.
> **Reference implementations (read *after* building):** any well-run PyPA project's `pyproject.toml` (for example `pypa/pip`, `pytest-dev/pytest`), uv's project docs, and Ruff's own configuration.

---

## 1. The story (plain English)

Imagine you're about to learn woodworking. Before you build any furniture, someone has to set up the workshop: a bench, sharp tools, a place for everything, and a notebook to record what you learn. **K0 is the workshop.**

Kiln runs on Linux, but your computer runs Windows. Windows has a built-in way to run a real Linux system next to it, called **WSL2** (Windows Subsystem for Linux). Yours already runs **Debian 13**. K0 installs the remaining tools: several Python versions (because some lessons compare versions), a C compiler (needed later to build small native extensions), and Python's testing and checking tools.

K0 also sets up the **rules of the workshop**:
- a script (`scripts/dev.sh`) that runs every common command the same way every time;
- a checker (`scripts/check_docs.py`) that refuses to call a milestone "done" when its explanations are missing;
- a `kiln doctor` command that inspects your machine and tells you, in plain words, what's missing and how to fix it.

You are new to Python itself. So before any real Kiln code exists, K0 writes the **starter set of Python notes**. These are short, friendly explanations of the Python features you'll see in every file: functions, classes, imports, exceptions, `with`, decorators, and more. K0 also writes the first **concept primers**: what a terminal is, what a process is, what a file descriptor is, and what a server is.

In this milestone we will set up the environment, the tooling, and the documentation machinery. We'll write the first primers and build `kiln doctor`, so that from K1 onward every line of code has somewhere to be explained.

## 2. What you will learn

- **Explain** what WSL2 is, why Kiln must run inside it, and how a Windows path like `C:\Users\...` becomes `/mnt/c/Users/...`. *(Environment, research §6)*
- **Use** the terminal for the handful of commands you'll need: `cd`, `ls`, `cat`, running a script, reading an exit code with `echo $?`. *(Testing & ops)*
- **Explain** what `uv` does (it installs Python versions, creates virtual environments, and locks dependencies) and what a **virtual environment** is. *(Packaging)*
- **Read** a `pyproject.toml` and say what each section is for. *(Packaging)*
- **Read** basic Python: variables, functions, classes, imports, exceptions, `with`, decorators, f-strings, comprehensions, type hints. *(Data model basics)*
- **Explain** what a process, a file descriptor and a server are, at the level of "I could draw it on a whiteboard." *(Processes; prerequisite for K1)*
- **Run** tests with `pytest`, and **explain** what a test, an assertion and a fixture are. *(Testing)*
- **Explain** why Kiln keeps an "experiment record" for every measurement. *(Statistics; prerequisite for K3–K9)*

## 3. Entry criteria

- WSL2 Debian 13 runs, and `uv` is installed inside it. Both were verified on 2026-11-03 ([ADR-003](../decisions.md#adr-003)).
- The repository exists at `C:\Users\Karthik Reddy\Documents\Study\Projects\Kiln` with the spec-pass documents.
- The learner has `sudo` rights inside WSL, for `apt install`.

## 4. Scope

### In scope
- System packages, Python interpreters, project skeleton, tooling config, lockfile.
- `scripts/dev.sh`, `scripts/bench-snapshot.sh`, `scripts/check_docs.py`.
- `kiln --version`, `kiln doctor`.
- `src/kiln/bench/records.py` (the ExperimentRecord writer and reader).
- The import-boundary test (`tests/test_import_boundaries.py`).
- `.gitattributes`, `.gitignore`, `README.md` updates.
- The **starter documentation set** (§10), plus the incident registry and errata files.

### Out of scope (and why)
- Any server, master or worker code. That's K1.
- The Rust toolchain. It's K8's job, and installing it now would only go stale. `doctor` reports it as `WARN (needed by K8)`.
- CI on a hosted service. There's no remote ([ADR-025](../decisions.md#adr-025)). `dev.sh check` *is* the CI.
- Comparing type checkers. That belongs to Cartographer C4 ([ADR-019](../decisions.md#adr-019)).

## 5. Design

### 5.1 System packages (Debian 13, via `apt`)

Install exactly the list in [spec/07 §2](../spec/07-environment.md#2-one-time-system-packages-k0-step-1). That section owns the list; K0 doesn't redefine it. The exact command, its output, and the date it was run go into the **setup log** (`docs/tours/K0.md` → "Appendix: setup log").

### 5.2 Python interpreters

Install exactly as in [spec/07 §3](../spec/07-environment.md#3-python-interpreters-k0-step-2):

```bash
(wsl) $ uv python install 3.13 3.14 3.14.4 3.14.5 3.15 3.15t
```

- The interpreter "tags" used everywhere else are `3.13`, `3.14`, `3.14.4`, `3.14.5`, `3.15`, `3.15t`.
- The matrix in [ADR-004](../decisions.md#adr-004) is: dev = `3.15`; tests = `3.13 3.14 3.15 3.15t`; lab-only = `3.14.4 3.14.5`.

### 5.3 Project skeleton

```
Kiln/
  pyproject.toml          project metadata, dependency groups, ruff/mypy/pytest config
  uv.lock                 committed (ADR-019)
  .python-version         "3.15"
  .gitattributes          * text=auto eol=lf ; *.sh text eol=lf ; *.ps1 text eol=crlf ; *.png binary
  .gitignore              __pycache__/ .venv/ .pytest_cache/ .mypy_cache/ .ruff_cache/ .hypothesis/
                          results/*  !results/published/  target/  *.so  build/ dist/ *.egg-info/
  README.md               (exists; K0 adds "Quick start" with the 3 commands to get going)
  src/kiln/__init__.py    __version__ = "0.1.0"; public API placeholder (App comes in K1)
  src/kiln/__main__.py    `python -m kiln` → kiln.cli.main()
  src/kiln/cli.py         argparse with subcommands: --version, doctor (more in K1+)
  src/kiln/doctor.py      environment checks (§5.6)
  src/kiln/bench/__init__.py
  src/kiln/bench/records.py
  scripts/dev.sh
  scripts/bench-snapshot.sh
  scripts/check_docs.py
  tests/conftest.py                   (shared fixtures; the 3.15t "GIL really off" session check, spec/09 §8)
  tests/unit/test_cli_smoke.py
  tests/unit/test_records.py
  tests/unit/test_import_boundaries.py
  tests/unit/test_check_docs.py       (unit tests of the checker, using tiny fixture files)
  tests/unit/test_doctor.py
  tests/snippets/test_snippets.py     (spec/09 §7)
  tests/fixtures/check_docs/...       (good/bad example files for each check)
  tests/fixtures/boundaries_bad/...   (a planted bad import)
  docs/concepts/snippets/             (runnable snippets referenced by primers)
  docs/python/snippets/
```

The test layout (`unit/`, `integration/`, `lab/`, `snippets/`) and its markers are defined in [spec/09 §2](../spec/09-testing-strategy.md#2-the-test-pyramid). K0 creates the directories.

### 5.4 `pyproject.toml` (the decisions; the exact text is the implementer's)

| Section | Content |
|---|---|
| `[project]` | `name = "kiln"`, `version = "0.1.0"`, `requires-python = ">=3.13"`, `dependencies = []` ([ADR-005](../decisions.md#adr-005)), `[project.scripts] kiln = "kiln.cli:main"` |
| `[build-system]` | `uv_build` (uv's own backend, the default for `uv init --lib`). Fallback: `hatchling` (see gate G0.3) |
| `[dependency-groups]` (PEP 735) | `dev = ["pytest", "pytest-timeout", "hypothesis", "ruff", "mypy", "coverage"]` (coverage per [ADR-027](../decisions.md#adr-027)); `lab-k3 = ["msgpack"]`; `lab-k6 = ["memray", "requests"]`; `lab-k8 = ["maturin"]`; `lab-k9 = []`. Versions are pinned through `uv.lock`, not with `==` in this file |
| `[tool.ruff]` | `target-version = "py313"` (the syntax floor, ADR-004), `line-length = 100`, `src = ["src", "tests", "scripts", "labs"]` |
| `[tool.ruff.lint]` | `select = ["E","F","W","I","B","UP","SIM","RUF","D","ANN","S","PL"]`, with `ignore` limited to documented exceptions, each with a `# WHY:` comment in the TOML. `pydocstyle.convention = "google"`. Per-file ignores: `tests/**` ignore `S101` (assert) and `ANN`; `labs/**` ignore `S` (labs do unsafe things on purpose, marked `# LAB:`) |
| `[tool.ruff.format]` | defaults (double quotes) |
| `[tool.mypy]` | `strict = true`, `python_version = "3.13"`, `files = ["src/kiln"]`, `warn_unreachable = true`. Version-gated modules get `[[tool.mypy.overrides]]` only if needed, with a reason |
| `[tool.pytest.ini_options]` | `testpaths = ["tests"]`, `addopts = "-ra --strict-markers -m 'not lab and not slow'"`, `timeout = 30`. Markers exactly as in [spec/09 §2 and §8](../spec/09-testing-strategy.md): `unit`, `integration`, `lab`, `snippets`, `slow`, `requires_ft`, `requires_314`, `requires_315`, plus `requires_gcc` and `requires_cgroup` (K0 adds these two; the list in spec/09 gets updated in the same commit). Also `--doctest-modules` on `src/kiln` |

### 5.5 `scripts/dev.sh`

The full list of subcommands, and what each runs, is the table in [`../spec/08-repo-layout-and-tooling.md`](../spec/08-repo-layout-and-tooling.md): `doctor, sync, fmt, lint, typecheck, test, docs-check, check, test-matrix, coverage, bench-snapshot, lab <id>, run, serve-demo, clean`. K0 implements all of them, except that `lab <id>` and `serve-demo` print "available from K1" until K1. K0-specific rules:

1. `#!/usr/bin/env bash` and `set -euo pipefail`. Every line commented ([documentation §4.4](../standards/documentation.md#44-how-dense-should-comments-be)), because the learner is new to shell too.
2. It **refuses to run outside Linux** with a clear message: "run me inside WSL: wsl.exe -d Debian --cd \"/mnt/c/...\" -- ./scripts/dev.sh ...".
3. It exports `UV_PROJECT_ENVIRONMENT="$HOME/.cache/kiln/venvs/${KILN_PY:-3.15}"` so venvs live on ext4, not 9P ([ADR-003](../decisions.md#adr-003)). `KILN_PY` selects the interpreter tag.
4. `test-matrix` loops over `3.13 3.14 3.15 3.15t`, runs `uv sync --python <tag> --group dev` and `pytest` for each, and prints a summary table (tag → passed/failed/skipped). It exits non-zero if any tag failed.
5. It always quotes `"$REPO_ROOT"`, because the path contains a space (`Karthik Reddy`).
6. `check` = `lint` + `typecheck` + `test` + `docs-check`, in that order, and stops at the first failure.

`scripts/bench-snapshot.sh` rsyncs the repo (excluding `.git`, `results/` and the caches) into `~/kiln-bench/` and prints the path. Benchmarks that measure import or startup time must run from there (ADR-003). The ExperimentRecord captures the filesystem type of the working directory, so a mistake is visible.

### 5.6 `kiln doctor`

**The check list (D1–D19), their severities and fix hints are defined in [spec/07 §12](../spec/07-environment.md#12-kiln-doctor-checks-k0).** K0 implements that table exactly. This section only fixes the code shape:

- `kiln doctor [--json] [--milestone Kn]`. Each check is a function `check_dN(ctx: DoctorContext) -> CheckResult(id, title, status, detail, fix, needed_by)`, where `status ∈ {"pass", "warn", "fail", "info"}`, displayed as ✅ ⚠️ ❌ ℹ️.
- `needed_by` is the milestone from spec/07 §12's severity column. With `--milestone Kn`, every ⚠️ whose `needed_by ≤ Kn` becomes ❌. So before starting a milestone, the learner can ask "am I ready?".
- Exit code 1 if any ❌, else 0.
- `DoctorContext` holds `proc_root: Path = Path("/proc")`, `sys_root: Path = Path("/sys")`, `run: Callable` (subprocess runner), and `env`. Unit tests inject a fake `/proc` tree under `tmp_path` and a fake runner, so every check is tested on any machine (`tests/unit/test_doctor.py`).
- Checks that run subprocesses (`uv python find`, `cargo --version`, `systemd-run`) have a 10 s timeout each. A timeout counts as ⚠️, with the detail "timed out".
- The `--help` text explains each status symbol and the `--milestone` flag.

### 5.7 `kiln.bench.records`

- It implements the **ExperimentRecord** of [spec/10 §2](../spec/10-measurement-methodology.md#2-the-experimentrecord-one-json-file-per-run), field for field, with the field rules of §2.1 and the paths of §2.2: `results/<milestone>/<experiment>/<run_id>.json`. K0 doesn't redefine any of it.
- Public API:
  - `capture_interpreter() -> dict`, `capture_host() -> dict`, `capture_git() -> dict`. These are the `interpreter`, `host` and `git` objects of spec/10.
  - `new_record(milestone, experiment, variant, params, repeat_index) -> dict`. It fills `schema_version`, `run_id`, `started_at` and the captured objects.
  - `add_metric(record, name, unit, *, value=None, samples=None) -> None`. It enforces "exactly one of value or samples", and the unit list.
  - `validate(record) -> list[str]`. It returns every problem found (an empty list means valid). Hand-written checks, no jsonschema (ADR-005).
  - `write_record(record, results_root=Path("results")) -> Path`. It validates first, then writes atomically (`.json.tmp` + `os.replace`).
  - `load_records(path) -> list[dict]`. It refuses an unknown `schema_version` with a clear message.
- `host.notes` must include `source=ext4-snapshot` or `source=9p` (detected from `/proc/mounts` for the cwd), so a benchmark accidentally run on 9P is visible (ADR-003).
- Written with `json.dumps(..., indent=2, sort_keys=True, allow_nan=False)`. A failed measurement is not NaN: it's omitted and explained in `notes`.

### 5.8 Import-boundary test

`tests/unit/test_import_boundaries.py` implements the procedure of [spec/08 §5](../spec/08-repo-layout-and-tooling.md#5-the-import-boundary-test). Here is the **complete allow-table** that the test keeps. Packages that don't exist yet are skipped. Changing the table requires an ADR.

The **core layer** is `kiln.errors` and `kiln.logsetup`. It imports nothing from Kiln, and **every** package below may import it in addition to what's listed. `kiln.protocol` may import `kiln.errors` (spec/01 §5).

| Package | May import (besides itself, the stdlib and the core layer) |
|---|---|
| `kiln.errors`, `kiln.logsetup` | — |
| `kiln.protocol` | — |
| `kiln.config` | — |
| `kiln.app` | — |
| `kiln.memory` | — |
| `kiln.forkcheck` | — |
| `kiln.native` | — |
| `kiln.labs` | `kiln.memory` |
| `kiln.client` | `kiln.protocol`, `kiln.config` |
| `kiln.worker` | `kiln.protocol`, `kiln.config`, `kiln.app`, `kiln.memory`, `kiln.labs` |
| `kiln.launch` | `kiln.protocol`, `kiln.config`, `kiln.memory`, `kiln.worker` (the fork launcher's child branch calls `kiln.worker.main.run_worker`) |
| `kiln.master` | `kiln.protocol`, `kiln.config`, `kiln.app`, `kiln.memory`, `kiln.launch`, `kiln.labs` |
| `kiln.gateway` | `kiln.protocol`, `kiln.config`, `kiln.bench.records` |
| `kiln.bench` | `kiln.protocol`, `kiln.config`, `kiln.client` (`bench.report` may also import `kiln.bench`) |
| `kiln.cli`, `kiln.doctor`, `kiln.__init__`, `kiln.__main__` | anything |

This makes [spec/01 §5](../spec/01-architecture.md#5-package-layout-module-map)'s rule ("master and worker never import each other") executable. The test docstring explains *why* the boundaries exist: they keep the master free of user and worker code, which ADR-006 depends on.

### 5.9 `scripts/check_docs.py`

- **Shape:** `python scripts/check_docs.py [--only CHECK_ID,...] [--milestone KN] [--format text|json]`. It is stdlib-only, so it runs on any interpreter.
- **Output:** one line per problem, `path:line: DOCnnn message (fix: ...)`, then a summary. Exit 0 if clean, 1 if problems were found, 2 on a usage error.
- **Scope:** "code files" means `src/**/*.py`, `labs/**/*.py`, `scripts/*.py`, and `tests/**/*.py` where stated.

| ID | Check ([documentation §10](../standards/documentation.md#10-what-scriptscheck_docspy-checks--summary)) | Precise rule |
|---|---|---|
| DOC001 | Module docstring present | every code file except `tests/**` and empty `__init__.py`. An `__init__.py` with code needs one |
| DOC002 | Module docstring headings | the docstring contains the lines `WHY THIS MODULE EXISTS`, `HOW IT FITS`, `READ FIRST`, `MILESTONE` (exact, uppercase, each on its own line) |
| DOC003 | Function/class docstring present | every `def`, `async def` and `class` in code files (including private ones, per documentation §4.2). Tests: functions named `test_*` |
| DOC004 | `Why:` section | functions whose body (excluding the docstring) spans > 5 source lines, and every class: the docstring contains a line starting with `Why:` |
| DOC005 | Python ledger coverage | every construct in the **construct map** (below) found in code files has a row in `docs/python/README.md` whose `feature` column equals the construct key |
| DOC006 | `CONCEPT(slug)` targets | `docs/concepts/<slug>.md` exists |
| DOC007 | `INCIDENT(id)` targets | `id` appears as a heading anchor in `docs/research/incidents.md` |
| DOC008 | Links | every relative markdown link in `docs/**/*.md` and `README.md`, and every `docs/...md` path mentioned in a code comment or docstring, resolves to an existing file. If it has a `#anchor`, the anchor must exist (GitHub-style slugs, plus explicit `<a id="...">`) |
| DOC009 | Primer sections | every `docs/concepts/*.md` (except `README.md`) has the 9 headings of [documentation §2](../standards/documentation.md#2-concept-primers-docsconcepts) plus `Terms introduced`, matched case-insensitively by heading text |
| DOC010 | Primer answers | each primer contains ≥ 3 `<details>` blocks |
| DOC011 | Snippets exist | every `docs/concepts/snippets/...py` path mentioned in a primer exists. (Running them is `pytest -m snippets`, see `tests/test_snippets.py`) |
| DOC012 | Lab report sections | every `docs/labs/K*.md` has the 13 headings of [documentation §6](../standards/documentation.md#6-lab-reports-docslabskn-slugmd) |
| DOC013 | No empty result cells | in a lab report, a table row under a heading containing "Results" has no empty cell. `not measured (<reason>)` is allowed |
| DOC014 | Glossary coverage | each term listed under `Terms introduced` (a bullet list of `**Term**` items) in any primer or Python note has a `**Term**` entry in `docs/glossary.md` (case-insensitive) |
| DOC015 | Test docstrings | every `test_*` function in `tests/` has a docstring |
| DOC016 | Milestone deliverables | for each milestone whose status in `docs/milestones/README.md` is 🚧, 👀 or ✅ (not 📝), every path in that milestone spec's "Docs deliverables" table exists |
| DOC017 | Python note sections | every `docs/python/*.md` (except README) has the headings of [documentation §3](../standards/documentation.md#3-python-notes-docspython) |

**Construct map** (AST node → ledger key). The keys double as the expected Python-note slugs:

| AST pattern | Ledger key |
|---|---|
| `FunctionDef` | `functions` |
| a decorator on a def or class | `decorators` |
| `ClassDef` | `classes-and-objects` |
| `@dataclass` / `dataclasses.dataclass(...)` | `dataclasses` |
| `With` / `AsyncWith` | `context-managers` |
| `Try`, `Raise` | `exceptions` |
| `ListComp`/`SetComp`/`DictComp`/`GeneratorExp` | `comprehensions` |
| `JoinedStr` (f-string) | `f-strings` |
| `TemplateStr` (3.14+, if the node exists) | `t-strings` |
| annotations (`AnnAssign`, arg or return annotations) | `type-hints` |
| `Import`/`ImportFrom` | `modules-and-imports` |
| class inheriting `Enum`/`IntEnum`/`StrEnum` | `enum` |
| `AsyncFunctionDef`, `Await` | `async-await` |
| `Yield`/`YieldFrom` | `generators` |
| `Lambda` | `lambda` |
| `NamedExpr` | `walrus` |
| `Match` | `match-statement` |
| `Global`/`Nonlocal` | `global-and-nonlocal` |
| `Starred`, or `*args`/`**kwargs` parameters | `star-args-and-unpacking` |
| `Subscript` with a `Slice` | `slicing` |
| `Assert` | `assert` |
| `Delete` | `del` |
| an assignment to `__slots__`, or `slots=True` | `slots` |
| class inheriting `Protocol` | `protocols` |
| PEP 695 `type` statements / type parameters | `generics` |
| a def named `__xxx__` | `dunder-methods` |
| a nested `FunctionDef` | `closures` |

`check_docs.py` itself is heavily commented, and `tests/test_check_docs.py` has one good and one bad fixture per check ID.

### 5.10 Incident registry and errata

- `docs/research/incidents.md` has one `##` heading per incident id, each with a 2–3 line summary and a link to research §2.x. These are the ids `INCIDENT(<id>)` tags may use:
  - `instagram-cow` (§2.1)
  - `gc-revert-314` (§2.2)
  - `dropbox-typecheck` (§2.3)
  - `pyrefly` (§2.4)
  - `lazy-imports` (§2.5)
  - `blocked-loop` (§2.6)
  - `celery-oom` (§2.7)
  - `snyk-leak` (§2.8)
  - `forkserver-default` (§2.9)
  - `free-threading` (§2.9)
  - `rust-hot-path` (§2.10)
  - `ultralytics` (§2.11)
  - `language-changes` (§2.12)
- `docs/research/errata.md` already exists from the spec pass (entries `E-nn`). K0 doesn't recreate it. K0 only updates the status of entries its gates settle (for example E-13, interpreter availability).

## 6. Steps

| # | Goal | Docs first | Tests | Code | Done when |
|---|---|---|---|---|---|
| 0.1 | **System setup** | primer `concepts/terminal-and-wsl.md`, `concepts/git-basics.md` | — | run apt and uv installs (§5.1, §5.2) | gates G0.1–G0.2 recorded; the setup log section is started |
| 0.2 | **Starter Python notes** (before any code) | all 18 Python notes in §10, plus `docs/python/README.md` (ledger, initially empty of rows) | snippets for each note in `docs/python/snippets/` (where useful) | — | notes complete with real REPL output |
| 0.3 | **Project skeleton** | primer `concepts/what-is-a-process.md` (for `python -m`) | `tests/unit/test_cli_smoke.py`: `kiln --version` on the current interpreter | pyproject, uv.lock, `src/kiln/__init__.py`, `__main__.py`, `cli.py` (argparse + `--version`), `.gitattributes`, `.gitignore`, `.python-version` | `uv run kiln --version` prints `kiln 0.1.0` |
| 0.4 | **dev.sh + snapshot script** | — (heavily commented script) | manual run recorded in the setup log | `scripts/dev.sh`, `scripts/bench-snapshot.sh` | `./scripts/dev.sh test-matrix` runs on all 4 tags |
| 0.5 | **check_docs.py** | — (the module docstring explains the doc system) | `tests/unit/test_check_docs.py` + fixtures, one good and one bad per DOC ID | `scripts/check_docs.py` | `dev.sh docs-check` passes on the repo |
| 0.6 | **kiln doctor** | primer `concepts/files-and-file-descriptors.md` (doctor reads `/proc` files) | `tests/unit/test_doctor.py` with a fake `/proc` and `/sys` tree (§5.6 `DoctorContext`) | `src/kiln/doctor.py`, `cli.py doctor` | `kiln doctor` shows ✅ for D1, D3, D7 and D16, and no ❌ |
| 0.7 | **Records + import boundaries + remaining docs** | primer `concepts/what-is-a-server.md`; glossary, faq, questions, concepts index, incidents, errata; tour K0 | `tests/unit/test_records.py` (round-trip, atomic write, validation failure cases), `tests/unit/test_import_boundaries.py`, `tests/snippets/test_snippets.py` | `src/kiln/bench/records.py` | all AC pass |

## 7. Lab design

K0 has **no lab report**: it reproduces no incident. Its "experiment" is the setup itself, recorded in **`docs/tours/K0.md` → "Appendix: setup log"**. For each command run, the log records the command, the date, the (trimmed) real output, and any error with how it was fixed. The log is a teaching artifact: it shows how a real environment gets built, mistakes included.

- **Question:** can this machine run every later milestone?
- **Hypothesis:** yes, except for Rust (K8, D10) and `ptrace_scope` (K5/K6, D11), which are expected ⚠️s.
- **Procedure:** `./scripts/dev.sh doctor --json > results/published/K0/doctor.json`, plus `kiln doctor --milestone K10` text output pasted into the log (it shows everything any milestone will need).
- **Result table shape:** `check id | status | detail` (copied from the real output).

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check (command) | If false, fallback |
|---|---|---|---|
| G0.1 | uv can install **exactly** 3.14.4, 3.14.5 and 3.15t | `uv python list --all-versions \| grep -E '3\.14\.(4\|5)\|3\.15.*freethreaded'` then `uv python install ...` | use the closest available patch versions. Write an ADR that updates ADR-004, and a note in K7's spec; K7 needs one build *with* and one *without* the incremental GC (see K7 gates) |
| G0.2 | apt package names on Debian 13 | `apt-cache policy build-essential gdb rsync` | look up the replacements and record them in the setup log |
| G0.3 | `uv_build` backend works with the src layout | `uv build` produces a wheel | switch to `hatchling` and record the ADR |
| G0.4 | ruff accepts `target-version = "py313"` and parses 3.15 syntax in files | `ruff check` on a file with a 3.15-only construct inside a gated module | add a per-file `# ruff: noqa` with WHY, or exclude gated modules from that rule |
| G0.5 | mypy installs and runs under 3.15 | `KILN_PY=3.15 ./scripts/dev.sh typecheck` | run mypy with the 3.14 interpreter (`--python-version 3.13` still analyses at the floor). Record it |
| G0.6 | pytest, hypothesis and pytest-timeout install on 3.15t (free-threaded wheels or pure Python) | `KILN_PY=3.15t ./scripts/dev.sh test` | mark the failing plugin and skip it on 3.15t with a reason; record it |
| G0.7 | `/proc/1/comm` on this WSL is `systemd` (affects the K1 OOM lab) | `cat /proc/1/comm` | doctor D13 ⚠️; K1 uses the manual cgroup fallback (spec/07 §6.2) |
| G0.8 | PEP 735 `[dependency-groups]` is supported by the installed uv | `uv sync --group dev` | upgrade uv (`uv self update`); otherwise use `[project.optional-dependencies]` plus an ADR |

## 9. Acceptance criteria

- **AC-0.1** `kiln --version` prints `kiln 0.1.0` on 3.13, 3.14, 3.15 and 3.15t (`tests/unit/test_cli_smoke.py`, run by `dev.sh test-matrix`).
- **AC-0.2** `./scripts/dev.sh check` is green on 3.15.
- **AC-0.3** `./scripts/dev.sh test-matrix` is green (it prints a 4-row summary table).
- **AC-0.4** `kiln doctor` exits 0: no ❌, and every ⚠️ has a `fix` text and a `needed_by` milestone. The output is in the setup log.
- **AC-0.5** `kiln doctor --milestone K1` lists exactly what's still missing for K1, or nothing, and exits accordingly.
- **AC-0.6** `check_docs.py` implements DOC001–DOC017. `tests/unit/test_check_docs.py` proves that each one catches its bad fixture and accepts its good fixture.
- **AC-0.7** `write_record` → `load_records` round-trips. `validate` rejects a record with a missing required field, a metric with both `value` and `samples`, and an unknown unit (`tests/unit/test_records.py`).
- **AC-0.8** The import-boundary test exists, passes, and fails on a planted bad import in a fixture (tested by pointing it at `tests/fixtures/boundaries_bad/`).
- **AC-0.9** Every docs deliverable in §10 exists and passes DOC009/DOC010/DOC014/DOC017.
- **AC-0.10** The venv lives under `~/.cache/kiln/venvs/` (doctor D7 ✅), and `.gitattributes` enforces LF (doctor D16 ✅).
- **AC-0.11** The learner can follow `README.md` "Quick start" from a fresh WSL shell and reach a green `dev.sh check`. 👤

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/terminal-and-wsl.md` | what a shell is; WSL vs Windows; `/mnt/c`; quoting paths with spaces; `$?`; running `dev.sh` from PowerShell; 9P vs ext4 and why venvs live in `~/.cache` |
| Concept | `docs/concepts/git-basics.md` | commit, branch, diff, the milestone branch flow (ADR-025), line endings (LF vs CRLF) |
| Concept | `docs/concepts/what-is-a-process.md` | program vs process; pid; memory isolation; parent/child; `ps`, `/proc/<pid>`; exit codes (an intro; K1 goes deeper) |
| Concept | `docs/concepts/files-and-file-descriptors.md` | what an fd number is; stdin/stdout/stderr = 0/1/2; "everything is a file"; `/proc/self/fd`; `open()` returning an object that wraps an fd |
| Concept | `docs/concepts/what-is-a-server.md` | client/server; ports; localhost; request/response; why a server runs "forever"; where Kiln fits (with the spec/01 diagram) |
| Python | `docs/python/running-python-and-the-repl.md` | `python`, `python -m`, the REPL, scripts vs modules, `uv run` |
| Python | `docs/python/variables-and-types.md` | names vs objects, `int/float/str/bool/None/list/dict/tuple/set`, mutability, `is` vs `==`, `type()` |
| Python | `docs/python/functions.md` | `def`, parameters, defaults (the mutable-default gotcha), `*args/**kwargs`, return, keyword-only args |
| Python | `docs/python/modules-and-imports.md` | `import x`, `from x import y`, packages, `__init__.py`, `sys.path` intro, src layout |
| Python | `docs/python/classes-and-objects.md` | `class`, `self`, `__init__`, attributes, methods, inheritance, `super()` intro |
| Python | `docs/python/dataclasses.md` | `@dataclass`, fields, defaults, `field(default_factory=...)`, `__repr__`/`__eq__` generated. (K1 adds `slots`/`frozen`) |
| Python | `docs/python/type-hints.md` | annotations, `list[int]`, `X \| None`, why hints don't change runtime, what mypy does |
| Python | `docs/python/exceptions.md` | `try/except/else/finally`, `raise`, custom exceptions, tracebacks, `raise ... from ...` |
| Python | `docs/python/context-managers.md` | `with`, `__enter__/__exit__`, files, `contextlib.contextmanager` |
| Python | `docs/python/decorators.md` | functions as values, wrapping, `@`, decorators with arguments (sets up `@app.task` in K1), `functools.wraps` |
| Python | `docs/python/bytes-vs-str.md` | text vs bytes, UTF-8, `.encode()/.decode()`, `bytearray`, why sockets carry bytes |
| Python | `docs/python/f-strings.md` | `f"{x}"`, format specs, `{x!r}`, `{x=}` |
| Python | `docs/python/comprehensions.md` | list, dict and set comprehensions, generator expressions, when *not* to use them |
| Python | `docs/python/enum.md` | `Enum`, `IntEnum`, `StrEnum`, why enums beat magic strings |
| Python | `docs/python/pathlib.md` | `Path`, `/` joining, `read_text`, why not string paths |
| Python | `docs/python/logging-basics.md` | loggers, levels, handlers, `basicConfig`, why not `print` (links to spec/06) |
| Python | `docs/python/pytest-basics.md` | test functions, `assert`, fixtures, `tmp_path`, markers, `-k`, `-x`, reading a failure |
| Python | `docs/python/argparse.md` | parsers, subcommands, `--help` text, exit code 2 |
| Index | `docs/python/README.md` | the ledger table (documentation §3) plus a suggested reading order |
| Index | `docs/concepts/README.md` | the primer index: slug, title, milestone, status, reading order |
| Glossary | `docs/glossary.md` | seeded with every term introduced by the K0 docs, plus the spec-pass terms (job, task, worker, master, queue, etc.) |
| FAQ | `docs/faq.md` | seed: "Why WSL?", "Why not just use Celery/gunicorn?", "Why so many comments?", "What's a milestone?", "Why several Python versions?" |
| Questions | `docs/questions.md` | the format explanation and an empty table `# | date | question | status | answered in` |
| Registry | `docs/research/incidents.md` | §5.10 |
| Errata | `docs/research/errata.md` (update only) | statuses of the entries K0's gates verify |
| Tour | `docs/tours/K0.md` | a tour of the skeleton, `dev.sh`, `check_docs.py`, `doctor.py`, `records.py`, plus **Appendix: setup log** |

## 11. Reference comparison assignment

After K0 works, add to `docs/tours/K0.md` a "Reference comparison" section:
- Compare our `pyproject.toml` with `pytest-dev/pytest`'s. Which tool configs do they keep in `pyproject.toml` and which in other files? Why?
- Compare `check_docs.py` with pydocstyle/Ruff's `D` rules. What does Ruff already cover (DOC003 partially), and what can only our checker do (ledger, primers, labs)?
- Compare `kiln doctor` with `flutter doctor` or `brew doctor` (the inspiration). What makes a doctor-style tool's output useful?

## 12. Risks and pitfalls

| Risk | Mitigation |
|---|---|
| CRLF line endings break `dev.sh` (`/usr/bin/env: 'bash\r'`) | `.gitattributes` + doctor D16; mention it in the terminal primer |
| The space in `/mnt/c/Users/Karthik Reddy/...` breaks unquoted shell variables | quote every variable in `dev.sh`; add a test that runs `dev.sh lint` from a path with a space (the repo itself) |
| The venv accidentally created on `/mnt/c` (`.venv` in the repo) | `UV_PROJECT_ENVIRONMENT` is always exported by `dev.sh`; doctor D7 |
| The Python notes drift into a full Python course | each note ≤ 1,500 words, covering what *Kiln* uses; link to the official tutorial for more |
| `check_docs.py` too strict too early (it fails on files that don't exist yet) | DOC016 only checks milestones whose status is not 📝 |
| Tools without 3.15t wheels | gate G0.6 |

## 13. Checkpoint quiz (seed questions)

1. Why can't Kiln run on Windows directly? Name two things it needs that Windows lacks.
2. What is the difference between `/mnt/c/...` and `~/` inside WSL, and why does the venv live in `~/.cache`?
3. What does `uv sync --group dev` do?
4. In `def f(x, items=[]):`, what's the bug waiting to happen?
5. What's the difference between `"héllo"` and `"héllo".encode()`? How many bytes is the second one?
6. What does `with open("f") as fh:` guarantee that `fh = open("f")` doesn't?
7. What does a decorator receive, and what does it return?
8. What does `kiln doctor --milestone K4` do differently from `kiln doctor`?
9. What are file descriptors 0, 1 and 2?
10. Why does `check_docs.py` exist? What would go wrong in this project without it?

## 14. Stretch / later

- A `pre-commit`-style git hook that runs `dev.sh lint` (opt-in; ask the learner first).
- `kiln doctor --fix` for safe automatic fixes (no sudo).
