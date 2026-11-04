# Global Interpreter State: Any Library Can Change It

> **Milestone:** K3 · **Read before:** [garbage-collector](garbage-collector.md), [import-system](import-system.md) · **Reading time:** ~13 min
> **Terms introduced:** **global interpreter state**, **shim**, **import hook**, **audit hook**, **detection layer**

## One-sentence summary

Some settings belong to the whole interpreter (whether the GC is enabled, signal handlers, the recursion limit, the locale, logging configuration), so any library you import can change them silently, and catching *who* did it takes several complementary detectors because no single hook sees every way of changing them.

## Why you should care

Instagram disabled the GC, and it came back. A debugger finally showed that `msgpack` called `gc.enable()` when it loaded (research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)). Nothing in their own code had changed. That's the general lesson: **your process's global settings are shared with every library in it**. K3 builds a detector (`src/kiln/memory/gcwatch.py`) that names the culprit when someone re-enables the GC in a Kiln worker. (Today's msgpack 1.2.4 doesn't do it any more: gate G3.1 checked 3.13, 3.14, 3.15 and 3.15t, so the lab uses a stand-in "rogue" library.)

## The mental model

**Analogy:** a shared office thermostat. You set it to 18 °C and leave; when you come back it says 24 °C. Anyone could have turned it. You can (A) replace the knob with one that logs who turned it, (B) check the temperature every time someone enters and leaves the room, or (C) glance at it every few minutes. Each catches some culprits and misses others.

**Where the analogy fails:** some "people" don't use the knob at all. C code can change the GC state by calling `PyGC_Enable()` directly, without going through the Python function you replaced.

```
                  who can flip gc.isenabled()?            which layer sees it
  Python code ── gc.enable() ──────────────────────────▶  A shim (exact stack)  + B (if during import) + C
  Cython code ── looks up gc.enable at call time ──────▶  A shim                + B + C
  C extension ── PyGC_Enable() directly (C API) ───────▶  ✗ A                   + B (if during import) + C
  later, at runtime, any of the above ─────────────────▶  A (if Python) + C poll / collection sentinel
  anything at all ────────────────────────────────────▶  D: gdb breakpoint on PyGC_Enable (manual)
```

## See it for yourself

[`snippets/global-interpreter-state_1.py`](snippets/global-interpreter-state_1.py) writes two throwaway libraries: `pylib` calls `gc.enable()`, and `clib` calls the C function `PyGC_Enable()` through `ctypes`, as a C extension would. It watches with three layers: (A) a shim replacing `gc.enable`, (B) a `sys.meta_path` import hook comparing `gc.isenabled()` before and after each import, and (C) a plain check of `gc.isenabled()`:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/global-interpreter-state_1.py
```

```text
import pylib (GC disabled before):
  A shim: gc.enable() called from pylib.py:2
  B import hook: importing 'pylib' turned the GC on
  C poll: gc.isenabled() is now True
import clib (GC disabled before):
  B import hook: importing 'clib' turned the GC on
  C poll: gc.isenabled() is now True
