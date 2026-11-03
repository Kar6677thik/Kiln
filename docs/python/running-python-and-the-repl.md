# Running Python and the REPL

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** nothing, this is the first note · **Reading time:** ~10 min
> **Terms introduced:** **interpreter**, **REPL**, **script**, **module**, **`__name__`**, **`python -m`**, **`uv run`**

## What it looks like

```bash
(wsl) $ python3.15                                  # start the interactive REPL
(wsl) $ python3.15 docs/python/snippets/greeting.py # run a file (a "script")
(wsl) $ python3.15 -m greeting                      # run a module by its import name
(wsl) $ python3.15 -c 'print("hello from -c")'      # run one line of code
(wsl) $ ./scripts/dev.sh run some_file.py           # Kiln's way: right interpreter, right packages
```

## What it means

**Python code doesn't run on its own.** A program called the **interpreter** reads it and carries it out, line by line. On your machine the interpreter is a file such as `/home/Megatron/.local/bin/python3.15`. When docs say "Python 3.15", they mean "the 3.15 version of that program". Kiln uses several versions side by side (see [ADR-004](../decisions.md#adr-004)).

There are four ways to hand code to the interpreter:

1. **The REPL** (Read–Eval–Print Loop). Type `python3.15` with nothing after it and you get a `>>>` prompt. The interpreter **reads** one line, **evaluates** it (runs it), **prints** the result, and **loops** back for the next line. It's a calculator for Python code, and the fastest way to try something out. Type `exit()` or press Ctrl-D to leave.
2. **A script.** A **script** is a `.py` file that you run as a program: `python3.15 path/to/file.py`. Python runs the file from top to bottom, then exits.
3. **`python -m name`.** This runs a **module** by its *import name* instead of its file path. A **module** is any `.py` file that other Python code can `import` (see [modules and imports](modules-and-imports.md)). `-m` makes Python search for the module the same way `import` would, then run it as the main program. Kiln will be started this way: `python -m kiln` runs [`src/kiln/__main__.py`](../../src/kiln/__main__.py).
4. **`python -c 'code'`** runs a short piece of code given on the command line. It's handy in shell scripts.

### Running vs. importing, and the `__name__` variable

Every module has a hidden variable called **`__name__`** (two underscores on each side). Python sets it for you:

- If the file is the program you started (as a script or with `-m`), `__name__` is the string `"__main__"`.
- If the file was **imported** by another file, `__name__` is the module's own name, for example `"greeting"`.

That's why you'll often see this at the bottom of a file:

```python
if __name__ == "__main__":
    main()
```

It means "only start the program if this file was *run*, not if it was *imported*". Kiln's [`src/kiln/__main__.py`](../../src/kiln/__main__.py) and [`scripts/repl_transcript.py`](../../scripts/repl_transcript.py) both end like this. In K2 this line becomes very important: it decides whether a worker started with the *spawn* method re-runs your whole program (see [spec/04 §6](../spec/04-supervisor.md#6-start-methods-k2)).

### `uv run` and `scripts/dev.sh run`

Kiln needs the *right* interpreter *and* the right extra packages (pytest, mypy…). Those packages live in a **virtual environment**, a folder holding one interpreter link plus installed packages. **`uv run`** runs a command inside the project's virtual environment, creating or updating it first if needed. Kiln wraps it once more: `./scripts/dev.sh run file.py` picks the interpreter from `KILN_PY` (default 3.15) and keeps the environment on the fast Linux disk ([ADR-003](../decisions.md#adr-003)). From K0 onward, that's how you run things.

## Why people use it

- The **REPL** gives instant feedback, so you can test an idea in seconds without writing a file.
- **Scripts** are repeatable: the same file gives the same program every time.
- **`-m`** runs code by its import name, so it works the same no matter which folder the file lives in. It also puts the current folder on the import path (more in [modules and imports](modules-and-imports.md)).
- **`uv run` / `dev.sh`** make sure everyone (you, the implementer, every test run) uses the same interpreter and packages.

