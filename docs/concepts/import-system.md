# The Import System: What Happens When You Write `import x`

> **Milestone:** K2 · **Read before:** [modules-and-imports](../python/modules-and-imports.md) · **Reading time:** ~12 min
> **Terms introduced:** **module cache**, **finder**, **loader**, **import-time side effect**

## One-sentence summary

The first `import x` in a process finds the file, *runs all of its top-level code*, and stores the resulting module in `sys.modules`. Every later import in that process is only a dictionary lookup, but every **new** process starts with an empty cache and runs the code again.

## Why you should care

"Module code runs once" is true *per process*. Under fork the child inherits the parent's already-filled `sys.modules`, so nothing runs again. Under spawn and forkserver the child imports afresh, so every **import-time side effect** happens again: registering an `atexit` handler, starting a thread, configuring logging, opening a file. krb5's `k5test` registered an `atexit` handler on import, and every forkserver child printed a fake failure banner because of it (errata E-05). That's forkcheck rule FA005.

## The mental model

**Analogy:** a library card catalogue. The first time anyone asks for a book, the librarian finds it on the shelves, photocopies it (runs the module) and files the copy at the front desk (`sys.modules`). Every later request in *this* library is answered from the front desk. A *new* library (a spawned process) has an empty front desk.

**Where the analogy fails:** "photocopying" a module means *executing* it, and execution can do anything: print, start threads, connect to databases. That's the danger.

```
 import noisy
   │
   ├─ "noisy" in sys.modules? ── yes ──▶ return it (nothing runs)
   │
   └─ no ─▶ finders search sys.path ─▶ loader creates a module object
            ─▶ sys.modules["noisy"] = module   (stored BEFORE running, so circular imports find it)
            ─▶ run noisy.py's top-level code   ← side effects happen HERE, once per process
            ─▶ bind the name "noisy" in the importing module
```

## See it for yourself

[`snippets/import-system_1.py`](snippets/import-system_1.py) creates a module that prints when it runs, and imports it three times:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/import-system_1.py
```

```text
first import:
  noisy.py is executing its top-level code
second import (nothing printed, it comes from sys.modules):
same object both times? True
cached in sys.modules? True
after deleting it from sys.modules, importing runs the file again:
  noisy.py is executing its top-level code
```

- The first import runs the file.
- The second returns the cached object, the *same* object (`is` is True).
- Deleting the cache entry makes Python run the file again. A spawned process is like starting with *nothing* in the cache.

## How it really works

- `sys.modules` is a plain dict: module name → module object. `import` checks it first.
- On a miss, Python asks each **finder** on `sys.meta_path` "can you find `noisy`?". The usual one, `PathFinder`, searches each folder of `sys.path`. A finder returns a **spec** that includes a **loader**. The loader creates the module object and executes the code (`exec(code, module.__dict__)`).
- The module is put into `sys.modules` *before* its code runs, so circular imports get a half-initialised module instead of looping forever.
- Everything at the top level of the file executes: `def` and `class` statements *create* functions and classes (cheap), but calls like `atexit.register(...)`, `threading.Thread(...).start()` or `logging.basicConfig(...)` *do things*.
- **Per process:** `sys.modules` lives in the process's memory.
  - **fork** copies it, already filled.
  - **spawn** starts empty and imports the main script (as `__mp_main__`, see [main-module](main-module.md)) and then everything it imports.
  - **forkserver** children start with whatever the forkserver had imported (its preload list) and import the rest fresh.

## How Kiln uses it

- `kiln.app.load_app` imports the user's app with `importlib.import_module` ([`../python/importlib.md`](../python/importlib.md)). Under fork with `app.preload`, that happens once, in the master. Under spawn it happens in every worker.
- [`src/kiln/launch/forkserver_main.py`](../../src/kiln/launch/forkserver_main.py) imports `start.forkserver_preload` once, so its children share those modules.
- [`src/kiln/forkcheck/scanner.py`](../../src/kiln/forkcheck/scanner.py) follows imports *between scanned files* (cross-module resolution, K2 spec §5.6). That lets FA005 report "`k5test_like.py` registers an atexit handler on import, and it's imported by `distilled.py`, which starts processes".

## Common misconceptions

- ❌ **Myth:** "A module's top-level code runs once, ever."
  ✅ **Reality:** once *per process*. Every spawned process runs it again.
- ❌ **Myth:** "`import` only makes names available."
  ✅ **Reality:** the first import *executes* the module. Side effects at top level are real actions.
- ❌ **Myth:** "`from m import X` imports only `X`."
  ✅ **Reality:** it runs all of `m` and then picks out `X`.

## Check yourself

1. Two different files both do `import json`. How many times does `json`'s code run in one process?
   <details><summary>Answer</summary>

   Once. The second import finds `"json"` in `sys.modules` and reuses the same module object.

   </details>
2. A module calls `atexit.register(report)` at top level. A program imports it and starts 4 spawn children. How many times can `report` run in total?
   <details><summary>Answer</summary>

   Up to 5: once in the parent and once in each child that exits normally, because each child re-imports the module and registers its own handler. krb5 hit exactly this with `k5test`.

   </details>
3. Why does Python store a module in `sys.modules` *before* running its code?
   <details><summary>Answer</summary>

   So that circular imports (a imports b, b imports a) find the partly-built module in the cache instead of starting the import again and looping forever.

   </details>

## Go deeper

- [Python docs: The import system](https://docs.python.org/3.15/reference/import.html).
- [Python docs: `sys.modules`](https://docs.python.org/3.15/library/sys.html#sys.modules).
- CPython source: `Lib/importlib/_bootstrap.py` (`_find_and_load`).
