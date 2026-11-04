# `if __name__ == "__main__":` (the main guard)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [running-python-and-the-repl](running-python-and-the-repl.md), [modules-and-imports](modules-and-imports.md) · **Reading time:** ~6 min
> **Terms introduced:** **main guard**, **`__name__`**

## What it looks like

```python
def main():
    print("doing the work")

if __name__ == "__main__":   # the main guard
    main()
```

## What it means

Every module has a variable called **`__name__`**:
- When a file is **run** as the program (`python tool.py`), Python sets its `__name__` to the string `"__main__"`.
- When a file is **imported** (`import tool`), its `__name__` is its module name, `"tool"`.

So `if __name__ == "__main__":` means "only run this block when this file is the program, not when something imports it". That block is the **main guard**.

## Why people use it

1. **Reusable files.** You can import `tool.py` to reuse its functions without it immediately doing its job.
2. **Multiprocessing under spawn and forkserver.** A child process *re-imports* the main script, under the name `__mp_main__` ([main-module](../concepts/main-module.md)). Without the guard, the child would run the "start child processes" code again. Python stops that with a `RuntimeError`. That's the krb5 breakage, and forkcheck rule FA001.

## How Kiln uses it

- [`src/kiln/__main__.py`](../../src/kiln/__main__.py) and every script in `scripts/` end with the guard, so importing them (for example in tests) has no side effects.
- [`src/kiln/worker/__main__.py`](../../src/kiln/worker/__main__.py), the spawn entry point (`python -m kiln.worker`), runs the worker only under the guard.
- [`src/kiln/forkcheck/symbols.py`](../../src/kiln/forkcheck/symbols.py) finds guard regions, and FA001 in [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py) reports process starts outside them.

## Gotchas

- **The comparison has to be exact.** `if __name__ == "main":` (missing underscores) is never true. forkcheck accepts both orders (`"__main__" == __name__`), but only `==`.
- **Guarding a definition isn't the point.** It's fine for `def` and `import` to run in the child; it needs the functions. Guard the *actions*: starting processes, opening servers, running the program.
- **The child still runs everything outside the guard,** every time. Keep the top level free of side effects.

## Try it

```text
>>> __name__
'__console__'
>>> import json
>>> json.__name__
'json'
>>> import runpy, tempfile, pathlib
>>> folder = tempfile.mkdtemp()
>>> path = pathlib.Path(folder) / "tool.py"
>>> _ = path.write_text("print('tool.py says __name__ is', repr(__name__))\nif __name__ == '__main__':\n    print('...and the guarded block runs')\n")
>>> _ = runpy.run_path(str(path), run_name="__main__")
tool.py says __name__ is '__main__'
...and the guarded block runs
>>> _ = runpy.run_path(str(path), run_name="tool")
tool.py says __name__ is 'tool'
```

(Generated from [`snippets/if-name-main.repl`](snippets/if-name-main.repl) with `scripts/repl_transcript.py` on Python 3.15.1. In this transcript tool the console's own module is called `__console__`. In the real REPL it would be `'__main__'`.)

`runpy.run_path` runs a file with whatever `__name__` you choose ([runpy](runpy.md)). As `"__main__"` the guarded block runs, and as `"tool"` it doesn't.

What happens *without* the guard under spawn, from [`snippets/if-name-main_1.py`](snippets/if-name-main_1.py):

```text
the spawn child crashed with RuntimeError:
    An attempt has been made to start a new process before the
    current process has finished its bootstrapping phase.
    This probably means that you are not using fork to start your
the parent still printed: (nothing)
```

## Check yourself

1. `helpers.py` ends with `print("ready")` at top level, unguarded. What happens when another file does `import helpers`?
   <details><summary>Answer</summary>

   `ready` is printed: importing a module runs its top-level code. Put the print under the main guard if it should only happen when you run `helpers.py` itself.

   </details>
2. In a spawned child, what is `__name__` inside the re-imported main script?
   <details><summary>Answer</summary>

   `'__mp_main__'`, so `__name__ == "__main__"` is False there, and the guarded code doesn't run again.

   </details>
3. Is `if __name__ == "__main__": main()` enough if `main()` is *also* called at the top level?
   <details><summary>Answer</summary>

   No. The unguarded call runs in every spawned child. The guard protects only what's inside it.

   </details>
