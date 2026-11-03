# pytest basics

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [functions](functions.md), [decorators](decorators.md), [exceptions](exceptions.md) · **Reading time:** ~14 min
> **Terms introduced:** **test**, **pytest**, **assertion**, **test function**, **fixture**, **tmp_path**, **marker**, **parametrize**, **test collection**

## What it looks like

```python
# pytest_demo_example.py
def add(a, b):
    return a + b


def test_add_small_numbers():
    assert add(2, 3) == 5
```

```bash
(wsl) $ pytest pytest_demo_example.py
```

## What it means

A **test** is a small piece of code that runs your real code and checks that the result is what you expected. **pytest** is the most popular tool for writing and running tests in Python. Kiln uses it for everything (ADR-019).

- A **test function** is any function whose name starts with `test_`, in a file whose name starts with `test_`. pytest finds them by itself. This search is called **test collection**.
- `assert add(2, 3) == 5` is an **assertion**: a statement that must be true. If it is, nothing happens. If it isn't, Python raises `AssertionError` and pytest reports the test as **failed**, showing the values on both sides (`assert -1 == 5`).
- A test passes if it finishes without an exception.

Four more tools appear in Kiln's tests:

- A **fixture** is setup code that pytest runs for you and hands to your test. You declare it with `@pytest.fixture`. A test asks for it by having **a parameter with the fixture's name**, and pytest matches the names automatically.
- **`tmp_path`** is a built-in fixture: a fresh, empty temporary folder (a [Path](pathlib.md)) for each test, so tests never touch your real files or each other's.
- A **marker** is a label on a test, applied as a decorator: `@pytest.mark.<name>`. Kiln uses markers like `integration`, `slow` and `requires_ft` (spec/08 §2.5) to choose which tests run where.
- **`@pytest.mark.parametrize`** is a special marker that runs one test function several times with different inputs, from a table. Each row shows up as its own test (`test_add_table[1-1-2]`).

Useful command-line options:

| Option | Effect |
|---|---|
| `-v` | verbose: one line per test, with its name |
| `-k sorted` | only run tests whose name contains `sorted` |
| `-x` | stop at the first failure |
| `-m integration` | only run tests carrying that marker |
| `-s` | show `print` output while tests run (pytest normally captures it) |

Exit code (see the [what-is-a-process](../concepts/what-is-a-process.md) primer): `0` means all passed, `1` means some failed.

## Why people use it

Kiln is mostly *invisible* machinery: processes, signals, sockets. You can't eyeball whether a worker died correctly. Tests are how we *know* that it works, and that it keeps working when later milestones change things. pytest's plain `assert` style (no special `assertEqual` methods), its fixtures and its clear failure reports make tests short enough to read as documentation. In Kiln every test also has a docstring saying *what* behaviour it pins down and *why* (documentation standard §4.5).

## How Kiln uses it

- [`tests/unit/`](../../tests/unit/) holds fast tests: [`test_cli_smoke.py`](../../tests/unit/test_cli_smoke.py) checks `kiln --version`, [`test_doctor.py`](../../tests/unit/test_doctor.py) uses `tmp_path` to build a fake `/proc` tree, and [`test_check_docs.py`](../../tests/unit/test_check_docs.py) uses `@pytest.mark.parametrize` to run one good and one bad example per check.
- `capsys` is another built-in fixture. It captures what a function printed, so a test can check the CLI's output.
- The repo's pytest settings live in `pyproject.toml` (spec/08 §2.5): `testpaths = ["tests"]`, markers, and a 30-second timeout per test.
- You normally run tests through `./scripts/dev.sh test`, and across Python versions with `./scripts/dev.sh test-matrix`.

## Gotchas

- **Name it `test_*`, or it doesn't exist.** A function called `check_add` in `tests/` is silently ignored. So is a file called `adder_tests.py`.
- **Asserting on a tuple is always true.** `assert (x == 1, "message")` asserts a non-empty tuple, which always passes. Write `assert x == 1, "message"` without parentheses.
- **Floats:** `assert 0.1 + 0.2 == 0.3` fails (`0.30000000000000004`). Use `pytest.approx(0.3)`.
- **Tests must not depend on each other** or on their order. Each test gets fresh fixtures for this reason.
- **Never `sleep` to wait for something in a test**, because it's either too slow or flaky. Wait for a *condition* instead (spec/09 §4.1). This matters a lot from K1, when tests start real processes.

## Try it

