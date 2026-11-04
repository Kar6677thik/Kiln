# threading basics

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [functions](functions.md), [context-managers](context-managers.md) · **Reading time:** ~8 min
> **Terms introduced:** **thread**, **daemon thread**, **Lock**, **Event**, **GIL**, **race condition**

## What it looks like

```python
import threading

stop = threading.Event()
t = threading.Thread(target=heartbeat, args=(stop,), daemon=True, name="heartbeat")
t.start()      # heartbeat(stop) now runs at the same time as the code below
...
stop.set()     # ask it to finish
t.join()       # wait until it has
```

## What it means

A **thread** is a second line of execution inside the *same* process. Threads share all the process's memory: the same variables, objects and open files. (Separate *processes*, by contrast, have separate memory; see [what-is-a-process](../concepts/what-is-a-process.md).)

| Piece | Meaning |
|---|---|
| `threading.Thread(target=f, args=(x,))` | prepare a thread that will run `f(x)` |
| `.start()` | actually start it; `f` now runs alongside your code |
| `.join()` | wait until the thread's function returns |
| `daemon=True` | a **daemon thread** doesn't keep the program alive: when the main thread finishes, daemon threads are stopped with it |
| `threading.Event()` | a flag threads can share: `.set()`, `.is_set()`, and `.wait(timeout)`, which sleeps until the flag is set or the timeout passes |
| `threading.Lock()` | a **Lock**: only one thread at a time can hold it. `with lock:` takes it at the start of the block and releases it at the end |
| `threading.active_count()` | how many threads exist right now, the main one included |

**The GIL in one paragraph.** Standard CPython has a **Global Interpreter Lock (GIL)**: only one thread runs Python bytecode at any instant. The interpreter switches between threads every few milliseconds (`sys.getswitchinterval()`, 5 ms by default), and whenever a thread waits for I/O or sleeps. So threads give *concurrency* (taking turns) but, on the normal build, not *parallel* Python computation. The free-threaded build (3.15t) removes the GIL. K4 goes deep into all of this.

## Why people use it

Threads let one process do something "in the background" while the main code is busy, such as sending a heartbeat every second while a long job runs. Because threads share memory, they also share mistakes: if two threads change the same data at the same time, the result depends on timing. That's a **race condition**. Locks are the basic fix: they make sections of code take turns.

## How Kiln uses it

- [`src/kiln/worker/heartbeat.py`](../../src/kiln/worker/heartbeat.py): `HeartbeatThread` is a daemon thread that sends a `heartbeat` message every `heartbeat_interval_s`, even while the main thread is busy running a job (spec/04 §5.4).
- The same file's `ChannelWriter` holds a `threading.Lock`. Both the heartbeat thread and the main thread send frames over one socket; without the lock, half of one frame could interleave with half of another and corrupt the stream.
- The **master** is forbidden to start threads at all ([ADR-006](../decisions.md#adr-006)): before every `fork()` it asserts `threading.active_count() == 1`. Why: see `docs/concepts/fork-and-threads.md` (K1).

## Gotchas

- **Calling `t.run()` instead of `t.start()`** runs the function in the *current* thread. Nothing concurrent happens, and no error tells you.
- **Daemon threads die abruptly.** At exit they're stopped mid-work, without cleanup. Fine for a heartbeat, wrong for writing a file.
- **Forgetting to release a lock.** Using `lock.acquire()` and then raising an exception before `lock.release()` leaves it locked forever. Always use `with lock:`.
- **`time.sleep()` in a loop can't be interrupted.** `event.wait(interval)` sleeps the same amount but wakes up immediately when someone calls `event.set()`, which makes shutdown fast.

## Try it

```text
>>> import threading, time
>>> ticks = []
>>> def heartbeat(stop):
...     while not stop.is_set():
...         ticks.append(time.monotonic())
...         stop.wait(0.05)
... 
>>> stop = threading.Event()
>>> t = threading.Thread(target=heartbeat, args=(stop,), daemon=True, name="heartbeat")
>>> t.start()
>>> time.sleep(0.3)
>>> threading.active_count()
2
>>> stop.set()
>>> t.join()
>>> t.is_alive()
False
>>> len(ticks) >= 4
True
>>> lock = threading.Lock()
>>> with lock:
...     print("only one thread at a time in here")
... 
only one thread at a time in here
>>> lock.locked()
False
```

(Generated from [`snippets/threading-basics.repl`](snippets/threading-basics.repl) on Python 3.15.1.)

While the main thread slept for 0.3 s, the heartbeat thread kept ticking every 0.05 s; `active_count()` was 2 (main + heartbeat). After `stop.set()` and `join()`, the thread is gone. `lock.locked()` is `False` afterwards, because `with` released it.

## Check yourself

1. What's the difference between `t.start()` and `t.run()`?
   <details><summary>Answer</summary>

   `start()` creates a new thread that calls `run()`. Calling `run()` yourself executes the function in the current thread, with no concurrency.

   </details>
2. Why is Kiln's heartbeat thread a daemon?
   <details><summary>Answer</summary>

   So it never keeps a worker process alive on its own. When the worker's main code finishes, the heartbeat thread should simply stop too.

   </details>
3. Two threads both call `sock.sendall(frame)` on the same socket. What can go wrong, and what fixes it?
   <details><summary>Answer</summary>

   A large send can be split, so the bytes of two frames can interleave, and the receiver decodes garbage. A shared `Lock` around each send (Kiln's `ChannelWriter`) makes the two threads take turns.

   </details>
