# Free-threading (the "t" build)

> **Milestone:** K4 · **Read before:** [gil](gil.md), [threads](threads.md), [c-extensions](c-extensions.md) · **Reading time:** ~12 min
> **Terms introduced:** **free-threaded build**, **`Py_GIL_DISABLED`**, **`sys._is_gil_enabled()`**, **`PYTHON_GIL`**, **biased reference counting**, **GIL re-enabling**

## One-sentence summary

The **free-threaded build** of CPython (installed as `3.15t`) has no GIL, so threads run Python code in parallel, unless you import a C extension that hasn't declared it's safe without the GIL, which **quietly turns the GIL back on** for the whole process.

## Why you should care

Research [§2.9](../research/python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver) lists the facts: free-threading is officially supported since 3.14 (PEP 779), costs about 1% on macOS ARM and about 8% on x86-64 Linux single-threaded, and "importing a C extension that doesn't declare free-threading support turns the GIL back on". That last one is the trap: your service keeps running, a single warning is printed somewhere, and every benefit you deployed the `t` build for is gone. K4's `gilwatch` exists to name the module that did it.

## The mental model

**Analogy:** the kitchen from [gil](gil.md) now has a knife for every cook. But some old appliances (legacy C extensions) were built assuming only one cook works at a time. The moment you plug one in, the kitchen manager goes back to "one knife for everyone", for the rest of the day, and only mentions it once.

**Where the analogy fails:** the "plug in" is an `import`, and it happens once per process. The GIL can't be switched off again until the process restarts.

```
 python3.15t starts ──▶ GIL off (sys._is_gil_enabled() == False)
     │
     ├── import json, sqlite3, msgpack ...   each declares Py_mod_gil = NOT_USED ──▶ GIL stays off
     │
     └── import legacy_ext                   no Py_mod_gil slot
             └──▶ CPython: RuntimeWarning "The global interpreter lock (GIL) has been enabled
                  to load module ..." ──▶ GIL ON for the rest of the process
                  (unless PYTHON_GIL=0 forces it off: "running unsafe on purpose")
```

## See it for yourself

[`snippets/free-threading_1.py`](snippets/free-threading_1.py) reports the build, the GIL state, and what the `PYTHON_GIL` environment variable does to a new interpreter:

```bash
(wsl) $ KILN_PY=3.15t ./scripts/dev.sh run docs/concepts/snippets/free-threading_1.py
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/free-threading_1.py
```

```text
Py_GIL_DISABLED = 1 -> free-threaded build: True
PYTHON_GIL=0: a new interpreter starts with GIL enabled = False
PYTHON_GIL=1: a new interpreter starts with GIL enabled = True
```

```text
Py_GIL_DISABLED = 0 -> free-threaded build: False
PYTHON_GIL=0 on this build: exit code 1
  Fatal Python error: config_read_gil: Disabling the GIL is not supported by this build
```

(3.15.1t, then 3.15.1.)

- **`Py_GIL_DISABLED`** is a compile-time setting, readable with `sysconfig.get_config_var("Py_GIL_DISABLED")`: 1 means "this is a free-threaded build". It says nothing about whether the GIL is on *now*.
- **`sys._is_gil_enabled()`** says whether the GIL is on right now. On 3.15t it starts `False` (gate G4.2).
- **`PYTHON_GIL=1`** turns the GIL on in a free-threaded build from the start; **`PYTHON_GIL=0`** keeps it off even when a legacy extension is imported. On a GIL build, `PYTHON_GIL=0` is a fatal error: there is nothing to disable.

