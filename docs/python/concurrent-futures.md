# concurrent.futures (executors and futures)

> **First used in:** see the [ledger](README.md) (milestone K4) · **Needs:** [threading](threading.md), [exceptions](exceptions.md) · **Reading time:** ~8 min
> **Terms introduced:** **executor**, **`ThreadPoolExecutor`**, **`submit`**, **`Future`**, **`result()`**, **`add_done_callback`**, **`shutdown(wait=)`**

## What it looks like

```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=2, thread_name_prefix="kiln-job") as pool:
    future = pool.submit(pow, 2, 10)   # schedule pow(2, 10) on a pool thread
    print(future.result())             # wait for it: 1024
```

## What it means

- An **executor** owns a set of workers and a queue of work items. **`ThreadPoolExecutor(max_workers=N)`** uses N threads, created as needed; `thread_name_prefix` names them `kiln-job_0`, `kiln-job_1`, … (gate G4.10). `ProcessPoolExecutor` and `InterpreterPoolExecutor` (3.14+, [concepts/subinterpreters](../concepts/subinterpreters.md)) have the same interface.
- **`pool.submit(f, *args, **kwargs)`** puts the call on the queue and returns immediately with a **`Future`**: an object standing for a result that doesn't exist yet.
- **`future.result()`** waits until the call has finished and returns its value, or **re-raises its exception** in the waiting thread. `future.exception()` returns the exception instead of raising it; `future.done()` checks without waiting.
- **`future.add_done_callback(fn)`** asks for `fn(future)` to be called when the future finishes (on the thread that finished it).
- **`pool.map(f, items)`** submits one call per item and yields results in order.
- **`pool.shutdown(wait=True)`** stops accepting work and waits for queued and running calls; the `with` block calls it for you. After shutdown, `submit` raises `RuntimeError`.

## Why people use it

Starting a thread per task, collecting results and passing exceptions back by hand is fiddly. An executor does it in a few lines, reuses threads, and limits how many run at once.

## How Kiln uses it

- `src/kiln/worker/executors/threads.py`: a `ThreadPoolExecutor(max_workers=worker.executor_concurrency, thread_name_prefix="kiln-job")`; each job's result is reported from a done-callback.
- `src/kiln/worker/executors/asyncio_exec.py`: replaces the event loop's default executor with a `ThreadPoolExecutor` sized to the slot count, so `asyncio.to_thread` can't exceed capacity.
- `src/kiln/worker/executors/interpreters.py`: an `InterpreterPoolExecutor` running a module-level shim.

## Gotchas

- **Exceptions hide in the future.** If nobody calls `result()` or `exception()`, a failed task fails silently.
- **`result()` blocks.** Calling it on the event loop's thread, or in a done-callback for another future in the same pool, can stall or deadlock.
- **Default sizes surprise.** `ThreadPoolExecutor()` with no `max_workers` uses `min(32, os.cpu_count() + 4)` threads; Kiln always passes an explicit size.

## Try it

```text
>>> from concurrent.futures import ThreadPoolExecutor
>>> import threading, time
>>> pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kiln-job")
>>> future = pool.submit(pow, 2, 10)
>>> future
<Future at 0x7a005ab801a0 state=finished returned int>
>>> future.result()
1024
>>> future.done()
True
>>> pool.submit(lambda: threading.current_thread().name).result()
'kiln-job_0'
>>> bad = pool.submit(int, "not a number")
>>> bad.exception()
ValueError("invalid literal for int() with base 10: 'not a number'")
>>> bad.result()
Traceback (most recent call last):
  ...
ValueError: invalid literal for int() with base 10: 'not a number'
>>> list(pool.map(lambda x: x * x, [1, 2, 3]))
[1, 4, 9]
>>> seen = []
>>> f = pool.submit(time.sleep, 0.05)
>>> f.add_done_callback(lambda fut: seen.append(fut.done()))
>>> f.result()
>>> seen
[True]
>>> pool.shutdown(wait=True)
>>> pool.submit(pow, 2, 2)
Traceback (most recent call last):
  ...
RuntimeError: cannot schedule new futures after shutdown
```

(Generated with `scripts/repl_transcript.py docs/python/snippets/concurrent-futures.repl` on 3.15.1. The two tracebacks are shortened with `...`; their last lines are exactly as printed. The address in `<Future at 0x…>` changes every run.)

## Check yourself

1. What happens to an exception raised inside a submitted function?
   <details><summary>Answer</summary>

   It's stored in the `Future`. `future.result()` re-raises it in whichever thread calls `result()`, and `future.exception()` returns it. If nobody asks, it's never shown.

   </details>
2. Why does Kiln give `ThreadPoolExecutor` an explicit `max_workers`?
   <details><summary>Answer</summary>

   The pool size must equal the capacity the worker reports to the master (`worker.executor_concurrency`). The default (`min(32, cpu_count + 4)`) would let more jobs run than the master thinks are running.

   </details>
3. Predict: `with ThreadPoolExecutor(1) as p: f = p.submit(time.sleep, 1)`, then `f.done()` right after the `with` block.
   <details><summary>Answer</summary>

   `True`. Leaving the `with` block calls `shutdown(wait=True)`, which waits for the sleep to finish.

   </details>
