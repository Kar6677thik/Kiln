# asyncio.run, the event loop's lifecycle, and crossing between threads

> **First used in:** see the [ledger](README.md) (milestone K4) · **Needs:** [async-await-basics](async-await-basics.md), [threading](threading.md), [concurrent-futures](concurrent-futures.md) · **Reading time:** ~9 min
> **Terms introduced:** **`asyncio.run`**, **event loop lifecycle**, **`asyncio.run_coroutine_threadsafe`**, **`asyncio.to_thread`**, **`asyncio.timeout`**, **`loop.call_soon_threadsafe`**

## What it looks like

```python
import asyncio, threading

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, name="kiln-loop", daemon=True).start()

async def answer():
    await asyncio.sleep(0.01)
    return 42

future = asyncio.run_coroutine_threadsafe(answer(), loop)   # from ANY other thread
print(future.result(timeout=1))                             # 42
loop.call_soon_threadsafe(loop.stop)
```

## What it means

- **`asyncio.run(coro)`** is the simple, one-shot way: create a new event loop, run `coro` to completion, cancel anything left over, shut down the loop's default thread pool, close the loop. One call, one loop, then it's gone (the **event loop lifecycle**: create → run → close).
- A **long-lived loop in its own thread** is the other pattern: `asyncio.new_event_loop()` and `loop.run_forever()` on a dedicated thread, so the loop keeps running while other threads hand it work.
- **`asyncio.run_coroutine_threadsafe(coro, loop)`** is how *another* thread hands a coroutine to that loop. It returns a `concurrent.futures.Future` (not an asyncio one), so the calling thread can `.result(timeout=...)` it like any executor future ([concurrent-futures](concurrent-futures.md)).
- **`await asyncio.to_thread(f, *args)`** runs a *blocking* function in the loop's default thread pool, so the loop stays free; the coroutine resumes when `f` returns.
- **`async with asyncio.timeout(s):`** cancels the block's `await` if it takes longer than `s` seconds and raises `TimeoutError` ([concepts/cpu-vs-io-bound](../concepts/cpu-vs-io-bound.md#cooperative-cancellation)).
- **`loop.call_soon_threadsafe(fn)`** is the thread-safe way to ask the loop to call a plain function, here `loop.stop`.

## Why people use it

`asyncio.run` for programs that are async from top to bottom. The loop-in-a-thread pattern when the rest of the program isn't async: a Kiln worker's main thread reads its socket with ordinary blocking calls and passes async jobs to a loop thread.

## How Kiln uses it

- `src/kiln/worker/executors/asyncio_exec.py`: starts a `kiln-loop` thread with `run_forever`; the worker's main thread calls `run_coroutine_threadsafe(self._run_job(run), loop)` for each job. Async tasks run under `asyncio.timeout(timeout_s)`; sync tasks via `asyncio.to_thread` on a pool sized to the slot count.
- `src/kiln/worker/executors/sync.py` and `threads.py`: an async task runs with `asyncio.run(...)`, a fresh loop per job.

## Gotchas

- **`asyncio.run` can't be called from a thread that's already running a loop**, such as inside a coroutine; it raises `RuntimeError`.
- **Loop objects aren't thread-safe.** From another thread, use only `run_coroutine_threadsafe` and `call_soon_threadsafe`, never `loop.create_task` or `loop.stop()` directly.
- **`to_thread` work can't be cancelled.** A timeout cancels the `await`, but the thread keeps running the function to the end.
- **The default thread pool is shared and sized `min(32, cpu_count + 4)`.** Kiln replaces it with `loop.set_default_executor(ThreadPoolExecutor(n))` so it matches the worker's capacity.

## Try it

```text
>>> import asyncio, threading, time
>>> async def answer():
...     await asyncio.sleep(0.01)
...     return 42
...
>>> asyncio.run(answer())
42
>>> loop = asyncio.new_event_loop()
>>> t = threading.Thread(target=loop.run_forever, name="kiln-loop", daemon=True)
>>> t.start()
>>> fut = asyncio.run_coroutine_threadsafe(answer(), loop)
>>> type(fut).__name__
'Future'
>>> fut.result(timeout=1)
42
>>> async def in_a_thread():
...     return await asyncio.to_thread(lambda: threading.current_thread().name)
...
>>> asyncio.run_coroutine_threadsafe(in_a_thread(), loop).result(timeout=1).startswith("asyncio_")
True
>>> async def too_slow():
...     async with asyncio.timeout(0.05):
...         await asyncio.sleep(1)
...
>>> asyncio.run_coroutine_threadsafe(too_slow(), loop).exception(timeout=1)
TimeoutError()
>>> loop.call_soon_threadsafe(loop.stop)
<_ThreadSafeHandle BaseEventLoop.stop()>
>>> t.join()
>>> loop.close()
>>> loop.is_closed()
True
```

(Generated with `scripts/repl_transcript.py docs/python/snippets/asyncio-run.repl` on 3.15.1. The default pool's threads are named `asyncio_0`, `asyncio_1`, …)

## Check yourself

1. Why does Kiln's asyncio executor use `run_coroutine_threadsafe` instead of `asyncio.run` for each job?
   <details><summary>Answer</summary>

   `asyncio.run` would create and destroy a loop per job and block the caller until it finished, one job at a time. One long-lived loop on its own thread can run many jobs concurrently, and `run_coroutine_threadsafe` is the safe way for the worker's main thread to hand jobs to it.

   </details>
2. What type does `run_coroutine_threadsafe` return, and why that one?
   <details><summary>Answer</summary>

   A `concurrent.futures.Future`. The caller is a normal (non-async) thread, so it needs a future it can block on with `.result()`, which asyncio's own futures don't allow from outside the loop.

   </details>
3. Predict: `async with asyncio.timeout(0.1): await asyncio.to_thread(time.sleep, 1)`. When is `TimeoutError` raised, and when does the thread stop?
   <details><summary>Answer</summary>

   `TimeoutError` after 0.1 s; the thread keeps sleeping until 1 s (measured on 3.15.1: the thread finished 1.0 s after the start). Threads can't be cancelled.

   </details>
