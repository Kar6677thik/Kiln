# async / await basics

> **First used in:** see the [ledger](README.md) (milestone K4) · **Needs:** [asyncio-first-look](asyncio-first-look.md), [functions](functions.md) · **Reading time:** ~7 min
> **Terms introduced:** **coroutine function**, **coroutine object**, **`await`**, **`asyncio.gather`**, **"async is not parallel"**

## What it looks like

```python
import asyncio

async def hello():               # a coroutine function
    await asyncio.sleep(0.1)     # pause here; the event loop runs other tasks meanwhile
    return "hi"

print(asyncio.run(hello()))      # hi
```

## What it means

- **`async def`** defines a **coroutine function**. Calling it does *not* run its body: `hello()` returns a **coroutine object**, a paused computation.
- Something has to drive the coroutine: an event loop. **`asyncio.run(coro)`** creates a loop, runs `coro` until it returns, closes the loop, and returns the value ([asyncio-run](asyncio-run.md)).
- **`await x`** (only allowed inside `async def`) pauses this coroutine until `x` is ready. While it's paused, the loop runs other coroutines. That's the whole trick: waiting without blocking the thread.
- **`asyncio.gather(a, b, c)`** runs several coroutines concurrently and returns their results in order.
- **"Async is not parallel."** Everything runs on one thread. Coroutines take turns at `await` points. Three `asyncio.sleep(0.2)` take 0.2 s together; three `time.sleep(0.2)` inside coroutines take 0.6 s, because `time.sleep` never gives the loop a turn.

## Why people use it

To wait on many things at once (sockets, timers, other services) with one thread and very little memory per waiting task. It's how K5's gateway serves many connections, and how K4's `asyncio` executor runs many `labs.async_sleep` jobs in one worker.

## How Kiln uses it

- `src/kiln/worker/executors/asyncio_exec.py`: async tasks run as coroutines on the worker's `kiln-loop` thread, each under `asyncio.timeout`.
- `src/kiln/labs/tasks.py`: `labs.async_sleep` (cooperative, `await asyncio.sleep`) and `labs.block_loop` (an `async def` that calls `time.sleep`: the uncooperative case).
- `src/kiln/bench/load.py` (K1): the load generator drives hundreds of client connections with one event loop.

## Gotchas

- **Forgetting `await`.** `asyncio.sleep(1)` without `await` creates a coroutine object and throws it away; Python warns "coroutine ... was never awaited".
- **Blocking calls inside `async def`.** `time.sleep`, `requests.get` or heavy computation stop *every* coroutine on the loop (the `three_busy` example below).
- **Calling a coroutine function from normal code.** `hello()` alone does nothing; use `asyncio.run(hello())` at the top level.

## Try it

```text
>>> import asyncio, time
>>> async def hello():
...     await asyncio.sleep(0.1)
...     return "hi"
...
>>> coro = hello()
>>> type(coro).__name__
'coroutine'
>>> asyncio.run(coro)
'hi'
>>> async def three_sleeps():
...     started = time.perf_counter()
...     await asyncio.gather(asyncio.sleep(0.2), asyncio.sleep(0.2), asyncio.sleep(0.2))
...     return round(time.perf_counter() - started, 1)
...
>>> asyncio.run(three_sleeps())
0.2
>>> async def three_busy():
...     started = time.perf_counter()
...     async def busy():
...         time.sleep(0.2)
...     await asyncio.gather(busy(), busy(), busy())
...     return round(time.perf_counter() - started, 1)
...
>>> asyncio.run(three_busy())
0.6
```

(Generated with `scripts/repl_transcript.py docs/python/snippets/async-await-basics.repl` on 3.15.1.)

## Check yourself

1. What does `hello()` return, and when does its body run?
   <details><summary>Answer</summary>

   A coroutine object. The body runs only when something drives it: `await hello()` inside another coroutine, or `asyncio.run(hello())`.

   </details>
2. Why did `three_busy` take 0.6 s although it used `gather`?
   <details><summary>Answer</summary>

   `time.sleep` doesn't `await`, so each `busy()` keeps the loop's only thread until it finishes. The three ran one after the other.

   </details>
3. Predict: `async def many(): await asyncio.gather(*(asyncio.sleep(0.1) for _ in range(1000)))`, then `asyncio.run(many())`. Roughly how long?
   <details><summary>Answer</summary>

   About 0.1 s (0.11 s measured on 3.15.1): all 1,000 sleeps wait at the same time on one thread. Note the wrapper: `asyncio.run(asyncio.gather(...))` without it fails on 3.15.1 with `RuntimeError: There is no current event loop in thread 'MainThread'`, because `gather` needs a running loop and `asyncio.run` needs a coroutine.

   </details>
