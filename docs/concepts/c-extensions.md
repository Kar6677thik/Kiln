# C extensions

> **Milestone:** K4 · **Read before:** [import-system](import-system.md), [free-threading](free-threading.md) · **Reading time:** ~12 min
> **Terms introduced:** **C extension**, **shared object (`.so`)**, **`PyInit_*`**, **single-phase initialization**, **multi-phase initialization**, **module slot**, **`Py_mod_gil`**, **ABI tag**

## One-sentence summary

A **C extension** is a Python module written in C and compiled into a **shared object** (`.so`) file; when Python imports it, it calls the module's `PyInit_<name>` function, and the *slots* that function declares (such as `Py_mod_gil`) decide whether a free-threaded Python must turn its GIL back on.

## Why you should care

The free-threaded build's biggest trap is an extension that doesn't declare `Py_mod_gil`: importing it re-enables the GIL for the whole process (research [§2.9](../research/python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver), [free-threading](free-threading.md)). To see that happen on purpose, and to test `gilwatch`, K4 builds two ~30-line extensions that differ by exactly one line. This primer explains what those lines mean. It also sets up K8, where Kiln's hot path moves into a compiled (Rust) extension.

## The mental model

**Analogy:** a C extension is a power tool with a plug. `import` plugs it in: Python reads the label on the plug (the slots) to learn what the tool needs, such as "I need the whole kitchen to myself" (needs the GIL) or "I'm fine sharing".

**Where the analogy fails:** a tool with *no* label isn't treated as "fine": CPython assumes the worst for anything that doesn't declare itself.

```
 import kiln_gil_labs.with_gil
   │ finder locates   with_gil.cpython-315t-x86_64-linux-gnu.so     (ABI tag = 315t)
   │ loader: dlopen(.so), call PyInit_with_gil()
   │   multi-phase: returns a PyModuleDef with slots:
   │     { Py_mod_exec, ... }                         ← with_gil.c: no Py_mod_gil slot
   │     { Py_mod_gil, Py_MOD_GIL_NOT_USED }          ← without_gil.c adds this one line
   ▼
 free-threaded CPython: slot missing → enable the GIL, warn once; present → GIL stays off
```

## See it for yourself

[`snippets/c-extensions_1.py`](snippets/c-extensions_1.py) prints the extension file suffix of the running build, checks the C headers are installed, shows where two standard-library C modules come from, and checks the GIL after importing them:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/c-extensions_1.py
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh run docs/concepts/snippets/c-extensions_1.py
```

```text
extension file suffix (EXT_SUFFIX): .cpython-315-x86_64-linux-gnu.so
Python.h present: True
_json     -> (built into the interpreter)
_dbm      -> _dbm.cpython-315-x86_64-linux-gnu.so
GIL enabled after those imports: True
```

```text
extension file suffix (EXT_SUFFIX): .cpython-315t-x86_64-linux-gnu.so
Python.h present: True
_json     -> (built into the interpreter)
_dbm      -> _dbm.cpython-315t-x86_64-linux-gnu.so
GIL enabled after those imports: False
```

(3.15.1, then 3.15.1t.)

- The **ABI tag** in the file name, `cpython-315` vs `cpython-315t`, says which interpreter build the `.so` was compiled for. A `.so` built for one won't be loaded by the other. That's why K4 builds the test extensions once per interpreter.
- `_json` is compiled *into* the interpreter binary in these builds; `_dbm` is a separate `.so` file.
- On 3.15t the GIL stayed off: standard-library extensions declare `Py_MOD_GIL_NOT_USED`. msgpack 1.2.4, a third-party extension installed for K3, also kept it off.

**The re-enabling demo itself needs a C compiler**, and this machine doesn't have one yet (`gcc` is missing; installing `build-essential` needs the learner's `sudo`). The headers *are* installed for 3.14, 3.15 and 3.15t (gate G4.7). Once `build-essential` is installed, `./scripts/dev.sh lab k4-gil` builds `labs/k4/gil_reenabler/` and the K4 lab report shows the real before/after transcript and CPython's warning text (gate G4.3).

## How it really works

- **What's in a `.so`.** Machine code for one platform, plus a table of exported symbols. Python's extension loader opens the file with `dlopen(3)` and looks up `PyInit_<modulename>`, whose name must match the module's last name component.
- **Single-phase initialization (the old way).** `PyInit_foo` builds and returns a finished module object (`PyModule_Create(&def)`). Simple, but the module's state is effectively global: such modules can't be loaded into subinterpreters ([subinterpreters](subinterpreters.md)), and a free-threaded CPython always treats them as needing the GIL.
- **Multi-phase initialization (PEP 489).** `PyInit_foo` returns only a *description*, `PyModuleDef_Init(&def)`; CPython creates the module and then runs the functions listed in the definition's **slots**. Slots are `{id, value}` pairs:
  - `Py_mod_exec`: a function that fills in the module (adds functions, constants).
  - `Py_mod_multiple_interpreters`: whether it works in subinterpreters, with or without a per-interpreter GIL.
  - **`Py_mod_gil`** (3.13+): `Py_MOD_GIL_NOT_USED` means "I don't rely on the GIL"; the default `Py_MOD_GIL_USED` means "I do".
- **K4's two test extensions** (`labs/k4/gil_reenabler/`), each exporting one function `add(a, b)`:

```c
static PyModuleDef_Slot with_gil_slots[] = {
    {Py_mod_exec, with_gil_exec},
    {0, NULL}                                   /* no Py_mod_gil: "I need the GIL" */
};

