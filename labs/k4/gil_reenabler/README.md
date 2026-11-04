# `kiln-gil-labs`: two C extensions, one line apart

> **Milestone:** K4 · **Read first:** [concepts/c-extensions.md](../../../docs/concepts/c-extensions.md), [concepts/free-threading.md](../../../docs/concepts/free-threading.md) · **Reading time:** ~10 min

This folder is a tiny, separate Python project with two **C extensions**, modules written in C and compiled into `.so` files. They are identical except for one line:

| module | the one line | on a free-threaded Python (3.15t) |
|---|---|---|
| `kiln_gil_labs.with_gil` | *(no `Py_mod_gil` slot)* | importing it **turns the GIL back on** for the whole process |
| `kiln_gil_labs.without_gil` | `{Py_mod_gil, Py_MOD_GIL_NOT_USED}` | importing it leaves the GIL off |

The names describe what each module *needs*: `with_gil` needs the GIL (the legacy situation), `without_gil` runs without it. K4's `gilwatch` must catch the first and ignore the second.

## Building it

It needs a C compiler, which isn't installed yet: `sudo apt install build-essential`. Then, once per interpreter (each gets its own correctly tagged `.so`, such as `with_gil.cpython-315t-x86_64-linux-gnu.so`):

```bash
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh sync --group lab-k4
(wsl) $ KILN_PY=3.15  ./scripts/dev.sh sync --group lab-k4
```

## `with_gil.c`, line by line

C has no `import`; it has `#include`, which pastes a header file in. Everything here is standard CPython C API.

```c
#define PY_SSIZE_T_CLEAN
#include <Python.h>
```
- `#define PY_SSIZE_T_CLEAN` must come before `Python.h`. It tells the argument-parsing functions to use `Py_ssize_t` (a size type as wide as a pointer) for lengths. It's a historical switch every modern extension sets.
- `#include <Python.h>` brings in every declaration of the C API: `PyObject`, `PyLong_FromLong`, the module structures. The headers come with the interpreter (`sysconfig.get_paths()["include"]`), different for 3.15 and 3.15t.

```c
static PyObject *
add(PyObject *module, PyObject *args)
{
    long a, b;
    if (!PyArg_ParseTuple(args, "ll", &a, &b)) {
        return NULL;
    }
    return PyLong_FromLong(a + b);
}
```
- `static` means "visible only inside this file". `PyObject *` is "a pointer to any Python object": every Python value, seen from C.
- A C function that Python calls receives the module and a tuple of arguments. `PyArg_ParseTuple(args, "ll", &a, &b)` converts the tuple into two C `long`s ("ll"); `&a` means "the address of a", so the function can write the value into it.
- If the arguments are wrong, `PyArg_ParseTuple` sets a `TypeError` and returns 0. Returning `NULL` from our function is how C code says "an exception is set: raise it".
- `PyLong_FromLong(a + b)` builds a new Python `int`.

```c
static PyMethodDef methods[] = {
    {"add", add, METH_VARARGS, "Return a + b."},
    {NULL, NULL, 0, NULL},
};
```
- The module's function table: the Python name, the C function, how it receives arguments (`METH_VARARGS`: as a tuple), and a docstring. C arrays don't know their own length, so the table ends with an all-zero "sentinel" row.

```c
static PyModuleDef_Slot slots[] = {
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {0, NULL},
};
```
- **Slots** are the multi-phase initialization (PEP 489) way for a module to describe itself: a list of `{id, value}` pairs, again ending with a zero row.
- `Py_mod_multiple_interpreters` says the module may be imported into subinterpreters that have their own GIL (K4's `interpreters` executor). Our module keeps no global state, so that is true.
- **What's missing is the point:** there is no `Py_mod_gil` entry. On a free-threaded build, CPython reads "no declaration" as "this module assumes a GIL protects it", and turns the GIL on before running it.

```c
static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    .m_name = "kiln_gil_labs.with_gil",
    .m_doc = "...",
    .m_size = 0,
    .m_methods = methods,
    .m_slots = slots,
};
```
- The module's definition. `PyModuleDef_HEAD_INIT` fills in bookkeeping fields. `.m_name = ...` is C's "designated initializer": set this field by name. `.m_size = 0` means the module keeps no per-module state.

```c
PyMODINIT_FUNC
PyInit_with_gil(void)
{
    return PyModuleDef_Init(&module_def);
}
```
- When Python imports `kiln_gil_labs.with_gil`, it opens the `.so` file and looks for a function named `PyInit_` + the last part of the module name: `PyInit_with_gil`. `PyMODINIT_FUNC` marks it as exported with the right calling convention.
- Returning `PyModuleDef_Init(&module_def)` (instead of a finished module) is what makes this *multi-phase* initialization: CPython reads the slots, decides about the GIL, then builds the module.

## `without_gil.c`: the one extra line

```c
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
```
- "This module doesn't rely on the GIL." It's a promise: our `add` touches only its own local variables, so it is true. On a GIL build the slot is ignored.

## `pyproject.toml`

- `build-backend = "setuptools.build_meta"`: setuptools knows how to call the C compiler with the interpreter's include paths and flags.
- `[tool.setuptools] ext-modules = [...]` (gate G4.7b) lists each extension: its full module name and its `.c` file. `src/kiln_gil_labs/__init__.py` makes `kiln_gil_labs` a package so both extensions live inside it.
