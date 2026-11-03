# Exceptions

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [functions](functions.md), [classes and objects](classes-and-objects.md) · **Reading time:** ~14 min
> **Terms introduced:** **exception**, **raise**, **`try` / `except` / `else` / `finally`**, **traceback**, **custom exception**, **exception chaining (`raise ... from ...`)**, **`ValueError`**, **`OSError`**

## What it looks like

```python
class ConfigError(Exception):
    """Raised when a Kiln setting is invalid."""


def parse_workers(text):
    try:
        n = int(text)
    except ValueError as err:
        raise ConfigError("workers must be a whole number, got " + repr(text)) from err
    if n < 1:
        raise ConfigError("workers must be >= 1, got " + str(n))
    return n
```

## What it means

An **exception** is Python's way of saying "something went wrong, and I can't carry on normally". When it happens, Python stops what it was doing and jumps out of the current function, then out of the function that called it, and so on, looking for code that says "I can handle this". If nobody handles it, the program stops and prints a **traceback**.

- **`raise SomeError("message")`** starts that process yourself. It's how a function says "these inputs are unacceptable".
- **`try:`** marks a block that might fail. The parts that can follow it:
  - **`except SomeError as err:`** runs only if that kind of exception happened inside the `try`. `err` is the exception object, and `str(err)` is its message.
  - **`else:`** runs only if **no** exception happened.
  - **`finally:`** runs **always**, whether or not there was an exception, and even if the block `return`s. It's for cleanup, like closing a file.

```
try ─────▶ ok? ──yes──▶ else ──┐
            │                  ├──▶ finally ──▶ continue
            no                 │
            ▼                  │
     matching except? ──yes────┘
            │ no
            ▼
     finally, then the exception keeps flying upward to the caller
```

### Exceptions are classes

Every exception is an instance of a class, and those classes inherit from each other. `ValueError` means "right type, bad value" (`int("forty-two")`). `OSError` covers operating-system failures, like a missing file or a refused connection. `except ValueError:` also catches any **subclass** of `ValueError`.

A **custom exception** is a class you write that inherits from `Exception`: `class ConfigError(Exception): pass`. It lets callers catch *your* problems specifically. All of Kiln's own errors will inherit from one base class, `KilnError`, from K1 ([code-style §4](../standards/code-style.md#4-errors-and-exceptions)).

### Chaining: `raise ... from ...`

Inside an `except` block, **`raise NewError(...) from err`** raises a new, more meaningful error and records the original one as its cause. The traceback then shows both, joined by *"The above exception was the direct cause of the following exception"*. That's **exception chaining**.

### Reading a traceback

A traceback lists the chain of function calls that led to the error, **oldest call first and the failing line last**. Read it **from the bottom up**:
1. The **last line** is the exception type and message, which tells you *what* went wrong.
2. The lines just above show *where*: file, line number, function name, and the code on that line.
3. Further up is *how the program got there*.

## Why people use it

- **Errors can't be silently ignored.** A function that returns `-1` on failure can be ignored by mistake. An exception stops the program unless someone handles it on purpose.
- **Handle it where you know what to do.** Low-level code raises, and high-level code (like the CLI) decides whether to show a friendly message, retry, or give up.
- **Guaranteed cleanup** with `finally` (and with [context managers](context-managers.md), which build on it).

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) reads files under `/proc` inside `try: ... except OSError:`. A missing or unreadable file becomes a ⚠️ result with a hint, instead of crashing the doctor.
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) **raises `ValueError`** when asked to write an invalid ExperimentRecord, with a message listing every problem, so a bad measurement can never be saved quietly.
- Kiln's rules ([code-style §4](../standards/code-style.md#4-errors-and-exceptions)): catch the **narrowest** exception you expect; never write a bare `except:`; error messages say *what happened, why it matters, and what to do*.

## Gotchas

