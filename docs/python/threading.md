# threading (Thread, join, current_thread, Event, Barrier)

> **First used in:** see the [ledger](README.md) (milestone K4) · **Needs:** [threading-basics](threading-basics.md), [functions](functions.md) · **Reading time:** ~8 min
> **Terms introduced:** **`threading.Thread`**, **`start` / `join`**, **`threading.current_thread`**, **`threading.Event`**, **`threading.Barrier`**

## What it looks like

```python
import threading

def work(n):
    print(n, threading.current_thread().name)

t = threading.Thread(target=work, args=(1,), name="helper")
t.start()     # begin running work(1) in a new thread
t.join()      # wait here until it has finished
```

## What it means

- **`threading.Thread(target=f, args=(...), name=...)`** creates a thread object; nothing runs yet. `target` is the function, `args` its positional arguments as a tuple, `name` a label for logs and debuggers.
- **`start()`** asks the operating system for a new thread, which calls `target(*args)`. `start()` returns immediately: the new thread and the current one now run side by side ([concepts/threads](../concepts/threads.md)).
- **`join()`** blocks until the thread's function has returned. Without it, the code after `start()` might run before, during or after the thread's work.
- **`threading.current_thread()`** returns the `Thread` object for whichever thread is running this line; `.name` is `'MainThread'` for the program's first thread.
- **`threading.Event`** is a flag threads can wait for: `event.wait()` blocks until another thread calls `event.set()`.
- **`threading.Barrier(n)`** makes threads wait until `n` of them have called `barrier.wait()`, then releases them all at once. K4's race demos use it so the threads start their loops at the same instant.
- **`daemon=True`** marks a thread that shouldn't keep the program alive ([concepts/threads](../concepts/threads.md)).

## Why people use it

To run something *while* the main code continues: a heartbeat during a long job, many network waits at once, or (on the free-threaded build) computation on several cores. [threading-basics](threading-basics.md) (K1) introduced `Thread` and `Event` for the heartbeat; this note adds what K4 needs.

## How Kiln uses it

- `src/kiln/worker/heartbeat.py`: a daemon `Thread` that loops until an `Event` is set.
- `src/kiln/worker/executors/asyncio_exec.py`: one `Thread` named `kiln-loop` runs the event loop forever.
- `labs/k4/race_demo.py`: `Barrier(T)` lines up `T` racing threads; `Thread` and `join` run and collect them.

## Gotchas

- **Calling `run()` instead of `start()`** runs the function *in the current thread*, with no new thread at all.
- **Forgetting `join()`** before reading results: the thread may not have finished writing them.
- **`args=(1)` is not a tuple**: it's the number 1 in brackets. Write `args=(1,)`.
- **Exceptions in a thread don't reach the caller.** They're printed (through `threading.excepthook`) and the thread ends; `join()` doesn't re-raise them. `concurrent.futures` does ([concurrent-futures](concurrent-futures.md)).

## Try it

```text
>>> import threading, time
>>> threading.current_thread().name
'MainThread'
>>> results = []
>>> def work(n):
...     results.append((n, threading.current_thread().name))
...
>>> t = threading.Thread(target=work, args=(1,), name="helper")
>>> t.start()
>>> t.join()
>>> results
[(1, 'helper')]
>>> t.is_alive()
False
>>> started = threading.Event()
>>> def waiter():
...     started.wait()
...     results.append("event seen")
...
>>> w = threading.Thread(target=waiter)
>>> w.start()
>>> w.is_alive()
True
>>> started.set()
>>> w.join()
>>> results[-1]
'event seen'
>>> barrier = threading.Barrier(3)
>>> order = []
>>> def racer(i):
...     barrier.wait()
...     order.append(i)
...
>>> ts = [threading.Thread(target=racer, args=(i,)) for i in range(3)]
>>> for x in ts: x.start()
...
>>> for x in ts: x.join()
...
>>> sorted(order)
[0, 1, 2]
>>> threading.Thread(target=time.sleep, args=(0,), daemon=True).daemon
True
```

(Generated with `scripts/repl_transcript.py docs/python/snippets/threading.repl` on 3.15.1.)

## Check yourself

1. What's the difference between `t.start()` and `t.run()`?
   <details><summary>Answer</summary>

   `start()` creates a new thread that calls `run()`, and returns immediately. Calling `run()` yourself just calls the target function in the current thread, synchronously.

   </details>
2. Why do K4's race demos use a `Barrier` instead of just starting the threads one after another?
   <details><summary>Answer</summary>

   Starting a thread takes time; the first thread could finish its loop before the last one starts, so they'd never overlap and the race would never show. A barrier holds them all until the last one arrives, then releases them together.

   </details>
3. Predict: `w = threading.Thread(target=started.wait); w.start(); w.join(timeout=0.1); w.is_alive()` when nobody sets the event.
   <details><summary>Answer</summary>

   `True`. `join(timeout=0.1)` returns after 0.1 s even though the thread hasn't finished; `is_alive()` tells you which happened.

   </details>
