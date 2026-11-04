# runpy (running code by path or module name)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [if-name-main](if-name-main.md), [modules-and-imports](modules-and-imports.md) · **Reading time:** ~6 min
> **Terms introduced:** **runpy**, **`run_path`**, **`run_module`**, **`python -m`**

## What it looks like

```python
import runpy
namespace = runpy.run_path("script.py", run_name="__main__")   # run a file as if it were the program
runpy.run_module("json.tool", run_name="__main__")             # what `python -m json.tool` does
```

## What it means

- **`runpy.run_path(path, run_name=...)`** executes a Python file *as a fresh module*, with `__name__` set to `run_name`, and returns the module's final variables as a dict. With `run_name="__main__"` the file behaves exactly as if you'd typed `python path`, so its main guard runs.
- **`runpy.run_module(name, run_name=...)`** does the same for a module found on `sys.path` by its import name.
- **`python -m name`** is the command-line form of `run_module(name, run_name="__main__")`. It's how `python -m pytest`, `python -m kiln` and Kiln's spawn workers (`python -m kiln.worker`) start.

## Why people use it

- To run a module by *name* (`-m`), so the right package is found on `sys.path` instead of hunting for a file path.
- To run another script *inside the current process*, for example a test harness that first changes a setting and then runs the user's script as `__main__`.

## How Kiln uses it

- [`src/kiln/launch/spawn.py`](../../src/kiln/launch/spawn.py) starts each worker as `python -m kiln.worker ...`, which runs [`src/kiln/worker/__main__.py`](../../src/kiln/worker/__main__.py).
- [`src/kiln/forkcheck/confirm.py`](../../src/kiln/forkcheck/confirm.py) runs a suspect script under each start method with a tiny bootstrap: `mp.set_start_method(M)` then `runpy.run_path(ENTRY, run_name="__main__")` (K2 spec §5.9). Gate G2.5 checks that spawn children still find the main module this way.

## Gotchas

- **Without `run_name`, `__name__` is `"<run_path>"`.** The main guard doesn't run. See the first run in "Try it".
- **It runs in the current process.** Side effects (prints, `sys.exit`, `sys.argv` reading) affect your program. Set `sys.argv` first if the script reads it.
- **`python -m` needs a module name, not a path:** `python -m kiln.worker`, not `python -m src/kiln/worker.py`.

## Try it

```text
>>> import runpy, tempfile, pathlib
>>> folder = tempfile.mkdtemp()
>>> path = pathlib.Path(folder) / "hello.py"
>>> _ = path.write_text("GREETING = 'hi'\nprint('running as', __name__)\n")
>>> result = runpy.run_path(str(path))
running as <run_path>
>>> result["GREETING"]
'hi'
>>> _ = runpy.run_path(str(path), run_name="__main__")
running as __main__
>>> _ = runpy.run_module("json.tool", run_name="not_main")
>>> import subprocess, sys
>>> subprocess.run([sys.executable, "-m", "json.tool", "--help"], capture_output=True, text=True).stdout.splitlines()[0]
'usage: python3.15 -m json.tool [-h] [--sort-keys] [--no-ensure-ascii]'
```

(Generated from [`snippets/runpy.repl`](snippets/runpy.repl) on Python 3.15.1.)

`run_module("json.tool", run_name="not_main")` printed nothing: the tool's main guard didn't fire. With `-m` (the last line) `json.tool` ran as the program and printed its usage.

## Check yourself

1. What is `__name__` inside a file run with `runpy.run_path(path)` and no `run_name`?
   <details><summary>Answer</summary>

   `'<run_path>'`, as the transcript shows. Pass `run_name="__main__"` to make the main guard run.

   </details>
2. What does `python -m kiln.worker --fd 7` do, in runpy terms?
   <details><summary>Answer</summary>

   It finds the module `kiln.worker` on `sys.path` and runs its `__main__.py` with `__name__ == "__main__"`, like `runpy.run_module("kiln.worker", run_name="__main__")`, with `sys.argv` set to the arguments.

   </details>
3. Why does forkcheck's `--confirm` bootstrap use `run_path` instead of starting `python ENTRY.py` directly?
   <details><summary>Answer</summary>

   It must call `multiprocessing.set_start_method(M)` *before* the user's script runs, in the same process. Running the script with `run_path(..., run_name="__main__")` right after that lets the script behave exactly as if launched normally.

   </details>
