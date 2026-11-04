# Closures (functions inside functions)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [functions](functions.md), [decorators](decorators.md) · **Reading time:** ~7 min
> **Terms introduced:** **nested function**, **closure**, **`nonlocal`**, **`__qualname__`**, **late binding**

## What it looks like

```python
def make_counter():
    count = 0
    def increment():        # a nested function
        nonlocal count      # "count" belongs to make_counter, not to increment
        count += 1
        return count
    return increment        # the returned function remembers count: a closure
```

## What it means

- A **nested function** is a `def` inside another function. It's created *each time* the outer function runs.
- If the inner function uses a variable of the outer function, Python keeps that variable alive for it, even after the outer function has returned. The function plus its remembered variables is a **closure**.
- **`nonlocal count`** lets the inner function *assign* to the outer variable. Without it, `count += 1` would try to create a new local variable and fail.
- A nested function's **`__qualname__`** (qualified name) records where it lives: `make_counter.<locals>.increment`. The `<locals>` part means "a local variable of a call to `make_counter`".

## Why people use it

Closures package behaviour together with a little private state, without writing a class. Decorators are built from them: the wrapper is a nested function that remembers the function it wraps ([decorators](decorators.md)).

## How Kiln uses it

- `App.task` in [`src/kiln/app.py`](../../src/kiln/app.py) uses a nested `register` function. It remembers the decorator's options (`name`, `timeout_s`...) until it receives the function to register.
- In K2, closures are mostly a **hazard to detect**. A nested function can't be pickled by name, so it can't be the target of a spawn/forkserver process. That's forkcheck's FA002 ([`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py)), found with `symtable` ([ast-and-symtable](../concepts/ast-and-symtable.md)). The oslo.concurrency case in [`corpus/real/`](../../corpus/real/) is exactly this.

## Gotchas

- **Late binding.** A closure remembers the *variable*, not its value at the time. `[lambda: i for i in range(3)]` gives three functions that all return the *final* `i` (2). The usual fix is a default argument, `lambda i=i: i`, which captures the value now.
- **Nested functions can't be pickled.** Their qualname contains `<locals>`, which no import can find. That's fine under fork, and fails under spawn/forkserver.
- **Forgetting `nonlocal`.** Assigning to an outer variable without it raises `UnboundLocalError`.

## Try it

```text
>>> def make_counter():
...     count = 0
...     def increment():
...         nonlocal count
...         count += 1
...         return count
...     return increment
... 
>>> counter = make_counter()
>>> counter(), counter(), counter()
(1, 2, 3)
>>> counter.__qualname__
'make_counter.<locals>.increment'
>>> counter.__code__.co_freevars
('count',)
>>> counter.__closure__[0].cell_contents
3
>>> callbacks = [lambda: i for i in range(3)]
>>> [f() for f in callbacks]
[2, 2, 2]
>>> callbacks = [lambda i=i: i for i in range(3)]
>>> [f() for f in callbacks]
[0, 1, 2]
>>> import pickle
>>> pickle.dumps(counter)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
_pickle.PicklingError: Can't pickle local object <function make_counter.<locals>.increment at 0x7a31a911f060>
```

(Generated from [`snippets/closures.repl`](snippets/closures.repl) on Python 3.15.1.)

`co_freevars` lists the variables borrowed from outside (`count`). `__closure__` holds the "cells" that keep them alive, and the cell's value is 3 after three calls. The last line is the K2 lesson: a closure can't be pickled.

## Check yourself

1. Why does `counter()` keep counting up even though `make_counter` already returned?
   <details><summary>Answer</summary>

   The closure keeps a reference to the `count` variable (a cell), so it survives the outer call, and each call to `increment` updates it.

   </details>
2. Predict: `fs = [lambda: n * 10 for n in (1, 2)]; [f() for f in fs]`.
   <details><summary>Answer</summary>

   `[20, 20]`, because of late binding: both lambdas read `n` when called, and by then it's 2. `lambda n=n: n * 10` gives `[10, 20]`.

   </details>
3. Why does `multiprocessing` under spawn refuse a nested function as a `Process` target?
   <details><summary>Answer</summary>

   The target must be pickled, and functions pickle by module + qualname. A qualname containing `<locals>` can't be looked up in a fresh process, so pickle raises `PicklingError`.

   </details>