static PyModuleDef_Slot without_gil_slots[] = {
    {Py_mod_exec, without_gil_exec},
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},          /* the one extra line */
    {0, NULL}
};
```

  The names are easy to misread: **`with_gil` *needs* the GIL** (the legacy situation); **`without_gil` runs without it**.
- **What happens on import (free-threaded build).** In `Python/import.c`, after creating a module from its definition, CPython checks the `Py_mod_gil` slot. If the module needs the GIL and it's off, CPython turns it on (unless `PYTHON_GIL=0`), and emits a `RuntimeWarning` naming the module once. It never turns it back off.
- **Building.** `setuptools` compiles each `.c` file with the interpreter's headers (`sysconfig.get_paths()["include"]`, where `Python.h` lives) and its compiler flags, and names the result with the interpreter's `EXT_SUFFIX`. `uv sync --group lab-k4` runs that build inside each interpreter's own venv.

## How Kiln uses it

- `labs/k4/gil_reenabler/`: the two extensions above, with a line-by-line `README.md` for readers who have never seen C.
- `src/kiln/worker/gilwatch.py`: names `kiln_gil_labs.with_gil` as the module whose import re-enabled the GIL, with the import chain that led to it.
- K8 replaces Kiln's hot path with a compiled Rust extension (PyO3), which must declare free-threading support the same way.

## Common misconceptions

- ❌ **Myth:** "C extensions always hold the GIL, so they're never parallel."
  ✅ **Reality:** a C extension can release the GIL around long work (`Py_BEGIN_ALLOW_THREADS`), and on the free-threaded build one that declares `Py_MOD_GIL_NOT_USED` never needs it.
- ❌ **Myth:** "a `.so` built for Python 3.15 works on 3.15t."
  ✅ **Reality:** the free-threaded build has a different ABI (object layout, refcount fields). The `t` in the ABI tag keeps the wrong file from even being found.
- ❌ **Myth:** "if an extension doesn't mention the GIL, it's neutral."
  ✅ **Reality:** a missing `Py_mod_gil` slot means "I need the GIL", and importing it turns the GIL on for the whole process.

## Check yourself

1. What must a C extension declare to keep the GIL off on 3.15t, and what happens if it doesn't?
   <details><summary>Answer</summary>

   It must use multi-phase initialization and include the slot `{Py_mod_gil, Py_MOD_GIL_NOT_USED}`. Without it, importing the module makes CPython enable the GIL for the rest of the process and emit a one-time `RuntimeWarning` naming the module (unless `PYTHON_GIL=0` forces the GIL off).

   </details>
2. Why does `labs/k4/gil_reenabler` get built separately for 3.15 and 3.15t?
   <details><summary>Answer</summary>

   Each build has its own ABI, recorded in the file name (`cpython-315` vs `cpython-315t`). A `.so` compiled against one build's headers can't be loaded by the other.

   </details>
3. Predict: a single-phase extension imported inside a subinterpreter. What happens?
   <details><summary>Answer</summary>

   An `ImportError`: single-phase modules keep process-global state and can't be loaded into a subinterpreter. Kiln's interpreters executor reports it as `error.type = "ImportError"` and logs `interp_import_failed`.

   </details>

## Go deeper

- [Extending and Embedding the Python Interpreter](https://docs.python.org/3.15/extending/index.html): the official tutorial.
- [PEP 489](https://peps.python.org/pep-0489/): multi-phase extension module initialization.
- [C API: module slots, `Py_mod_gil`](https://docs.python.org/3.15/c-api/module.html#c.Py_mod_gil) and the [free-threading extensions HOWTO](https://docs.python.org/3.15/howto/free-threading-extensions.html).
