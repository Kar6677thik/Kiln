# asyncio: a first look

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [functions](functions.md), [inspect](inspect.md) · **Reading time:** ~8 min
> **Terms introduced:** **async def**, **await**, **coroutine**, **asyncio.run**, **asyncio.gather**, **asyncio streams**

This note gives you **just enough** to read [`src/kiln/bench/load.py`](../../src/kiln/bench/load.py), the K1 load generator. asyncio gets proper, deep coverage in K4 and K5 (the gateway).

## What it looks like

```python
import asyncio

async def greet(name):
    await asyncio.sleep(0.1)          # pause here; let other work run meanwhile
    return f"hello {name}"

async def main():
    return await asyncio.gather(greet("a"), greet("b"))   # run both at the same time

print(asyncio.run(main()))            # ['hello a', 'hello b']
```

## What it means

| Piece | Meaning |
|---|---|
| **async def** | defines a coroutine function. Calling it returns a **coroutine**, a paused task, without running the body |
| **await** x | "pause *this* coroutine until `x` is finished, and let the event loop run others in the meantime". Only allowed inside `async def` |
| `asyncio.sleep(0.1)` | a pause that doesn't block the thread, unlike `time.sleep`. While one coroutine sleeps, others can run |
| **asyncio.run**(coro) | starts an event loop, runs `coro` to completion, closes the loop, returns the result. Typically called once, at the top of a program |
| **asyncio.gather**(a, b, c) | runs several coroutines concurrently and returns their results as a list, **in the order given** |
| `asyncio.open_connection(host, port)` | **asyncio streams**: connects and returns a `(reader, writer)` pair. `await reader.readline()`, `writer.write(data)` and `await writer.drain()` replace blocking `recv` and `send` |

The idea in one sentence: an **event loop** (like the master's selector loop in spec/01 §4, see [selectors-module](selectors-module.md)) runs many coroutines on **one thread**, switching between them every time one says `await`. Nothing runs in parallel. Waiting simply overlaps.

## Why people use it

A load generator wants to keep, say, 64 connections busy at once. With blocking sockets that needs 64 threads. With asyncio it's 64 coroutines on a single thread: each one sends a request and `await`s the reply, and while it waits, the others send theirs. Clients are a natural fit for asyncio.

The master *doesn't* use asyncio, on purpose (ADR-006): it's written directly on `selectors`, so you can see what asyncio does underneath.

## How Kiln uses it

[`src/kiln/bench/load.py`](../../src/kiln/bench/load.py) is the first and only asyncio code in K1:

- **Closed loop:** each simulated client is a coroutine that opens a KP/1 connection with `asyncio.open_connection`, then repeatedly sends `submit` and awaits the job's result. `asyncio.gather` runs C of them concurrently.
- **Open loop** (`--rate`): coroutines are started on a fixed schedule, whether or not earlier replies have arrived.

## Gotchas

- **Forgetting `await`.** `greet("a")` without `await` creates a coroutine that never runs (Python warns: "coroutine ... was never awaited").
- **A blocking call freezes everything.** `time.sleep(1)` inside a coroutine stops the whole event loop for a second, and every other coroutine with it. Use `await asyncio.sleep(1)`. K5 builds a detector for exactly this mistake.
- **`asyncio.run` inside a running loop fails.** It's meant to be called once, from ordinary code, not from inside another coroutine.
- **Concurrency is not parallelism.** `gather` overlaps *waiting*. Three CPU-heavy coroutines still run one after another on the single thread (K4 explains threads and processes for that).

## Try it

```text
>>> import asyncio
>>> async def greet(name):
...     await asyncio.sleep(0.1)
...     return f"hello {name}"
... 
>>> coro = greet("kiln")
>>> coro
<coroutine object greet at 0x7ad864b26740>
>>> coro.close()
>>> asyncio.run(greet("kiln"))
'hello kiln'
>>> async def main():
...     return await asyncio.gather(greet("a"), greet("b"), greet("c"))
... 
>>> asyncio.run(main())
['hello a', 'hello b', 'hello c']
```

(Generated from [`snippets/asyncio-first-look.repl`](snippets/asyncio-first-look.repl) on Python 3.15.1.) Calling `greet("kiln")` only created a coroutine object; `asyncio.run` actually ran it.

**Seeing the overlap.** [`snippets/asyncio-first-look_1.py`](snippets/asyncio-first-look_1.py) awaits three 0.2-second sleeps, first one after another, then with `gather`:

```text
one_after_another  ['a', 'b', 'c']  about 0.6 s
all_together       ['a', 'b', 'c']  about 0.2 s
```

**Streams.** [`snippets/asyncio-first-look_2.py`](snippets/asyncio-first-look_2.py) runs an echo server and a client in one program with `asyncio.start_server` and `asyncio.open_connection`, and prints `echo: hello kiln`.

## Check yourself

1. Two coroutines each `await asyncio.sleep(1)`. Run with `gather`, about how long does it take? And if one of them uses `time.sleep(1)` instead?
   <details><summary>Answer</summary>

   With `gather` and `asyncio.sleep`, about 1 second, because the waits overlap. With `time.sleep(1)` in one of them, about 2 seconds: `time.sleep` blocks the whole thread, so nothing else runs during it.

   </details>
2. What does `asyncio.gather(a(), b())` return?
   <details><summary>Answer</summary>

   A list of the results, in the order the coroutines were given (`[result_of_a, result_of_b]`), even if `b` finished first.

   </details>
3. Why can 64 asyncio clients run on one thread, while 64 blocking clients need 64 threads?
   <details><summary>Answer</summary>

   A blocking `recv` holds its thread hostage until data arrives. An `await` gives control back to the event loop, which runs another coroutine meanwhile. The thread is only busy when there's actual work to do.

   </details>
