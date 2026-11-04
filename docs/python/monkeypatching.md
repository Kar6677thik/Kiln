# Monkeypatching

> **First used in:** see the [ledger](README.md) (milestone K3) · **Needs:** [modules-and-imports](modules-and-imports.md), [functions](functions.md) · **Reading time:** ~6 min
> **Terms introduced:** **monkeypatching**, **attribute lookup at call time**

## What it looks like

```python
import gc

original = gc.enable

def spy() -> None:
    print("someone called gc.enable()")
    original()

gc.enable = spy        # every later `gc.enable()` now calls spy
...
gc.enable = original   # put it back
```

## What it means

- **Monkeypatching** is replacing an attribute of a module, class or object *at runtime*, from outside it.
- It works because Python looks attributes up **at call time**. `gc.enable()` means "find the attribute named `enable` on the `gc` module object *now*, then call it". If you replaced the attribute, everyone who writes `gc.enable()` afterwards gets your version.
- It does **not** change references copied earlier: `from gc import enable` creates a new name bound to whatever `gc.enable` was at that moment.

## Why people use it

- **Tests:** replacing a function with a fake (`unittest.mock.patch`, pytest's `monkeypatch` fixture).
- **Instrumentation:** wrapping a function to log or time its calls without editing its source, which is exactly what K3's GC detector does.
- **Hot fixes** to third-party libraries (risky; prefer a real fix).

## How Kiln uses it

- `src/kiln/memory/gcwatch.py`, layer A: replaces `gc.enable` with a wrapper that records `traceback.extract_stack()` and then calls the original, so a library re-enabling the GC is caught with an exact stack. `uninstall()` puts the original back, and a test checks `gc.enable is original` afterwards ([global-interpreter-state](../concepts/global-interpreter-state.md)).
- K2's forkcheck rule FA006 *detects* monkeypatching in test code, because a patch applied in the parent isn't inherited by spawned children.

## Gotchas

- **Saved references bypass the patch.** `from gc import enable` executed *before* the patch keeps calling the original.
- **C code bypasses it.** A C extension calling `PyGC_Enable()` never looks at the `gc` module's attributes.
- **Always restore.** A patch left in place leaks into everything that runs later, including other tests. Use `try/finally` or a fixture.
- **Patches don't cross into new processes.** A spawned child imports a fresh, unpatched `gc` (forkcheck FA006).

## Try it

```text
>>> import gc
>>> original = gc.enable
>>> calls = []
>>> def spy():
...     calls.append("someone called gc.enable")
...     original()
... 
>>> gc.enable = spy
>>> gc.disable()
>>> gc.enable()
>>> calls, gc.isenabled()
(['someone called gc.enable'], True)
>>> from gc import enable as saved_before
>>> saved_before is spy
True
>>> gc.enable = original
>>> gc.enable is original
True
```

(Generated from [`snippets/monkeypatching.repl`](snippets/monkeypatching.repl) on Python 3.15.1.)

The spy recorded the call and still enabled the GC. `from gc import enable` *after* patching got the spy. Imported *before* patching, it would have been the original.

## Check yourself

1. A library did `from gc import enable` at import time, before your patch. Does your patch see its calls?
   <details><summary>Answer</summary>

   No. Its name `enable` is bound to the original function object; replacing `gc.enable` later doesn't change that binding.

   </details>
2. Why must a patch be restored at the end of a test?
   <details><summary>Answer</summary>

   The patched module is shared by the whole process. Leaving the patch in place changes the behaviour of every later test and of the code under test.

   </details>
3. Why can't monkeypatching `gc.enable` catch a C extension that calls `PyGC_Enable()`?
   <details><summary>Answer</summary>

   The C function changes the GC state directly; it never goes through the Python-level `gc` module attribute that was replaced.

   </details>
