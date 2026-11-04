# `inspect` (looking at functions from code)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [functions](functions.md), [decorators](decorators.md) · **Reading time:** ~5 min
> **Terms introduced:** **inspect.iscoroutinefunction**, **coroutine object**, **inspect.signature**, **introspection**

## What it looks like

```python
import inspect

async def fetch(url): ...

inspect.iscoroutinefunction(fetch)     # True: it was defined with "async def"
inspect.signature(fetch)               # <Signature (url)>
```

## What it means

**Introspection** means a program examining its own code while it runs: "what kind of function is this?", "which parameters does it take?". The `inspect` module offers that for functions, classes and modules.

Kiln uses two of its tools:

- **inspect.iscoroutinefunction(f)** returns `True` if `f` was defined with `async def`. Calling such a function doesn't run its body. Instead it returns a **coroutine object**: a paused piece of work that only runs when an event loop drives it (`asyncio.run(...)`, or `await` inside another coroutine). See [asyncio-first-look](asyncio-first-look.md).
- **inspect.signature(f)** returns a `Signature` describing the parameters: their names, defaults and annotations. Its `.bind(*args, **kwargs)` checks whether a call *would* fit, without making it, and raises `TypeError` like the real call would.

## Why people use it

A framework receives functions written by someone else, and must treat them correctly. Kiln's task decorator receives both plain functions and `async def` ones. They must be *run* differently: call one directly, or hand the coroutine to an event loop. So the decorator asks once, at registration time, and remembers the answer.

## How Kiln uses it

[`src/kiln/app.py`](../../src/kiln/app.py): `App.task` records `is_async=inspect.iscoroutinefunction(func)` in each `TaskSpec` (spec/03 §2.1).

- **Sync executor:** in K1 it runs async tasks with `asyncio.run(...)`, once per job.
- **Manifest:** the `is_async` flag also travels in the worker's manifest (spec/02 §4.1), so later executors (K4) can choose the right path.

## Gotchas

- **Calling an async function doesn't run it.** `fetch("x")` returns a coroutine object immediately. If nobody ever awaits it, Python warns: `RuntimeWarning: coroutine 'fetch' was never awaited`. (The REPL below calls `coro.close()` to discard one politely.)
- **Use `inspect.iscoroutinefunction`, not `asyncio.iscoroutinefunction`.** The asyncio version is deprecated since 3.14.
- **Wrapping can hide async-ness.** A plain (non-async) decorator wrapped around an `async def` makes `iscoroutinefunction` return `False`. Kiln checks the function *before* wrapping anything; `@app.task` returns the original function unchanged.

## Try it

```text
>>> import inspect
>>> def add(a: int, b: int = 2) -> int:
...     return a + b
... 
>>> async def fetch(url):
...     return url
... 
>>> inspect.iscoroutinefunction(add)
False
>>> inspect.iscoroutinefunction(fetch)
True
>>> coro = fetch("x")
>>> inspect.iscoroutine(coro)
True
>>> coro.close()
>>> sig = inspect.signature(add)
>>> sig
<Signature (a: int, b: int = 2) -> int>
>>> list(sig.parameters)
['a', 'b']
>>> sig.parameters["b"].default
2
>>> sig.bind(1)
<BoundArguments (a=1)>
>>> sig.bind()
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …(frames inside inspect.py trimmed)…
TypeError: missing a required argument: 'a'
```

(Generated from [`snippets/inspect.repl`](snippets/inspect.repl) on Python 3.15.1.) `fetch("x")` produced a coroutine without running anything. `sig.bind()` failed exactly the way `add()` would, but without calling it.

## Check yourself

1. `async def job(): print("hi")`. What does `job()` print?
   <details><summary>Answer</summary>

   Nothing. It returns a coroutine object; the body (and the `print`) only runs when an event loop runs it, for example `asyncio.run(job())`.

   </details>
2. Why does Kiln check `iscoroutinefunction` when a task is registered, rather than when it runs?
   <details><summary>Answer</summary>

   The answer never changes for a given function, so checking once is enough. Recording it in the manifest also tells the master and future executors (K4) how each task must be run.

   </details>
3. What does `inspect.signature(add).bind(1, 2, 3)` do?
   <details><summary>Answer</summary>

   It raises `TypeError` ("too many positional arguments"), because `add` takes at most two. `bind` checks a call against the signature without running the function.

   </details>
