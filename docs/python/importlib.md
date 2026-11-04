# `importlib` (importing a module by name)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [modules-and-imports](modules-and-imports.md), [exceptions](exceptions.md) · **Reading time:** ~6 min
> **Terms introduced:** **importlib.import_module**, **getattr**, **app spec ("module:attribute")**

## What it looks like

```python
import importlib

module = importlib.import_module("examples.demo")   # same as `import examples.demo`
app = getattr(module, "app")                        # same as `examples.demo.app`
```

## What it means

A normal `import json` statement needs the module name written in your source code. Sometimes the name only arrives at run time, for example typed by a user on the command line. **importlib.import_module** does the same job as the `import` statement, but takes the name as a string:

- It searches `sys.path` exactly like `import` does (see [modules-and-imports](modules-and-imports.md)).
- It runs the module's top-level code the **first** time.
- It stores the module in `sys.modules`. Later calls return the *same* module object without running it again.
- If the module isn't found, it raises `ModuleNotFoundError`.

**getattr** is the run-time version of the dot: `getattr(module, "app")` is `module.app`, with the attribute name given as a string. A missing attribute raises `AttributeError`.

Together they turn an **app spec ("module:attribute")** like `"examples.demo:app"` into an object: split at the colon, import the left part, then take the right part from it.

## Why people use it

Plugin systems, web servers and job servers all let the user say *which* code to run, without editing the server. gunicorn takes `myproject.wsgi:application`. Celery takes `-A proj`. `kiln serve examples.demo:app` works the same way. The server can't contain `import examples.demo`, because it doesn't know that name until you type it.

## How Kiln uses it

[`src/kiln/app.py`](../../src/kiln/app.py) → `load_app(spec, pythonpath)`:
1. It adds the `app.pythonpath` folders to `sys.path`.
2. It splits the spec at `:` with `str.partition`.
3. It calls `importlib.import_module(module_name)`, then `getattr(module, attribute)`.
4. It checks that the result really is a `kiln.App`.

Each failure becomes an `AppLoadError` that says what went wrong and how to fix it. A missing colon, a missing module, a missing attribute, and "not an App" each get their own message.

With `app.preload = true`, the **master** runs `load_app` before forking, so workers inherit the already-imported module. Otherwise each worker calls it itself.

## Gotchas

- **Importing runs the module's code.** `import_module("examples.demo")` executes everything at the top of that file. That's why the master importing your app (preload) means the master runs your app's top-level code, though never your tasks.
- **The second import is free and changes nothing.** If the file changed on disk, `import_module` still returns the old cached module from `sys.modules`. That's the root of the "reload doesn't pick up code changes with preload" caveat (spec/04 §5.2).
- **A missing module and a broken module look different.** `ModuleNotFoundError` means "not found on `sys.path`". Any other exception (`SyntaxError`, `NameError`, ...) means the module was found, but its own code crashed while importing. `load_app` reports both with their tracebacks.

## Try it

```text
>>> import importlib, sys
>>> module = importlib.import_module("json")
>>> module
<module 'json' from '/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/json/__init__.py'>
>>> module.dumps([1, 2])
'[1, 2]'
>>> importlib.import_module("json") is module
True
>>> "json" in sys.modules
True
>>> spec = "json:dumps"
>>> module_name, _, attribute = spec.partition(":")
>>> module_name, attribute
('json', 'dumps')
>>> getattr(importlib.import_module(module_name), attribute)
<function dumps at 0x7e56a3deb950>
>>> importlib.import_module("no_such_module_kiln")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …(frames inside importlib trimmed)…
ModuleNotFoundError: No module named 'no_such_module_kiln'
>>> getattr(module, "no_such_function")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
AttributeError: module 'json' has no attribute 'no_such_function'
```

(Generated from [`snippets/importlib.repl`](snippets/importlib.repl) on Python 3.15.1, with traceback middles trimmed.)

- The repr shows *where* the module was found.
- Importing twice gives the very same object (`is` is `True`), because of the `sys.modules` cache.
- `"json:dumps".partition(":")` splits a spec exactly the way `load_app` does.

## Check yourself

1. Write the two lines that turn the spec `"examples.demo:app"` into the `app` object.
   <details><summary>Answer</summary>

   ```python
   module = importlib.import_module("examples.demo")
   app = getattr(module, "app")
   ```
   (Kiln gets the two parts with `spec.partition(":")` first.)

   </details>
2. You edit `examples/demo.py` and call `importlib.import_module("examples.demo")` again in the same process. Do you get the new code?
   <details><summary>Answer</summary>

   No. The module is already in `sys.modules`, so the cached old version is returned and the file isn't read again. A new process (a new worker, without preload) is the reliable way to get new code.

   </details>
3. `load_app` fails with `ModuleNotFoundError: No module named 'examples'`. Where would you look?
   <details><summary>Answer</summary>

   At `sys.path`: the folder containing `examples/` isn't on it. Run from the repo root, or add the folder to `app.pythonpath` in the config.

   </details>