The importing-a-legacy-extension demo needs a C compiler to build the two test extensions (`labs/k4/gil_reenabler/`). This machine has none yet (`build-essential` needs the learner's `sudo`), so that transcript lives in the K4 lab report once it's built. What we can show already: standard-library extensions keep the GIL off ([c-extensions](c-extensions.md) snippet).

## How it really works

- **What replaced the GIL (PEP 703).**
  - **Biased reference counting:** each object remembers the thread that created it. That thread updates the refcount with a cheap, non-atomic increment; other threads use a separate, atomic "shared" counter. Most objects are only used by their creator, so most refcount updates stay cheap.
  - **Per-object locks:** containers like `dict` and `list` lock themselves during each operation ([thread-safety-of-builtins](thread-safety-of-builtins.md)).
  - **mimalloc** as the allocator, safe for concurrent use, and a GC that stops all threads ("stop the world") while it runs.
  - **Immortal objects** ([immortal-objects](immortal-objects.md)) need no refcount updates at all; 3.15t immortalizes more of them (interned strings, code constants).
- **The cost.** These mechanisms make single-threaded code a little slower: the research cites about 8% on x86-64 Linux. K4's lab measures it on this machine (sync executor, 1 worker, 3.15 vs 3.15t).
- **How an extension says "I'm safe".** A C extension using multi-phase initialization lists *slots*; `{Py_mod_gil, Py_MOD_GIL_NOT_USED}` declares that it doesn't rely on the GIL. If the slot is missing, a free-threaded CPython assumes the worst: it enables the GIL before running the module's code and emits a `RuntimeWarning` naming the module ([c-extensions](c-extensions.md)). Single-phase (legacy `PyModule_Create`) modules always count as "needs the GIL".
- **What's still not safe.** The research's examples: sharing one *iterator* between threads, and reading another thread's `frame.f_locals`. Also: anything *you* do in several steps ([data-races](data-races.md)).
- **Checking for it in a process.** `sys._is_gil_enabled()` (the leading underscore means "implementation detail", but it's the documented way). Kiln reports it in every `ready` and `heartbeat` message as `gil_enabled`.

### gilwatch's design (K4)

Knowing *that* the GIL came back isn't enough; you need *which import did it*. `src/kiln/worker/gilwatch.py` uses the same trick as K3's gcwatch layer B ([global-interpreter-state](global-interpreter-state.md)): a `sys.meta_path` finder wraps every module's loader, records `sys._is_gil_enabled()` before and after `exec_module`, and blames the innermost module whose import flipped it, together with the chain of imports that led there. A wrapper around `warnings.showwarning` captures CPython's own warning text to cross-check.

## How Kiln uses it

- `src/kiln/worker/gilwatch.py`: active only when `Py_GIL_DISABLED == 1`; logs `event=gil_reenabled module=... import_chain=...`, and `event=gil_forced_off` if `PYTHON_GIL=0` kept it off.
- `labs/k4/gil_reenabler/`: `with_gil` (no `Py_mod_gil` slot: *needs* the GIL) and `without_gil` (declares `Py_MOD_GIL_NOT_USED`).
- The K4 lab rejects any 3.15t benchmark run whose heartbeats report `gil_enabled = true`: such a run would silently measure the GIL build.

## Common misconceptions

- ❌ **Myth:** "the free-threaded build is just Python without a lock; everything else is the same."
  ✅ **Reality:** refcounting, the allocator, the GC and the built-in containers all work differently, which costs some single-threaded speed and changes which bugs show up.
- ❌ **Myth:** "if I run `python3.15t`, my program runs without the GIL."
  ✅ **Reality:** only until something imports a C extension without the `Py_mod_gil` declaration. Check `sys._is_gil_enabled()` *after* your imports.
- ❌ **Myth:** "no GIL means I need locks everywhere now."
  ✅ **Reality:** single built-in operations stay safe (per-object locks). You need locks for *compound* operations, which already needed them with the GIL.

## Check yourself

1. What's the difference between `sysconfig.get_config_var("Py_GIL_DISABLED")` and `sys._is_gil_enabled()`?
   <details><summary>Answer</summary>

   The first is a fixed property of the build ("was this CPython compiled free-threaded?"). The second is the current state of this process ("is the GIL on right now?"). A free-threaded build can have the GIL on, because of `PYTHON_GIL=1` or a legacy import.

   </details>
2. Your 3.15t service's throughput dropped to GIL-build levels after a dependency update, with no error. What's your first check, and which Kiln tool answers "who"?
   <details><summary>Answer</summary>

   Check `sys._is_gil_enabled()` after startup (Kiln shows `gil_enabled` in `stats`). If it's `True`, `gilwatch` names the module whose import enabled it, and the import chain that pulled it in.

   </details>
3. Predict: `PYTHON_GIL=0 python3.15 -c "print(1)"` on the normal build.
   <details><summary>Answer</summary>

   A fatal error, exit code 1: "Disabling the GIL is not supported by this build". The setting only exists on free-threaded builds.

   </details>

## Go deeper

- [Free-threading HOWTO (3.15)](https://docs.python.org/3.15/howto/free-threading-python.html): status, costs, known limitations.
- [PEP 703](https://peps.python.org/pep-0703/) (the design) and [PEP 779](https://peps.python.org/pep-0779/) (supported status).
- [C API: `Py_mod_gil`](https://docs.python.org/3.15/c-api/module.html#c.Py_mod_gil) and the [free-threading extensions HOWTO](https://docs.python.org/3.15/howto/free-threading-extensions.html).
