# `sys.getrefcount` and `sys._is_immortal`

> **First used in:** see the [ledger](README.md) (milestone K3) · **Needs:** [classes-and-objects](classes-and-objects.md) · **Reading time:** ~5 min
> **Terms introduced:** **`sys.getrefcount`**, **`sys._is_immortal`**

## What it looks like

```python
import sys

x = object()
sys.getrefcount(x)      # 2: the name x, plus the temporary reference for the argument
sys._is_immortal(None)  # True (Python 3.14+)
```

## What it means

- `sys.getrefcount(obj)` returns the object's current reference count, `ob_refcnt` ([refcounting](../concepts/refcounting.md)). The number is **one higher** than you expect, because passing `obj` to the function creates a reference that lasts for the call.
- For **immortal** objects it returns a fixed marker value, not a count: `3221225472` on 3.14 and 3.15, `4294967295` on 3.13 (measured).
- `sys._is_immortal(obj)` (new in 3.14) says directly whether an object is immortal ([immortal-objects](../concepts/immortal-objects.md)). The leading underscore means "CPython-specific, may change".

## Why people use it

To *see* reference counting: debugging leaks (a count that keeps growing), checking that a cache doesn't hold extra references, and, in K3, proving which objects are immortal and therefore safe to read in a forked worker without copying pages.

## How Kiln uses it

- Gate G3.6 and the snippets in [immortal-objects](../concepts/immortal-objects.md) use `sys._is_immortal` to record which objects are immortal on each interpreter.
- `src/kiln/labs/preload.py` builds the "immortal-heavy" graph from values that these checks show are immortal.

## Gotchas

- **The +1.** `getrefcount(x)` counts the argument. A brand-new object bound to one name reports 2.
- **Immortal values mean nothing.** `getrefcount(None)` is a marker, not "None is used three billion times".
- **Free-threaded builds** (3.15t) split the count into two fields (biased reference counting, see [refcounting](../concepts/refcounting.md)); `getrefcount` reports their combination, so don't compare exact numbers across builds.
- **`sys._is_immortal` doesn't exist on 3.13.** Guard it with `hasattr(sys, "_is_immortal")`.

## Try it

```text
>>> import sys
>>> x = object()
>>> sys.getrefcount(x)
2
>>> y = x
>>> sys.getrefcount(x)
3
>>> holder = [x, x, x]
>>> sys.getrefcount(x)
6
>>> del y, holder
>>> sys.getrefcount(x)
2
>>> sys.getrefcount(None)
3221225472
>>> sys._is_immortal(None), sys._is_immortal(x)
(True, False)
>>> sys._is_immortal(1024), sys._is_immortal(int("1025"))
(True, False)
```

(Generated from [`snippets/sys-getrefcount.repl`](snippets/sys-getrefcount.repl) on Python 3.15.1.)

Each new name and each list slot adds one. Deleting them brings the count back to 2. On 3.15 the small int cache reaches 1024, so `1024` is immortal and a freshly made `1025` isn't.

## Check yourself

1. Predict: `a = []; b = {"k": a}; sys.getrefcount(a)`.
   <details><summary>Answer</summary>

   `3`: the name `a`, the dict value, and the argument.

   </details>
2. Why does `sys.getrefcount(None)` print such a huge number?
   <details><summary>Answer</summary>

   `None` is immortal (PEP 683). Its refcount field holds a fixed marker value that `Py_INCREF`/`Py_DECREF` never change.

   </details>
3. How would you check immortality on Python 3.13, which has no `sys._is_immortal`?
   <details><summary>Answer</summary>

   Compare `sys.getrefcount(obj)` with the 3.13 marker `4294967295` (measured for an immortal string on 3.13.17). That relies on an implementation detail, so treat it as a debugging trick, not an API.

   </details>
