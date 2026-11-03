# Modules and Imports

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [running Python and the REPL](running-python-and-the-repl.md), [functions](functions.md) · **Reading time:** ~12 min
> **Terms introduced:** **import**, **package**, **`__init__.py`**, **`sys.path`**, **`sys.modules`**, **standard library**, **src layout**, **`ModuleNotFoundError`**

## What it looks like

```python
import json  # the whole module, used as json.dumps(...)
from pathlib import Path  # one name out of a module
import subprocess as sp  # the whole module under a shorter name
from kiln.bench import records  # a module inside Kiln's own package
```

## What it means

A **module** is a `.py` file whose definitions (functions, classes, variables) other code can use. To **import** a module is to load it and get a name that refers to it.

- `import json` finds the module `json`, runs it (the first time only), and binds the name `json` to the module object. You then reach inside with a dot: `json.dumps(...)`.
- `from pathlib import Path` imports the module but binds only the name `Path` in your file.
- `import subprocess as sp` gives the module a different local name.

A **package** is a folder of modules. It's marked by a special file called **`__init__.py`**, whose code runs the first time anything imports the package. Dots in an import path walk into folders: `from kiln.bench import records` means "folder `kiln`, sub-folder `bench`, file `records.py`".

```
src/
  kiln/                 ← package "kiln"
    __init__.py         ← runs on "import kiln"
    cli.py              ← module "kiln.cli"
    bench/              ← package "kiln.bench"
      __init__.py
      records.py        ← module "kiln.bench.records"
```

### Where does Python look? `sys.path`

**`sys.path`** is a list of folders. On `import x`, Python searches them in order for `x.py` or a folder `x/`. The first entry is the folder of the script you ran (or the current folder for `python -m` and the REPL). The rest are the **standard library** (the modules that ship with Python: `json`, `os`, `pathlib`…) and the virtual environment's installed packages.

### Imports happen once: `sys.modules`

After the first import, Python stores the module in a dict called **`sys.modules`**. A second `import` of the same name returns the stored module **without running its code again**. That's why a package's `__init__.py` runs once per process.

### Kiln's src layout

Kiln's code lives in `src/kiln/`, not in `kiln/` at the top of the repo. This is the **src layout** ([spec/08 §1.1](../spec/08-repo-layout-and-tooling.md#11-why-the-src-layout)). Because `src/` is *not* the current folder, `import kiln` can only find the copy that `uv sync` installed into the virtual environment. That way, tests always exercise the package the way a real user would get it.

## Why people use it

- **Organisation.** One file per topic (`framing.py`, `supervisor.py`) keeps each file small enough to read.
- **Reuse.** The standard library gives you hundreds of ready-made modules. Kiln's runtime uses *only* those ([ADR-005](../decisions.md#adr-005)).
- **Namespaces.** `json.dumps` and `pickle.dumps` can't clash, because each lives in its own module.

## How Kiln uses it

- [`src/kiln/__init__.py`](../../src/kiln/__init__.py) is the package's front door. It defines `__version__`. From K1 it also re-exports `App`, so users can write `import kiln; kiln.App(...)`.
- [`src/kiln/cli.py`](../../src/kiln/cli.py) imports [`src/kiln/doctor.py`](../../src/kiln/doctor.py) to run `kiln doctor`.
- `tests/unit/test_import_boundaries.py` reads every `import` statement in `src/kiln/` (without running anything) and fails if a package imports one it isn't allowed to. For example, the master must never import worker code ([spec/01 §5](../spec/01-architecture.md#5-package-layout-module-map)).

## Gotchas

- **Importing runs code.** Everything at the top level of a module runs on first import. In the example below, importing `shapes` prints a line. Kiln keeps top-level code to definitions only. In K2 you'll see real programs break because of import-time side effects.
- **Name your files carefully.** A file called `json.py` in your folder **shadows** (hides) the standard `json` module, because the script's folder is searched first.
- **Module names can't contain dashes.** `import my-file` is a syntax error. That's why Kiln's module files use `snake_case` ([spec/08 §6](../spec/08-repo-layout-and-tooling.md#6-naming-conventions)).
- **Circular imports.** If `a.py` imports `b.py` and `b.py` imports `a.py`, one of them sees the other half-loaded. The import-boundary test helps prevent this.

## Try it

```text
>>> import math
>>> math.sqrt(16)
4.0
>>> from math import pi
>>> pi
3.141592653589793
>>> import json as j
>>> j.dumps({"workers": 4})
'{"workers": 4}'
>>> import sys
>>> "json" in sys.modules
True
>>> import kiln_does_not_exist
Traceback (most recent call last):
  File "<console>", line 1, in <module>
ModuleNotFoundError: No module named 'kiln_does_not_exist'
```

A **`ModuleNotFoundError`** means no folder on `sys.path` contained that name. Usually that's a typo, or the package isn't installed in the environment you're using.

Now a real package. [`snippets/shapes/`](snippets/shapes/__init__.py) is a folder with `__init__.py` and `circle.py`, and [`modules-and-imports_1.py`](snippets/modules-and-imports_1.py) imports it:

```text
(wsl) $ python3.15 docs/python/snippets/modules-and-imports_1.py
shapes package imported
area(2) = 12.57
shapes came from: shapes/__init__.py
first place Python searched: snippets
'shapes' in sys.modules: True
```

- `shapes package imported` appears only **once**, even though the script imports `shapes` twice. The second import came from `sys.modules`.
- The first folder on `sys.path` is `snippets`, the folder containing the script, which is why `import shapes` found the folder next to it.

## Check yourself

1. What's the difference between `import json` and `from json import dumps`?
   <details><summary>Answer</summary>

   Both load the `json` module. The first binds the name `json`, and you call `json.dumps(...)`. The second binds only `dumps`, and you call `dumps(...)` directly.

   </details>
2. A module prints "loading!" at its top level. You import it three times in one program. How many times does "loading!" appear?
   <details><summary>Answer</summary>

   Once. After the first import the module is cached in `sys.modules`, and later imports reuse it without running its code again.

   </details>
3. What makes a folder a package?
   <details><summary>Answer</summary>

   An `__init__.py` file inside it. Its code runs when the package is first imported.

   </details>
4. Why does Kiln put its code in `src/kiln/` instead of `kiln/`?
   <details><summary>Answer</summary>

   With the src layout, `import kiln` can't accidentally pick up the source folder from the current directory. It has to use the installed package, so tests see Kiln exactly as a user would.

   </details>