The demo files are [`snippets/pytest_demo_example.py`](snippets/pytest_demo_example.py) and [`snippets/pytest_demo_failing.py`](snippets/pytest_demo_failing.py). They're named `pytest_demo_*.py` so the repo's own test run never collects them. They were copied into an empty scratch folder and run there, so the repo's pytest settings didn't apply. `uv run --no-project --with pytest` runs pytest without installing it anywhere permanent, and `-p no:cacheprovider` stops pytest from leaving a cache folder behind.

All passing:

```text
(wsl) $ uv run --no-project --quiet --with pytest --python 3.15 pytest -p no:cacheprovider pytest_demo_example.py
============================= test session starts ==============================
platform linux -- Python 3.15.1, pytest-9.2.0, pluggy-1.7.0
rootdir: /tmp/kiln-p2-pytest
collected 6 items

pytest_demo_example.py ......                                            [100%]

============================== 6 passed in 0.11s ===============================
```

Each `.` is one passed test: 1 plain test, 3 rows of the parametrized table, 1 `tmp_path` test and 1 fixture test. With `-v` you see each name, including the parametrized rows:

```text
(wsl) $ uv run --no-project --quiet --with pytest --python 3.15 pytest -p no:cacheprovider -v pytest_demo_example.py
...
pytest_demo_example.py::test_add_small_numbers PASSED                    [ 16%]
pytest_demo_example.py::test_add_table[1-1-2] PASSED                     [ 33%]
pytest_demo_example.py::test_add_table[0-0-0] PASSED                     [ 50%]
pytest_demo_example.py::test_add_table[-1-1-0] PASSED                    [ 66%]
pytest_demo_example.py::test_writes_a_file PASSED                        [ 83%]
pytest_demo_example.py::test_sorted_uses_fixture PASSED                  [100%]

============================== 6 passed in 0.02s ===============================
```

Selecting by name with `-k`:

```text
(wsl) $ uv run --no-project --quiet --with pytest --python 3.15 pytest -p no:cacheprovider -v -k sorted pytest_demo_example.py
...
collecting ... collected 6 items / 5 deselected / 1 selected

pytest_demo_example.py::test_sorted_uses_fixture PASSED                  [100%]

======================= 1 passed, 5 deselected in 0.01s ========================
```

A failure (the `add` function has a planted bug, `a - b`):

```text
(wsl) $ uv run --no-project --quiet --with pytest --python 3.15 pytest -p no:cacheprovider pytest_demo_failing.py
============================= test session starts ==============================
platform linux -- Python 3.15.1, pytest-9.2.0, pluggy-1.7.0
rootdir: /tmp/kiln-p2-pytest
collected 1 item

pytest_demo_failing.py F                                                 [100%]

=================================== FAILURES ===================================
____________________________ test_add_small_numbers ____________________________

    def test_add_small_numbers():
        total = add(2, 3)
>       assert total == 5
E       assert -1 == 5

pytest_demo_failing.py:9: AssertionError
=========================== short test summary info ============================
FAILED pytest_demo_failing.py::test_add_small_numbers - assert -1 == 5
============================== 1 failed in 0.02s ===============================
```

How to read a failure: the `>` marks the failing line, and the `E` line shows the actual values (`-1` vs the expected `5`). `pytest_demo_failing.py:9` is the file and line number. The exit code was `1`. (The `...` lines above stand for the same header lines as in the first run, trimmed.)

## Check yourself

1. pytest ignores a function `def check_parse():` in `tests/unit/test_parse.py`. Why?
   <details><summary>Answer</summary>

   Only functions whose names start with `test_` are collected as tests. Rename it `test_parse...`.

   </details>
2. How does a test receive a fixture named `numbers`?
   <details><summary>Answer</summary>

   By declaring a parameter called `numbers`: `def test_x(numbers):`. pytest sees the name, runs the fixture function, and passes in its return value.

   </details>
3. In the failure report, what do the `>` and `E` lines tell you?
   <details><summary>Answer</summary>

   `>` points at the line that failed. `E` shows the error, here the assertion with the real values filled in (`assert -1 == 5`), which tells you `add(2, 3)` returned -1.

   </details>
4. Why is `tmp_path` better than writing to a fixed file like `/tmp/out.txt` in a test?
   <details><summary>Answer</summary>

   Each test gets its own empty folder. Tests can't see each other's leftovers, can run in parallel safely, and never overwrite something important.

   </details>