- **Don't catch everything.** A bare `except:` also catches Ctrl-C (`KeyboardInterrupt`) and hides real bugs. Catch the specific error you expect.
- **Don't swallow errors silently.** `except ValueError: pass` throws away the evidence. At minimum, log it.
- **`finally` runs even after `return`.** In "Try it", `parse_port("7340")` prints `finally always runs` *before* the REPL shows the returned `7340`.
- **The REPL's `__console__.ConfigError`.** In "Try it" the custom error shows as `__console__.ConfigError`. That prefix is the name of the module where the class was defined (here, the transcript tool's console). In a real file you'd see something like `kiln.config.ConfigError`.

## Try it

```text
>>> int("42")
42
>>> int("forty-two")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
ValueError: invalid literal for int() with base 10: 'forty-two'
>>> def parse_port(text):
...     try:
...         port = int(text)
...     except ValueError:
...         print("not a number:", text)
...         return None
...     else:
...         print("parsed ok")
...         return port
...     finally:
...         print("finally always runs")
... 
>>> parse_port("7340")
parsed ok
finally always runs
7340
>>> parse_port("abc")
not a number: abc
finally always runs
>>> class ConfigError(Exception):
...     pass
... 
>>> def check_workers(n):
...     if n < 1:
...         raise ConfigError("workers must be >= 1 (got " + str(n) + ")")
...     return n
... 
>>> check_workers(4)
4
>>> check_workers(0)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "<console>", line 3, in check_workers
__console__.ConfigError: workers must be >= 1 (got 0)
>>> try:
...     check_workers(0)
... except ConfigError as err:
...     print("caught:", err)
... 
caught: workers must be >= 1 (got 0)
>>> def load(text):
...     try:
...         return int(text)
...     except ValueError as err:
...         raise ConfigError("bad config value") from err
... 
>>> load("x")
Traceback (most recent call last):
  File "<console>", line 3, in load
ValueError: invalid literal for int() with base 10: 'x'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "<console>", line 5, in load
__console__.ConfigError: bad config value
```

`parse_port("abc")` printed nothing after `finally always runs`, because it returned `None` and the REPL doesn't display `None`.

Tracebacks from a real file are richer. They show the source line and mark the failing part with `~` and `^`. [`exceptions_1.py`](snippets/exceptions_1.py) prints one with `traceback.print_exc()`, without crashing:

```text
(wsl) $ python3.15 docs/python/snippets/exceptions_1.py
Traceback (most recent call last):
  File "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln/docs/python/snippets/exceptions_1.py", line 15, in <module>
    start({"workers": "four"})
    ~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln/docs/python/snippets/exceptions_1.py", line 11, in start
    return read_workers(config)
  File "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln/docs/python/snippets/exceptions_1.py", line 7, in read_workers
    return int(config["workers"])
ValueError: invalid literal for int() with base 10: 'four'
the program kept going after printing the traceback
```

Reading it from the bottom: a `ValueError`, because `'four'` isn't a number. It happened in `read_workers` at line 7, which was called by `start` at line 11, which was called from the top level at line 15.

## Check yourself

1. In `try / except / else / finally`, which parts run when **no** exception happens?
   <details><summary>Answer</summary>

   The `try` block, then `else`, then `finally`. The `except` block is skipped.

   </details>
2. Where in a traceback do you look first, and why?
   <details><summary>Answer</summary>

   At the last line. It names the exception type and message, which says *what* went wrong. The lines above it show where and how the program got there.

   </details>
3. Why write `raise ConfigError("bad value") from err` instead of plain `raise ConfigError("bad value")`?
   <details><summary>Answer</summary>

   `from err` records the original exception as the cause, so the traceback shows both: the low-level detail (`ValueError: invalid literal...`) and the high-level meaning (`ConfigError`). Without `from`, Python still shows the original error, but it labels it *"During handling of the above exception, another exception occurred"*. That reads like a second, accidental bug rather than a deliberate translation of the error.

   </details>
4. Why is `except:` (with nothing after it) discouraged?
   <details><summary>Answer</summary>

   It catches *everything*, including Ctrl-C and programming mistakes like typos, so real bugs get hidden. Catch the specific exception you expect, such as `except ValueError:` or `except OSError:`.

   </details>
