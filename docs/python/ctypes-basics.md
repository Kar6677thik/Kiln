# `ctypes` basics

> **First used in:** see the [ledger](README.md) (milestone K3; first seen in K1's `labs.hold_gil`) · **Needs:** [modules-and-imports](modules-and-imports.md) · **Reading time:** ~7 min
> **Terms introduced:** **`ctypes`**, **`ctypes.CDLL`**, **`ctypes.PyDLL`**, **`ctypes.pythonapi`**

## What it looks like

```python
import ctypes

libc = ctypes.CDLL(None)                 # the C library already loaded into this process
libc.strlen(b"kiln")                     # call the C function strlen → 4
ctypes.pythonapi.PyGC_Enable()           # call a function of CPython's own C API
```

## What it means

`ctypes` lets Python call functions in compiled C libraries without writing any C.
- **`ctypes.CDLL(name)`** loads a shared library (`None` means "the libraries already in this process", including libc) and gives access to its functions as attributes. Calls **release the GIL** while the C function runs.
- **`ctypes.PyDLL(name)`** is the same, but calls **keep holding the GIL** and check for Python errors afterwards. That's required for functions of the Python C API.
- **`ctypes.pythonapi`** is a ready-made `PyDLL` for the running interpreter's own C API (`PyGC_Enable`, `PyGC_IsEnabled`, …).
- By default ctypes assumes a C function takes and returns `int`. For anything else, set `func.argtypes` and `func.restype`, or the values will be garbage.

## Why people use it

- Calling an OS or library function Python doesn't wrap.
- Reaching CPython's C API from Python code, which is how the K3 lab imitates a C extension.
- Quick experiments before writing a real extension module (K8 does that properly in Rust).

## How Kiln uses it

- K1's `labs.hold_gil` uses `ctypes.PyDLL(None).sleep(...)` to call C `sleep` *while holding the GIL*, so the heartbeat thread can't run (gate G1.6).
- K3's `labs/k3/rogue_gc_enabler.py`, mode `capi`, calls `ctypes.pythonapi.PyGC_Enable()`: it re-enables the GC exactly as a C extension would, bypassing a monkeypatched `gc.enable` ([global-interpreter-state](../concepts/global-interpreter-state.md)). Gate G3.8 confirmed `PyGC_Enable` exists on 3.13, 3.14, 3.15 and 3.15t.

## Gotchas

- **Wrong types crash the process.** ctypes can't check a C function's real signature. A wrong `argtypes`/`restype` can corrupt memory or segfault, with no Python exception.
- **CDLL vs PyDLL matters.** Calling a Python C API function through `CDLL` releases the GIL while calling into the interpreter, which is undefined behaviour.
- **It's CPython-specific and platform-specific.** `CDLL(None)` works on Linux; library names differ between systems.

## Try it

```text
>>> import ctypes, gc
>>> libc = ctypes.CDLL(None)
>>> libc.getpid() == __import__("os").getpid()
True
>>> libc.strlen(b"kiln")
4
>>> ctypes.pythonapi.PyGC_IsEnabled()
1
>>> gc.disable()
>>> ctypes.pythonapi.PyGC_IsEnabled()
0
>>> ctypes.pythonapi.PyGC_Enable()
0
>>> gc.isenabled()
True
>>> type(ctypes.pythonapi).__name__, type(libc).__name__
('PyDLL', 'CDLL')
```

(Generated from [`snippets/ctypes-basics.repl`](snippets/ctypes-basics.repl) on Python 3.15.1.)

C's `getpid` agrees with `os.getpid`. `PyGC_Enable()` returned `0`, which per the C API docs means "the GC was disabled before this call", and the Python side now sees the GC enabled.

## Check yourself

1. Why does `labs.hold_gil` use `PyDLL` and not `CDLL` to call `sleep`?
   <details><summary>Answer</summary>

   `CDLL` releases the GIL during the call, so other threads would keep running. `PyDLL` keeps the GIL, which is exactly the "C code holding the GIL" situation the lab needs.

   </details>
2. What does the `0` returned by `ctypes.pythonapi.PyGC_Enable()` mean?
   <details><summary>Answer</summary>

   The previous state: the GC was disabled before the call (it returns 1 if it was already enabled).

   </details>
3. Why can a mistake with ctypes crash Python instead of raising an exception?
   <details><summary>Answer</summary>

   ctypes calls machine code directly with the types you declared. If they're wrong, the C function reads or writes the wrong memory, and nothing in Python can check that beforehand.

   </details>
