# K8 — Rust Hot Path: Profile First, Then Cross the Language Boundary (PyO3)

> **Status:** 📝 specified · **Depends on:** K1 (KP/1 submit validation in `protocol/messages.py`, `kiln bench load`), K4 (gilwatch, the `c-extensions` primer), K5 (gateway JSON handling), K6 (the Tachyon and `sampling-vs-tracing-profilers` primers) · **Branch:** `milestone/k8-rust` · **Size:** M (6 steps)
> **Incident:** research §2.10 (Pydantic v2 moved its core to Rust and reports 5–50× speedups. The "Tea and Bits" case went from 293 ms to 2.9 ms per iteration after profiling with `py-spy --native` and rewriting one hot function. *The catch:* many small calls across the boundary cancel out the speedup)
> **Reference implementation (read *after* building):** [pydantic-core](https://github.com/pydantic/pydantic-core) (validators in Rust via PyO3), and the [PyO3 guide](https://pyo3.rs) sections on free-threading and the stable ABI

---

## 1. The story (plain English)

Python is slow at some things: tight loops, lots of tiny operations, walking big data structures. Languages like **Rust** and C compile to machine code and can be 10–100× faster at those jobs. So a common move is to rewrite the *one* slowest function in Rust and call it from Python. Pydantic did this with its whole validation core, and got speedups of 5–50×.

There are two traps, and this milestone is about both.

**Trap 1: optimizing the wrong thing.** If a function takes 3% of your program's time, making it infinitely fast saves 3%. That's **Amdahl's law**, which you met in K4. The engineers in research §2.10 *profiled first*. They measured where the time actually went, and only then rewrote the hot spot. Most "let's rewrite it in Rust" projects skip this step.

**Trap 2: the toll booth.** Every call from Python into Rust passes through a "toll booth". The arguments get converted, the interpreter state is checked, and the result is turned back into Python objects. If you call a Rust function a million times to do a tiny thing each time, you pay the toll a million times, and that can cost more than the work itself. Fast native code wants **few, fat calls**, not many thin ones.

**In this milestone we will:**
1. Profile Kiln's master and gateway under load, honestly, and find out whether validating job submissions is even hot. If it isn't, that's a valid result, and we'll say so.
2. Write a careful pure-Python validator as the reference.
3. Build a Rust version with **PyO3** and **maturin** that declares itself safe for free-threaded Python.
4. Prove that the two agree, using property-based tests.
5. Measure the toll booth precisely: tiny calls vs batched calls, bytes vs strings in, booleans vs Python objects out.

## 2. What you will learn

- **Explain** why you profile before optimizing, and **compute** the maximum benefit of speeding up a function from its profile share (Amdahl). *(Memory & perf)*
- **Read** a sampling flamegraph of a real server and attribute time to functions. *(Memory & perf)*
- **Explain** what the CPython C API is (`PyObject*`, reference counts, module init) at the level needed to understand what PyO3 does for you. *(Native code)*
- **Explain** the cost of crossing the Python↔native boundary (argument conversion, object creation, GIL attach/detach) and **predict** when batching wins. *(Native code)*
- **Explain** wheels, ABI tags, `abi3` (one binary for many Python versions), why `abi3` doesn't cover free-threaded builds, and what `abi3t` (PEP 803) changes. *(Native code, Packaging)*
- **Explain** how a native module declares free-threading support, and why it must actually be thread-safe when it does. *(Native code, Concurrency)*
- **Use** property-based testing (Hypothesis) to show that two implementations are equivalent, and **explain** why edge cases (big integers, NaN, lone surrogates, `bool` vs `int`) are where they differ. *(Testing)*

## 3. Entry criteria

- K1: the `submit` validation rules are implemented in `src/kiln/protocol/messages.py` with tests, and `kiln bench load` supports `--args/--kwargs`.
- K4: `concepts/c-extensions.md` and `worker/gilwatch.py` exist.
- K6: `concepts/sampling-vs-tracing-profilers.md` and `concepts/flamegraphs.md` exist, and the Tachyon gate (G6.3) has recorded the real CLI.
- The Rust toolchain is installed per spec/07 §9 (`kiln doctor --milestone K8` passes the Rust checks).

## 4. Scope

### In scope
- **Step 1, profile first:** the master and the gateway under load, with Tachyon (sampling) and cProfile/`profiling.tracing` (tracing), followed by a written decision.
- `src/kiln/native/validate.py`: the validator **facade** (§5.3). The pure-Python reference implementation of the K8 API (§5.2) is K1's `protocol/messages.py`, refactored.
- `rust/kiln_native/`: a PyO3 crate built by maturin into the module `kiln_native` (the package `kiln-native` in the `lab-k8` group; spec/08 §2.2).
- The config key `protocol.validator = "python" | "native"`.
- Equivalence tests (Hypothesis), boundary microbenchmarks (`labs/k8/boundary_bench.py`), and the end-to-end load comparison.
- Builds for 3.13, 3.15 and 3.15t.

### Out of scope (and why)
- Rewriting framing, the event loop, or JSON *encoding* in Rust. We optimize one thing, measured, as the research method demands.
- Publishing wheels to PyPI. Packaging and publishing belong to Cartographer C8.
- `unsafe` Rust and the raw C API. PyO3's safe API is the lesson, and the C API is explained conceptually in the primer.

## 5. Design

### 5.1 Step 1: profile first (the decision record)

- **Master:**
  1. Start `kiln serve examples.demo:app --labs --workers 4` from the ext4 snapshot.
  2. Run `kiln bench load --jobs 50000 --concurrency 64 --task labs.echo --kwargs '{"value": <a 2 KiB nested JSON document>}'`, so payload validation has some real work.
  3. Attach Tachyon to the **master** pid using the exact command recorded by G6.3, with ptrace per spec/07 §5. Capture a flamegraph and a collapsed-stacks or pstats file.
  4. Compute the **share of master samples** spent in: (a) `protocol/messages.py` submit validation, (b) `json.loads`/`json.dumps`, (c) framing (`protocol/framing.py`), (d) selector/syscall frames, (e) everything else.
- **Gateway:** the same, under `labs/k5/http_load.py` at concurrency 64 against `POST /v1/jobs`, attributing (a) HTTP parsing, (b) `json.loads` of the body, (c) validation, (d) the KP client.
- **Tracing cross-check:** `python -m cProfile -o results/K8/profile/master.prof -m kiln serve ...`, or `profiling.tracing` on 3.15 (gate G8.9). Then compare its percentages with Tachyon's. The primer explains why tracing overhead inflates the share of functions that make many calls.
- **Decision rule** (written into the lab report *before* any Rust is written):
  - Let V be the validation share and J the share of JSON decode + validation combined.
  - The report states `max speedup if validation were free = 1/(1−V)`, and the same for J.
  - **Target choice:** if V ≥ 10%, target `validate_submit`. Else if J ≥ 10%, target `parse_and_validate_submit` (decode + validate in Rust). Else neither is hot. **We still build both** (that's the learning objective), but the report says plainly that end-to-end Kiln throughput isn't expected to change measurably, and the end-to-end measurement (§7 run E) is there to confirm it.
  - The 10% threshold is fixed here, before measuring, so the decision can't be bent to fit the result.

### 5.2 The K8 API (identical in Python and Rust)

| Function | Signature | Semantics |
|---|---|---|
| `validate_submit` | `(msg: dict) -> None` | raises `ValidationError(field: str, message: str)` on the first violation, checking fields **in this order**: `task`, `args`, `kwargs`, `timeout_s`, `max_attempts`. Rules: `task` is a `str` matching `^[A-Za-z_][A-Za-z0-9_.]{0,127}$` (spec/03 §2.1); `args` is absent or a `list`; `kwargs` is absent or a `dict` whose keys are all `str`; `timeout_s` is absent or an `int`/`float` (**not** `bool`), finite and > 0; `max_attempts` is absent or an `int` (**not** `bool`) ≥ 1. Unknown fields are ignored (spec/02 §2). These are exactly K1's `messages.py` rules. Step 2 makes `messages.py` expose them as this API (see below) |
| `parse_and_validate_submit` | `(raw: bytes) -> dict` | decodes `raw` as UTF-8 JSON that must be an object, then rejects NaN/Infinity (spec/02 §2), then runs `validate_submit`. Returns the dict. Raises `FrameDecodeError(message)` for bytes that aren't valid JSON or aren't an object (maps to `BAD_FRAME`), or `ValidationError` |
| `validate_many` | `(msgs: list[dict]) -> list[tuple[str, str] \| None]` | for each message: `None` if valid, else `(field, message)`. Never raises for invalid messages, which makes it the "batched call" |
| `is_valid_task_name` | `(name: str) -> bool` | the regex check alone: the "tiny call" |
| `noop` | `() -> None` | does nothing. It measures pure call overhead |

- **The Python reference *is* K1's validation code.** Step 2 refactors `src/kiln/protocol/messages.py` so that it exposes exactly the §5.2 functions plus a `MESSAGES` dict of error-message constants. That leaves one source of truth for the protocol rules, with no second copy to drift.
- **Error messages** must be **identical strings** in both implementations, because the equivalence test compares them. The Rust crate repeats them in `src/messages.rs` and exports them as `kiln_native.MESSAGES`. A test asserts `kiln_native.MESSAGES == kiln.protocol.messages.MESSAGES`.
- `ValidationError` and `FrameDecodeError` are defined in `kiln/errors.py` (the core layer, spec/01 §5), subclassing `KilnError`. The Rust module **imports and raises these same Python classes** (`py.import("kiln.errors")?.getattr("ValidationError")`), so callers catch one type whichever implementation is active.

### 5.3 The facade and the config switch

- New config key: **`protocol.validator`**, type `"python" | "native"`, default `"python"`. Step 2 adds it to spec/05 §2 `[protocol]`.
- `kiln.native.validate.get_validator(choice: str) -> ValidatorModule` returns an object with the §5.2 functions. For `"python"` that's `kiln.protocol.messages`. For `"native"`, it does `import kiln_native`. If that import fails, it raises a config error: `protocol.validator = "native" but the kiln_native extension is not installed for this interpreter (<version>). Build it with: KILN_PY=<v> ./scripts/dev.sh sync --group lab-k8`. Exit code 2. **There's no silent fallback**, because benchmarks must never quietly measure the wrong implementation.
- **Where it's used:**
  - the master's submit handler calls `validator.validate_submit(msg)` after frame decoding
  - the gateway's `POST /v1/jobs` calls `validator.parse_and_validate_submit(body)` instead of `json.loads` + validation
- **Dependency rule amendment.** spec/01 §5's rule lists what `master` and `gateway` may import, and `native` isn't in that list. Step 2 amends spec/01: *"`native` imports only the core layer (`protocol`, `errors`). `master` and `gateway` may import `native`."* `protocol` stays in the core layer and never imports `native`. The import-linter test (K0) gets updated to match.

### 5.4 The Rust crate (`rust/kiln_native/`)

| File | Content |
|---|---|
| `Cargo.toml` | `[lib] name = "kiln_native"`, `crate-type = ["cdylib"]`. Dependencies: `pyo3` (version per G8.2; features `extension-module`, plus `abi3-py313` for GIL builds only, which is set from maturin features, see below), `serde_json` (features `arbitrary_precision`, per G8.6), `regex` (or a hand-written matcher, since the pattern is simple; the README explains the choice). Every line is commented |
| `pyproject.toml` | `[build-system] requires = ["maturin>=X,<Y"]`, `build-backend = "maturin"`, `[project] name = "kiln-native"`, `[tool.maturin] features = [...]`, `module-name = "kiln_native"` |
| `src/lib.rs` | `#[pymodule(gil_used = false)]` (G8.3) declaring the module and adding the §5.2 functions and `MESSAGES` |
| `src/validate.rs` | the rules, operating on `&Bound<'_, PyDict>` (walks Python objects) |
| `src/parse.rs` | `serde_json::from_slice::<Value>` → reject non-objects → reject non-finite floats → convert `Value` → Python objects (big integers via the `arbitrary_precision` string → Python `int(str)`) → `validate` |
| `src/messages.rs` | the error-message constants |
| `README.md` | **a line-by-line walkthrough for someone who has never seen Rust**: `fn`, `let`, `&`, `Result`/`?`, `match`, lifetimes `'py` at an intuition level, PyO3's `Bound`, `#[pyfunction]`, `#[pymodule]`. It links to `docs/concepts/pyo3-and-maturin.md` |

- **Thread safety under `gil_used = false`:** the functions keep no Rust global mutable state (the compiled regex sits in a `std::sync::OnceLock`, which is thread-safe). The README explains why that makes the declaration honest. Walking a `PyDict` that another thread mutates at the same moment is the *caller's* problem, as it is in pure Python, and the primer says so.
- **Builds** (`scripts/dev.sh sync --group lab-k8` per interpreter; uv builds the local path package with maturin):

| Interpreter | Wheel kind | Notes |
|---|---|---|
| 3.13, 3.15 (GIL) | **abi3** (`abi3-py313`), so **one** `.so` tagged `abi3` | the lab shows that the *same file* imports on 3.13 and 3.15: compare the sha256 of the installed `.so` across both venvs |
| 3.15t | **version-specific** `cp315t`, or `abi3t` if G8.4 passes | abi3 doesn't apply to free-threaded builds before PEP 803's `abi3t` |
| 3.14 | abi3 (the same wheel) | tested in `test-matrix`, so it's covered |

- If an interpreter newer than PyO3's officially supported maximum fails to build, set `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` for abi3 builds (G8.2 fallback), and document it.

### 5.5 Equivalence testing (`tests/unit/test_native_equivalence.py`)

- Marked `requires_native`, a new marker that skips with a reason if `kiln_native` isn't importable. Step 4 adds the marker to spec/09 §8's marker list.
- **Hypothesis strategies:**
  - `json_values()`: recursive JSON (None, bools, ints including > 2⁶⁴, floats including ±0.0, strings with arbitrary Unicode, lists, dicts)
  - `submit_messages()`: mostly-valid messages with random mutations (wrong type, `True` where an int is expected, a bad task name, a negative timeout, missing fields, extra fields)
  - `raw_frames()`: `json.dumps(submit_messages())` encoded, plus corrupted bytes, NaN/Infinity literals, top-level arrays, invalid UTF-8
- **Properties:**
  1. For every message, both `validate_submit` implementations raise the same `(field, message)` or both pass.
  2. For every raw frame, `parse_and_validate_submit` gives an equal dict (`==`), the same `ValidationError`, or both raise `FrameDecodeError`.
  3. `validate_many(xs)` equals `[single(x) for x in xs]` in both implementations.
- **Known divergences:**
  - Lone surrogate escapes (`"\ud800"`): Python's `json` accepts them, and `serde_json` rejects them.
  - Duplicate keys: both keep the last value (verify this).
  - Integer forms like `1.0` vs `1` (both produce a float, verify this).
  - **Rule:** every divergence that Hypothesis finds is either **fixed**, by making the Python reference stricter when the spec allows it, which needs an ADR if it changes KP behaviour, or **listed** in the lab report's "Known divergences" table with a justification. The generator then excludes it with a commented `assume(...)`. A divergence that's silently filtered out is a bug.
- **Thread-safety smoke test** on 3.15t: 8 threads call `kiln_native.validate_many` on shared inputs for 2 s. There must be no crash, results must equal the single-threaded results, and gilwatch must report nothing (the GIL stays off).

### 5.6 Boundary microbenchmarks (`labs/k8/boundary_bench.py`, lab id `k8-boundary`)

Timing method:
- `time.perf_counter_ns()` around an inner loop of `L` calls, with `L` chosen per benchmark so that one sample is ≥ 10 ms.
- Results are reported as **ns per call**.
- 3 warm-ups and ≥ 10 repeats per spec/10. The median, the CI and the min ("best achievable", allowed for microbenchmarks by spec/10 §4.1) are all reported.

| ID | What | Python side | Native side | Question |
|---|---|---|---|---|
| B1 | pure call overhead | `def noop(): pass` | `kiln_native.noop()` | what does one crossing cost? |
| B2 | tiny call | `re.fullmatch` reference `is_valid_task_name` | native `is_valid_task_name` | does Rust win when the work is tiny? |
| B3 | batching | `validate_submit` per message, N calls | `validate_many([...N])`, 1 call; N ∈ {1, 10, 100, 1000} | where's the crossover? |
| B4 | input type | `json.loads(raw)` + `validate_submit` | `parse_and_validate_submit(raw)`; also a `str` input variant paying `.encode()` first | does doing the decode natively pay? |
| B5 | output cost | — | `parse_and_validate_submit` (builds a dict) vs a variant `parse_and_check_submit(raw) -> bool` (no objects built; a bench-only function exported under `kiln_native._bench`) | how much of the native time is *building Python objects*? |

Payloads: three fixed submit documents (`small` ≈ 100 B, `medium` ≈ 2 KiB, `large` ≈ 64 KiB nested), generated deterministically from a seed and stored in `labs/k8/payloads/`.

### 5.7 End-to-end run

`kiln bench load --jobs 50000 --concurrency 64 --task labs.echo --kwargs <medium payload>` with `KILN_PROTOCOL__VALIDATOR=python` vs `native`. Throughput, p99 and master CPU time (spec/10 §8) are recorded. Then the gateway, via `labs/k5/http_load.py`, with the same switch.

## 6. Steps

1. **Profile first + decision record (§5.1).**
   - *Docs first:* `concepts/profiling-before-optimizing.md`, `python/cprofile-and-profiling-tracing.md`.
   - *Gates:* G8.8, G8.9.
   - *Deliverable:* the "Decision" section of the lab report, with flamegraphs (as SVG/HTML files in `results/published/K8/profile/`) and the share table. **No Rust is written before this is committed.**
2. **Refactor `protocol/messages.py` into the §5.2 reference API + facade + config key + spec amendments (§5.2, §5.3).**
   - *Tests:* every rule in §5.2, including the bool-is-an-int gotcha (`True` as `max_attempts` → `ValidationError("max_attempts", ...)`); K1's protocol tests still pass; `protocol.validator="native"` without the extension → exit 2 with the exact message.
3. **Rust toolchain + crate skeleton with `noop` + builds on 3.13/3.15/3.15t.**
   - *Docs:* `concepts/c-api-basics.md`, `concepts/pyo3-and-maturin.md`, `concepts/stable-abi-abi3-abi3t.md`, `python/importing-compiled-modules.md`, `rust/kiln_native/README.md` (started).
   - *Gates:* G8.1–G8.5.
   - *Tests:* `kiln_native.noop()` imports and runs on each interpreter; the abi3 `.so` has the same sha256 in the 3.13 and 3.15 venvs; on 3.15t, `sys._is_gil_enabled()` is `False` after the import.
4. **Rust validators + equivalence tests + known divergences (§5.4, §5.5).**
   - *Gate:* G8.6.
   - *Docs:* finish the README.
5. **Microbenchmarks + end-to-end (§5.6, §5.7).**
   - *Docs:* `concepts/ffi-boundary-cost.md`, `python/timeit-and-perf-counter.md`.
6. **Lab report, code tour, reference comparison.**

## 7. Lab design

- **Question:** where does Kiln's master and gateway time actually go? For the validation path, how much faster is Rust per call, how does that change with call granularity, input type and output construction, and does any of it move end-to-end throughput?
- **Implementer's hypothesis:**
  - Validation is a small share of master time. Syscalls, framing and JSON dominate, so the end-to-end gain from `validate_submit` alone is within noise.
  - `parse_and_validate_submit` beats `json.loads`+validate by a modest factor on medium and large payloads, but much of the native time goes to building Python objects (B5).
  - B1 shows native call overhead of the same order as a Python function call.
  - B2 shows Rust barely winning, or even losing, for tiny work.
  - B3 shows batching amortizing the crossing, with the advantage growing with N until it plateaus.
  - 3.15t numbers differ a bit (no GIL attach cost, other overheads). We measure and don't guess.
- **Independent variables:**
  - implementation ∈ {python, native}
  - benchmark ∈ {B1…B5}
  - payload ∈ {small, medium, large}
  - N (B3) ∈ {1, 10, 100, 1000}
  - interpreter ∈ {3.13, 3.15, 3.15t}
  - end-to-end component ∈ {master, gateway}
- **Dependent variables:**
  - ns/call (median [CI], min)
  - speedup ratio native/python [CI]
  - end-to-end jobs/s and req/s, p99, master CPU seconds per 1,000 jobs
  - profile shares (%)
- **Controls:**
  - ext4 snapshot
  - CPU pinning for microbenchmarks (`taskset -c 2`, single-threaded; `host.pinned_cpus` recorded)
  - release builds (`maturin develop --release`, or uv building in release mode, verified by G8.5)
  - the same payload files
  - `PYTHONHASHSEED=0` for the microbenchmarks
- **Repeats and statistics:** per [spec/10](../spec/10-measurement-methodology.md): 3 warm-ups, ≥ 10 repeats, round-robin interleaving of implementations, ratio-of-medians CIs with the §4.3 interpretation rules. The practical-significance threshold for end-to-end runs is set in the Method section beforehand.
- **Procedure:** `./scripts/dev.sh lab k8-profile` (§5.1), then `KILN_PY=<v> ./scripts/dev.sh lab k8-boundary` for each interpreter (§5.6), then `./scripts/dev.sh lab k8-e2e` (§5.7).
- **Result tables** (headers only):
  - *Profile shares:* `component | validation % | JSON % | framing % | syscalls/selector % | other % | max speedup if validation free | source (Tachyon/cProfile)`
  - *B1–B2:* `bench | interp | python ns/call [CI] | native ns/call [CI] | ratio [CI]`
  - *B3:* `N | interp | python ns/msg | native ns/msg | ratio [CI]`
  - *B4/B5:* `payload | interp | variant | ns/call [CI] | ratio vs python [CI] | object-building share (B5)`
  - *End-to-end:* `component | interp | validator | throughput [CI] | p99 ms | master CPU s/1k jobs | ratio [CI]`
  - *Known divergences:* `input | python result | native result | resolution (fixed / justified)`
  - *Builds:* `interp | wheel tag | .so sha256 | gil_used=false honoured?`

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G8.1 | the Rust toolchain installs in WSL and links with `cc` | `rustc --version && cargo --version` (spec/07 §9) | install `build-essential` (spec/07 §2) and retry |
| G8.2 | the current PyO3 release supports building for CPython 3.15 and 3.15t | build `noop` for each | abi3 builds: set `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1`. 3.15t: if PyO3 can't target it yet, mark the 3.15t rows `not measured` with the PyO3 version and the error, and add an errata entry |
| G8.3 | `#[pymodule(gil_used = false)]` is the current syntax for declaring free-threading support, and it keeps the GIL off on import | compile + import on 3.15t + `sys._is_gil_enabled()` + gilwatch | use the syntax from the current PyO3 guide, and update the primer and the README |
| G8.4 | PyO3/maturin can build `abi3t` wheels for 3.15t (PEP 803) | try the feature or flag named in the current PyO3/maturin docs | version-specific `cp315t` build (the default plan). The primer explains abi3t conceptually and states its support status as observed on the gate date |
| G8.5 | uv can build and install the local maturin project into the `$HOME/.cache/kiln/venvs/py<v>` venv from a repo path containing a **space**, in release mode | `KILN_PY=3.15 ./scripts/dev.sh sync --group lab-k8`, then check `kiln_native.__file__` and that the build profile is release (maturin output) | build from the ext4 snapshot (no space in the path), or `maturin build --release` then `uv pip install <wheel>`; document it |
| G8.6 | `serde_json` with `arbitrary_precision` lets us build exact Python ints for huge JSON integers | an equivalence test with 10³⁰ | parse ints > i64 by re-parsing the number's text in Python (`int(s)`) via PyO3, documented |
| G8.7 | the PyO3 API names used (`Bound`, `PyDict`, `import`, exceptions from Python classes) match the installed PyO3 version | compile | adapt to the installed version, and note the version in the README |
| G8.8 | Tachyon can attach to the master and produce a flamegraph | the G6.3 command | `profiling.sampling run` launching `kiln serve` directly (no attach). Last resort: cProfile only, with the overhead caveat |
| G8.9 | on 3.15, `cProfile` is still available, and/or `profiling.tracing` is the new name (research §3 mentions both) | `python3.15 -c "import cProfile, profiling.tracing"` | use whichever exists; the Python note explains both |

## 9. Acceptance criteria

- **AC-8.1** The lab report's Decision section (profile shares, the Amdahl bound and the target choice per §5.1's rule) is committed **before** the first Rust commit. Git history shows the order.
- **AC-8.2** `protocol.validator` works in both modes in the master and the gateway. `native` without the extension exits 2 with the exact message.
- **AC-8.3** The equivalence properties (§5.5) pass with Hypothesis' `max_examples=2000` on 3.15 (and on 3.15t if G8.2 passed). Every divergence is fixed or listed with a justification.
- **AC-8.4** `kiln_native` imports on 3.13, 3.14, 3.15 (the same abi3 `.so`) and on 3.15t, where the GIL stays disabled after import.
- **AC-8.5** Benchmarks B1–B5 and the end-to-end runs are published to `results/published/K8/`, with CIs and spec/10 wording. The report answers "did it matter end-to-end?" honestly.
- **AC-8.6** The Docs deliverables exist, including the Rust README walkthrough, and `./scripts/dev.sh docs-check` passes.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/profiling-before-optimizing.md` | Amdahl recap (link K4) with Kiln's real numbers; sampling vs tracing distortion (link K6); "measure, change one thing, measure again" |
| Concept | `docs/concepts/c-api-basics.md` | `PyObject*`, reference counts in C (`Py_INCREF`/`DECREF`), borrowed vs owned references, module init (link K4's `c-extensions`), the GIL from C's point of view, what PyO3 automates |
| Concept | `docs/concepts/ffi-boundary-cost.md` | what happens on each call (argument parsing, conversion, object creation, error translation, GIL attach); why batching wins; B1–B5 results explained |
| Concept | `docs/concepts/pyo3-and-maturin.md` | crate → cdylib → `.so` → wheel; `#[pyfunction]`, `#[pymodule]`, `Bound<'py, T>`; maturin develop/build; how uv builds it; `gil_used = false` |
| Concept | `docs/concepts/stable-abi-abi3-abi3t.md` | API vs ABI; wheel tags (`cp315-cp315-manylinux…`, `cp313-abi3-…`, `cp315t`); why abi3 excludes free-threaded builds; PEP 803 abi3t and its observed support status |
| Python | `docs/python/cprofile-and-profiling-tracing.md` | `python -m cProfile -o`, `pstats` sorting, `profiling.tracing` on 3.15 (per G8.9), deterministic vs sampling |
| Python | `docs/python/timeit-and-perf-counter.md` | `perf_counter_ns`, the `timeit` module, why loops are needed for tiny operations, warm-ups |
| Python | `docs/python/importing-compiled-modules.md` | how `import kiln_native` finds a `.so` (extension suffixes, `importlib.machinery.EXTENSION_SUFFIXES`), `__file__`, ABI tags in filenames |
| Rust walkthrough | `rust/kiln_native/README.md` | a line-by-line walkthrough for a reader who has never seen Rust |
| Tour | `docs/tours/K8.md` | stops: profile decision → messages.py reference → validate.py facade/config → lib.rs → validate.rs/parse.rs → equivalence tests → boundary_bench |
| Lab | `docs/labs/K8-rust.md` | per the template, including the Decision, Known divergences and Builds sections |
| Spec updates | `docs/spec/05-configuration-and-cli.md` (`protocol.validator`), `docs/spec/01-architecture.md` (dependency rule), `docs/spec/09-testing-strategy.md` (`requires_native` marker) | |

## 11. Reference comparison assignment

1. **pydantic-core** (`src/validators/`, `src/input/`): how it walks Python input. *Questions:* does it convert Python objects to Rust values first, or validate *in place* on Python objects? Which of our B3/B5 lessons does its design reflect? How does it handle `bool` vs `int` strictness?
2. **pydantic-core's JSON mode** (`jiter`): *Question:* why did they write their own JSON parser instead of using serde_json, and how does that relate to our B5 "object-building" measurement?
3. **The PyO3 free-threading guide:** *Question:* what does PyO3 require of a module that declares `gil_used = false`, and is our module compliant? Look at `OnceLock` and the absence of global mutable state.
4. **Research §2.10, Tea and Bits:** *Question:* they found "the rest of the gain came from cutting allocations at the language boundary". Which of our measurements shows the same effect?

## 12. Risks and pitfalls

- **Debug builds** make Rust look slow. Always verify the release profile (G8.5) and record it in the Builds table.
- **The `bool` is an `int` gotcha:** `isinstance(True, int)` is `True` in Python. Both implementations must reject bools for `timeout_s`/`max_attempts`, and the Python note explains it.
- **Comparing against a strawman.** The Python reference must be reasonably written (a precompiled regex, no needless copies) before comparing, otherwise the speedup is fake. The reviewer checks this.
- **Stale `.so` files** from an earlier build shadow new ones. Check `kiln_native.__file__` and the sha256 in every benchmark record (`params.native_so_sha256`).
- **Profiling a 9P checkout** inflates import and file-I/O costs. Profile from the ext4 snapshot only (ADR-003).
- **ptrace** for Tachyon attach: see spec/07 §5.

## 13. Checkpoint quiz (seed questions)

1. The profile says validation is 4% of master time. What is the best possible end-to-end speedup from making it infinitely fast?
2. Why do sampling and tracing profilers disagree about functions that are called very often?
3. What happens, step by step, when Python calls `kiln_native.validate_submit(msg)`?
4. Why can calling a Rust function a million times for tiny work be *slower* than pure Python?
5. Why does `validate_many` (one call, N messages) beat N calls to `validate_submit`?
6. B5 shows that building the result dict is a big part of the native time. Why, and what does pydantic-core do about it?
7. What does the `abi3` tag let you do, and why can't the 3.15t build use it?
8. What does `gil_used = false` promise, and what in our crate makes that promise true?
9. Why is `True` a dangerous value for `max_attempts`, and why must both validators agree on it?
10. Hypothesis found that Python accepts `"\ud800"` and Rust rejects it. What are the two acceptable ways to resolve this, and why is silently filtering it out not one of them?

## 14. Stretch / later

- Release the GIL during the serde parse in `parse_and_validate_submit` (copy the bytes, detach, parse, re-attach to build objects), and measure the multi-threaded benefit on the GIL build.
- Rewrite `FrameDecoder` in Rust and measure. Is framing hot (from the §5.1 profile)?
- Build the module with mypyc instead (compiled pure Python), and compare it with Rust and CPython.