## How Kiln uses it

- [`src/kiln/__main__.py`](../../src/kiln/__main__.py) makes `python -m kiln` work. It calls `main()` from [`src/kiln/cli.py`](../../src/kiln/cli.py) and passes the returned number to the operating system as the **exit code**.
- [`scripts/repl_transcript.py`](../../scripts/repl_transcript.py) is a script that feeds lines into Python's own REPL machinery and records the real output. Every "Try it" section in these notes was produced with it, so the output you see is genuine.
- `scripts/dev.sh run` (from K0) is the standard way to run any snippet in the docs.

## Gotchas

- **`python` vs `python3.15`.** On Debian, the bare `python3` is the *system* Python (3.13.5), not the ones Kiln installed. Always use `./scripts/dev.sh run`, or name the version explicitly.
- **Indented blocks in the REPL end with an empty line.** After `def f():` the prompt changes to `...`, and you have to press Enter on an empty line to finish the block. A blank line *inside* a function body also ends it early in the REPL (a file doesn't have this problem).
- **The REPL prints values automatically, a script doesn't.** Typing `1 + 2` at `>>>` shows `3`. Writing `1 + 2` in a file shows nothing; you need `print(1 + 2)`.
- **Importing a module runs its top-level code.** In the example below, merely *importing* `greeting` prints a line. That's why "don't do work at import time" is a common rule.

## Try it

```text
>>> 1 + 2
3
>>> "kiln".upper()
'KILN'
>>> import sys
>>> sys.version_info[:2]
(3, 15)
>>> x = 10
>>> x * 3
30
>>> print("hello from the REPL")
hello from the REPL
```

Notice that `"kiln".upper()` shows `'KILN'` *with quotes*: the REPL displays the value the way you'd type it in code. `print(...)` shows the text *without* quotes, the way a user would see it.

Now the difference between running and importing. [`greeting.py`](snippets/greeting.py) prints its own `__name__`. [`running-python-and-the-repl_1.py`](snippets/running-python-and-the-repl_1.py) imports it:

```text
(wsl) $ python3.15 docs/python/snippets/greeting.py
greeting.py is running; __name__ is '__main__'
hello, script

(wsl) $ python3.15 docs/python/snippets/running-python-and-the-repl_1.py
greeting.py is running; __name__ is 'greeting'
this file's __name__ is '__main__'
hello, importer

(wsl) $ cd docs/python/snippets && python3.15 -m greeting
greeting.py is running; __name__ is '__main__'
hello, script

(wsl) $ python3.15 -c 'print("hello from -c")'
hello from -c
```

When run directly, `greeting.py` sees `__name__ == '__main__'` and runs its `if` block. When imported, it sees `'greeting'` and skips that block, but its top-level `print` *still* ran during the import.

## Check yourself

1. You type `"abc" * 2` at the `>>>` prompt. What do you see, and what would you see if the same line were in a script?
   <details><summary>Answer</summary>

   The REPL shows `'abcabc'`, because it prints the value of every expression you type. In a script nothing is printed: the value is computed and thrown away. You'd need `print("abc" * 2)`, which prints `abcabc` without quotes.

   </details>
2. A file `tool.py` ends with `if __name__ == "__main__": main()`. When does `main()` run?
   <details><summary>Answer</summary>

   Only when `tool.py` is the program being started: `python tool.py` or `python -m tool`. If another file does `import tool`, then `__name__` is `"tool"`, the condition is false, and `main()` doesn't run.

   </details>
3. Why does Kiln use `./scripts/dev.sh run file.py` instead of `python3 file.py`?
   <details><summary>Answer</summary>

   `python3` on Debian is the system Python 3.13.5, without Kiln's packages. `dev.sh run` uses the chosen version (default 3.15) *and* the project's virtual environment, which lives on the fast Linux disk (ADR-003).

   </details>
