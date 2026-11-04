# functools.partial (pre-filling a function's arguments)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [functions](functions.md), [closures](closures.md) · **Reading time:** ~5 min
> **Terms introduced:** **functools.partial**, **partial object**

## What it looks like

```python
from functools import partial
import operator

double = partial(operator.mul, 2)   # "mul, with the first argument already set to 2"
double(21)                          # same as operator.mul(2, 21) → 42
```

## What it means

`partial(func, *args, **kwargs)` returns a **partial object**: a callable that remembers `func` and some arguments. Calling it calls `func` with the remembered arguments first, followed by any new ones. You can inspect what it holds through `.func`, `.args` and `.keywords`.

It's like a closure that only stores arguments, but it's a plain object, which matters for pickling: a partial pickles as "the function (by name) + the stored arguments".

## Why people use it

To adapt a function to an interface that wants fewer arguments. For example, a callback that will be called with one value, or a process target that needs a fixed setting: `Process(target=partial(work, config))`.

## How Kiln uses it

- forkcheck rule FA002 ([`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py)) looks *inside* `partial(...)`. `partial(nested_function, ...)` is still unpicklable, because pickling the partial means pickling the function it wraps by name. `partial(module_level_function, ...)` is fine.

## Gotchas

- **A partial is only as picklable as its pieces.** Wrapping a nested function or a lambda doesn't fix pickling; see the last line of "Try it".
- **Positional arguments are added in front.** `partial(f, 1)(2)` calls `f(1, 2)`, not `f(2, 1)`.
- **Keywords can be overridden.** `partial(int, base=16)("ff", base=10)` uses `base=10`.

## Try it

```text
>>> from functools import partial
>>> import operator
>>> double = partial(operator.mul, 2)
>>> double(21)
42
>>> double.func, double.args, double.keywords
(<built-in function mul>, (2,), {})
>>> int_from_hex = partial(int, base=16)
>>> int_from_hex("ff")
255
>>> import pickle
>>> len(pickle.dumps(double))
73
>>> def outer():
...     def inner(x):
...         return x + 1
...     return partial(inner, 41)
... 
>>> pickle.dumps(outer())
Traceback (most recent call last):
  File "<console>", line 1, in <module>
_pickle.PicklingError: Can't pickle local object <function outer.<locals>.inner at 0x74ffef4d3060>
when serializing tuple item 0
when serializing functools.partial reconstructor arguments
when serializing functools.partial object
```

(Generated from [`snippets/functools-partial.repl`](snippets/functools-partial.repl) on Python 3.15.1.)

The partial of `operator.mul` pickles (73 bytes). The partial of a nested function fails, and Python 3.15's error notes show the path: pickling the partial means pickling its arguments, the first of which is the local function.

## Check yourself

1. What does `partial(print, "kiln:")("ready")` print?
   <details><summary>Answer</summary>

   `kiln: ready`. The stored argument comes first, then the new one.

   </details>
2. Can `Process(target=partial(work, 5))` be used under spawn if `work` is defined at module level?
   <details><summary>Answer</summary>

   Yes. The partial pickles as "the function `work`, found by name, plus the argument 5", and both pieces are picklable.

   </details>
3. Why doesn't wrapping a nested function in `partial` make it picklable?
   <details><summary>Answer</summary>

   Pickling a partial pickles the function it holds, and a nested function can only be pickled by name, which fails because its qualname contains `<locals>`.

   </details>