```

(Python 3.15.1; 3.13.17 and 3.15.1t print the same.)

- **`pylib`:** all three layers fire, and the shim gives the exact file and line.
- **`clib`:** the shim is **silent**. `PyGC_Enable()` never calls the Python-level `gc.enable` attribute we replaced. The import hook still names the module, and the poll notices the state changed.

## How it really works

- **What counts as global interpreter state:** `gc` enabled/thresholds/frozen objects, signal handlers (`signal.signal`), `sys.setrecursionlimit`, `sys.setswitchinterval`, `sys.path`, `sys.modules`, warning filters, the root logger's handlers, `locale`, `os.environ` and the working directory, `random`'s global seed. They're per-process (or per-interpreter), and any module can change them.
- **Layer A, the shim (monkeypatching).** Python finds `gc.enable` by looking up the attribute `enable` on the `gc` module *at call time*. Replacing that attribute with a wrapper that records `traceback.extract_stack()` and then calls the original catches every caller that goes through the attribute: Python code, and Cython-compiled code (K3 spec §5.7 says Cython looks it up the same way; that claim is gate G3.7). It misses C code calling `PyGC_Enable()`, and code that saved a reference to the original function before the shim was installed (`from gc import enable`). See [monkeypatching](../python/monkeypatching.md).
- **Layer B, the import hook.** `sys.meta_path` is the list of finders the import system asks to locate a module ([import-hooks-meta-path](../python/import-hooks-meta-path.md)). A finder at position 0 can ask the *other* finders for the module's spec and wrap its loader's `exec_module`, recording `gc.isenabled()` before and after the module's code runs. That catches flips by *anything*, C included, as long as it happens **while a module is being imported**. With nested imports, the flip belongs to the innermost module whose own before/after shows it.
- **Layer C, poll plus collection sentinel.** Kiln's worker heartbeat thread already wakes every interval; it can check `gc.isenabled()`. A `gc.callbacks` function can also notice a collection *starting* while the GC was supposed to be off. Layer C catches runtime flips from any source, but it only gives a time window (and which job was running), not the culprit.
- **Layer D, the debugger.** Instagram's method: run the process under `gdb` with breakpoints on `PyGC_Enable` and `gc_enable_impl`, then `bt` (C stack) and `py-bt` (Python stack) at the hit. It sees everything, but it's manual. (gdb isn't installed on this machine yet; the K3 lab report shows real output once it is.)
- **A rejected alternative: audit hooks.** `sys.addaudithook` gets an `import` event, but it fires *before* the module executes, so it can't see the state *after*. It would need pairing each event with the next one, which nested imports make fragile.

## How Kiln uses it

- `src/kiln/memory/gcwatch.py`: `GcWatch(expect_disabled=..., on_event=...)` with `install()` (layers A, B and the C sentinel), `poll()` (C, called by the heartbeat thread) and `uninstall()`. Events are logged as WARNING `gc_reenabled` with `layer`, `module`, `job_id` and the top of the stack. It's stdlib-only, so K7 can load it by file path.
- It's installed only when `labs.gc_watch = true` and the `gc_mode` expects the GC to be off.
- `labs/k3/rogue_gc_enabler.py` is the stand-in culprit, with modes `python`, `capi` and `runtime` chosen by `KILN_ROGUE_MODE`.
- Code tag: `CONCEPT(global-interpreter-state)`.

## Common misconceptions

- ❌ **Myth:** "if I call `gc.disable()` at startup, the GC stays off."
  ✅ **Reality:** only until anything calls `gc.enable()` or `PyGC_Enable()`. Use `gc.set_threshold(0)` if you need it to stay off ([garbage-collector](garbage-collector.md)).
- ❌ **Myth:** "replacing `gc.enable` catches every re-enable."
  ✅ **Reality:** C code never goes through the Python attribute. The snippet's `clib` slipped past the shim.
- ❌ **Myth:** "`gc.callbacks` tells me when someone calls `gc.enable()`."
  ✅ **Reality:** callbacks fire around *collections*, not around enable/disable calls (errata E-14). Kiln uses them only to notice a collection that shouldn't have happened.

## Check yourself

1. A C extension calls `PyGC_Enable()` in its module initialisation. Which layers catch it?
   <details><summary>Answer</summary>

   B (it happened during an import, so the before/after comparison names the module), C (the poll or the sentinel notices later), and D (gdb). Not A: the C call bypasses the Python attribute.

   </details>
2. The same extension instead calls `PyGC_Enable()` inside a function your task calls later. Which layers catch it now?
   <details><summary>Answer</summary>

   C (with the time window and the running job, not the culprit) and D. A misses the C call, and B only watches imports.

   </details>
3. Why does `from gc import enable` before the shim is installed defeat layer A?
   <details><summary>Answer</summary>

   It copies a reference to the original function into the importing module. Replacing the `gc` module's attribute later doesn't change that reference, so calls through it never reach the shim.

   </details>
4. Name three pieces of global state, other than the GC, that a library could change behind your back.
   <details><summary>Answer</summary>

   Any three of: signal handlers, `sys.setrecursionlimit`, `sys.setswitchinterval`, the root logger's handlers, warning filters, `locale`, `os.environ`, the working directory, `random`'s seed, `sys.path`.

   </details>

## Go deeper

- [Python docs: `gc.callbacks`](https://docs.python.org/3.15/library/gc.html#gc.callbacks), [`sys.meta_path`](https://docs.python.org/3.15/library/sys.html#sys.meta_path), [`sys.addaudithook`](https://docs.python.org/3.15/library/sys.html#sys.addaudithook).
- [Python docs: `PyGC_Enable` (C API)](https://docs.python.org/3.15/c-api/gcsupport.html#c.PyGC_Enable).
- Instagram Engineering, "Dismissing Python Garbage Collection at Instagram" (research §8): the msgpack story.
- [errata E-03 and E-14](../research/errata.md): what today's msgpack does, and what `gc.callbacks` can't see.
