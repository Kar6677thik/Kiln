# Import hooks: `sys.meta_path`

> **First used in:** see the [ledger](README.md) (milestone K3) · **Needs:** [modules-and-imports](modules-and-imports.md), [importlib](importlib.md), [classes-and-objects](classes-and-objects.md) · **Reading time:** ~9 min
> **Terms introduced:** **`sys.meta_path`**, **meta path finder**, **module spec**, **`exec_module`**

## What it looks like

```python
import importlib.abc
import sys

class Watcher(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        for finder in sys.meta_path[1:]:          # ask the real finders
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                ...                               # wrap spec.loader.exec_module here
                return spec
        return None

sys.meta_path.insert(0, Watcher())                # we're asked first from now on
```

## What it means

When you `import x` and `x` isn't already in `sys.modules`, Python asks each object in **`sys.meta_path`** in turn: "`find_spec('x', ...)`: do you know this module?" ([import-system](../concepts/import-system.md)).
- A **meta path finder** answers with a **module spec** (`importlib.machinery.ModuleSpec`): the module's name, where it comes from, and the **loader** that can create it. `None` means "not me, ask the next one".
- Python creates an empty module object, then calls **`loader.exec_module(module)`**, which runs the module's code inside it.
- The default `sys.meta_path` holds three finders: built-in modules, frozen modules, and the path-based finder that searches `sys.path`. (Each is a class used directly, which is why the transcript below shows `'type'` three times.)

The **delegate-and-wrap pattern**: a finder at position 0 doesn't find anything itself. It asks the remaining finders, takes the spec they return, replaces the loader's `exec_module` with a wrapper that does something before and after calling the real one, and returns the spec. The import then proceeds normally, through your wrapper.

## Why people use it

Running code around *every* import without changing any module: measuring import time, logging what gets imported, enforcing "module X must never be imported", or loading modules from unusual places (zip files, databases, encrypted files).

## How Kiln uses it

- `src/kiln/memory/gcwatch.py`, layer B: the wrapper records `gc.isenabled()` before and after each module's `exec_module`, so if importing a module turns the GC on, by Python code or by a C extension calling `PyGC_Enable()`, the module is named ([global-interpreter-state](../concepts/global-interpreter-state.md)).
- The K3 spec requires a test that imports a namespace package, a C extension module (`_json`) and a zip-imported module with the watcher installed, because those have unusual loaders.

## Gotchas

- **Only modules not yet imported pass through.** A second `import x` comes from `sys.modules` and never reaches `sys.meta_path` (see the transcript).
- **Not every loader has `exec_module`**, and some loaders are shared between modules. Only wrap loaders that have `exec_module`, and bind the original function per spec (the `real=real` default argument below avoids the late-binding trap from [closures](closures.md)).
- **Namespace packages** have no code to execute; their spec's loader behaves differently. Delegate everything you don't need to change.
- **Remove your finder when done** (`sys.meta_path.remove(...)`), or it stays active for the life of the process.

## Try it

```text
>>> import sys, importlib.abc
>>> [type(f).__name__ for f in sys.meta_path]
['type', 'type', 'type']
>>> class Logger(importlib.abc.MetaPathFinder):
...     def find_spec(self, name, path, target=None):
...         for finder in sys.meta_path[1:]:
...             spec = finder.find_spec(name, path, target)
...             if spec is not None:
...                 real = spec.loader.exec_module
...                 def exec_module(module, real=real):
...                     print("executing", module.__name__)
...                     real(module)
...                 spec.loader.exec_module = exec_module
...                 return spec
...         return None
... 
>>> sys.meta_path.insert(0, Logger())
>>> import colorsys
executing colorsys
>>> import colorsys
>>> sys.meta_path.pop(0).__class__.__name__
'Logger'
```

(Generated from [`snippets/import-hooks-meta-path.repl`](snippets/import-hooks-meta-path.repl) on Python 3.15.1.)

The first `import colorsys` went through our finder and printed; the second came straight from `sys.modules`.

## Check yourself

1. Why does the second `import colorsys` print nothing?
   <details><summary>Answer</summary>

   The module was already in `sys.modules`, so the import system returned it without asking any finder.

   </details>
2. Why does the wrapper take `real=real` as a default argument instead of just using `real`?
   <details><summary>Answer</summary>

   Late binding: a nested function reads `real` when it's *called*. If the finder handled another import in between, `real` would point at the wrong loader's method. A default argument captures the value at definition time.

   </details>
3. A C extension calls `PyGC_Enable()` in its init function. Why does a meta-path watcher still catch it, when a `gc.enable` shim doesn't?
   <details><summary>Answer</summary>

   The watcher doesn't care *how* the GC was enabled; it compares `gc.isenabled()` before and after running the module. The shim only sees calls that go through the Python attribute `gc.enable`.

   </details>
