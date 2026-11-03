# Context Managers (`with`)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [classes and objects](classes-and-objects.md), [exceptions](exceptions.md) · **Reading time:** ~12 min
> **Terms introduced:** **context manager**, **`with` statement**, **`__enter__` / `__exit__`**, **`contextlib.contextmanager`**, **resource leak**

## What it looks like

```python
with open("notes.txt", "w") as fh:  # set up: open the file
    fh.write("hello\n")  # use it
# here the file is guaranteed to be closed, even if write() had failed
```

## What it means

Many things need a **set-up step** and a guaranteed **clean-up step**: open a file → close it; take a lock → release it; start a timer → stop it. If you forget the clean-up, or an error skips it, you get a **resource leak**. Examples: a file left open, a lock held forever, a socket never closed. A long-running server like Kiln can't afford those, because they pile up over days.

The **`with` statement** guarantees the clean-up. The object after `with` is a **context manager**. Python calls two of its methods for you:

1. **`__enter__()`** runs at the start. Whatever it returns is bound to the name after `as` (here `fh`).
2. The indented block runs.
3. **`__exit__(exc_type, exc, tb)`** runs at the end, **always**: after a normal finish, after a `return`, and after an exception. If an exception happened, Python passes its details in. Otherwise all three are `None`.

It's equivalent to this `try/finally` (see [exceptions](exceptions.md)), but shorter and impossible to get wrong:

```python
fh = open("notes.txt", "w").__enter__()
try:
    fh.write("hello\n")
finally:
    fh.__exit__(...)  # for a file, this closes it
```

If `__exit__` returns `False` (or `None`), any exception keeps flying after the clean-up. If it returns `True`, the exception is swallowed. That's rarely what you want, and Kiln never does it.

### Writing your own the short way: `@contextmanager`

Writing a class with `__enter__` and `__exit__` is one way (see `Timer` in "Try it"). The standard library offers a shorter one: **`contextlib.contextmanager`**. You write a function with a single `yield`:
- the code **before** `yield` is the set-up;
- the value you `yield` is what `as` receives;
- the code **after** `yield` (put it in `finally:`) is the clean-up.

```python
from contextlib import contextmanager


@contextmanager
def opened(name):
    print("open", name)  # set-up
    try:
        yield name  # the with-block runs here
    finally:
        print("close", name)  # clean-up, always
```

(`yield` makes the function a *generator*, which can pause in the middle and resume later. You don't need to understand generators to *use* this pattern.)

## Why people use it

- **Clean-up can't be forgotten** and can't be skipped by an error.
- **Set-up and clean-up sit next to each other**, so a reader sees the whole lifetime of the resource at a glance.
- **Less code** than writing `try/finally` everywhere.

## How Kiln uses it

- [`scripts/repl_transcript.py`](../../scripts/repl_transcript.py) uses `with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):` to point `print()` output at a buffer **temporarily**. When the block ends, output goes back to the screen, even if the code inside raised an error. (One `with` can manage several context managers, separated by commas.)
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) writes each ExperimentRecord with `with open(...)`, so the file is always closed before it's renamed into place ([spec/10 §2.1](../spec/10-measurement-methodology.md#21-field-rules)).
- From K1, Kiln's rule is that *every* file, socket and selector is managed with `with` or closed in a `finally` ([code-style §8](../standards/code-style.md#8-resource-cleanup)). The master opens many sockets, and a leaked one stays open for the master's whole life.

## Gotchas

- **The name after `as` still exists after the block**, but the resource is closed. Using `fh` after the block raises `ValueError: I/O operation on closed file`.
- **`with open(path)` reads text by default.** For raw bytes use `open(path, "rb")` (see [bytes vs str](bytes-vs-str.md)).
- **Forgetting `try/finally` in a `@contextmanager` function.** Without it, the clean-up after `yield` is skipped when the block raises.
- **`fh.write(...)` returns a number.** In the REPL you'll see `6` printed after writing `"hello\n"`. That's the count of characters written, not an error.

## Try it

```text
>>> from pathlib import Path
>>> import tempfile
>>> folder = tempfile.mkdtemp()
>>> path = Path(folder) / "notes.txt"
>>> with open(path, "w") as fh:
...     fh.write("hello\n")
... 
6
>>> fh.closed
True
>>> with open(path) as fh:
...     print(fh.read())
... 
hello

>>> class Timer:
...     def __enter__(self):
...         print("enter: start timing")
...         return self
...     def __exit__(self, exc_type, exc, tb):
...         print("exit: stop timing; error was", exc_type)
...         return False
... 
>>> with Timer() as t:
...     print("inside the block")
... 
enter: start timing
inside the block
exit: stop timing; error was None
>>> with Timer():
...     1 / 0
... 
enter: start timing
exit: stop timing; error was <class 'ZeroDivisionError'>
Traceback (most recent call last):
  File "<console>", line 2, in <module>
ZeroDivisionError: division by zero
>>> from contextlib import contextmanager
>>> @contextmanager
... def announce(name):
...     print("start", name)
...     try:
...         yield name.upper()
...     finally:
...         print("end", name)
... 
>>> with announce("job") as value:
...     print("got", value)
... 
start job
got JOB
end job
```

Things to notice:
- `fh.closed` is `True` right after the block, because `with` closed the file.
- The blank line after `hello` is there because the file content already ends with `\n` and `print` adds another.
- When `1 / 0` failed, `__exit__` still ran (`exit: stop timing; error was <class 'ZeroDivisionError'>`), and *then* the exception continued, because `__exit__` returned `False`.

The same guarantee with `@contextmanager` and a real error, from [`context-managers_1.py`](snippets/context-managers_1.py):

```text
(wsl) $ python3.15 docs/python/snippets/context-managers_1.py
open socket
using socket
close socket
caught: something went wrong
```

`close socket` printed **before** `caught: ...`. The clean-up ran first, then the error reached the `except`.

## Check yourself

1. What does `with` guarantee that `fh = open("f")` followed by `fh.close()` doesn't?
   <details><summary>Answer</summary>

   That the file is closed even if an exception happens (or a `return` runs) between opening and closing. With the manual version, an error before `fh.close()` skips the close and leaks the file.

   </details>
2. In a class-based context manager, which method's return value does `as` receive?
   <details><summary>Answer</summary>

   `__enter__`'s. In `with Timer() as t`, `t` is whatever `__enter__` returned (here `self`, the Timer).

   </details>
3. Predict the output order: a `@contextmanager` prints `A` before `yield` and `C` in `finally` after it, and the `with` block prints `B` then raises an error that you catch outside and print as `D`.
   <details><summary>Answer</summary>

   `A`, `B`, `C`, `D`. The clean-up (`C`) runs before the exception reaches the outer `except`, which prints `D`.

   </details>
