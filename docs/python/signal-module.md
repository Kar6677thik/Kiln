# The signal module

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [functions](functions.md), [enum](enum.md), [what-is-a-process](../concepts/what-is-a-process.md) · **Reading time:** ~8 min
> **Terms introduced:** **signal handler**, **SIG_IGN**, **SIG_DFL**, **wakeup fd**, **`Signals` enum**

## What it looks like

```python
import signal

def on_term(signum, frame):
    pending.append(signum)                       # do (almost) nothing here

signal.signal(signal.SIGTERM, on_term)          # call on_term when SIGTERM arrives
signal.signal(signal.SIGINT, signal.SIG_IGN)    # ignore Ctrl-C
```

## What it means

A **signal** is a tiny notification the operating system delivers to a process ("please stop", "a child exited", "Ctrl-C was pressed"). The full concept is in `docs/concepts/signals.md` (K1). The `signal` module is Python's way to react to them:

| Call | Effect |
|---|---|
| `signal.signal(sig, handler)` | from now on, when `sig` arrives, Python calls `handler(signum, frame)`. Returns the previous handler |
| `signal.SIG_IGN` | the special "ignore this signal" handler |
| `signal.SIG_DFL` | the special "do the default thing" handler (for SIGTERM: terminate) |
| `signal.getsignal(sig)` | what's installed now |
| `signal.Signals` | an **enum** of all signals: `signal.Signals(15)` is `SIGTERM`, `.name` gives `"SIGTERM"` |
| `signal.set_wakeup_fd(fd)` | whenever a signal arrives, also write its number as one byte to `fd` (see below) |
| `os.kill(pid, sig)` | *send* a signal (it's in the `os` module, see [os-module](os-module.md)) |

A **signal handler** receives the signal number and the current stack frame (which you usually ignore).

**When does the handler run?** Python's C code notes the signal immediately, but runs your Python handler a little later, *between two bytecode instructions of the main thread*. It can interrupt almost any line of your program.

**The wakeup fd.** `signal.set_wakeup_fd(w.fileno())` makes the C-level handler write one byte (the signal number) into a socket or pipe. An event loop that's waiting in `select()` on the other end wakes up at once. This is the modern form of the classic "self-pipe trick".

## Why people use it

Signals are how the outside world controls a server: `kill -HUP <pid>` means "reload", `kill -TERM` means "stop gracefully", Ctrl-C sends `SIGINT`. Handling them lets a program clean up instead of dying mid-write.

## How Kiln uses it

- [`src/kiln/master/signals.py`](../../src/kiln/master/signals.py): `SignalPipe` creates a socketpair, calls `signal.set_wakeup_fd(...)` on one end, and installs handlers that **only** append the signal number to a list. The master's loop reads the wakeup socket and handles each signal at a safe point (spec/04 §3).
- [`src/kiln/worker/main.py`](../../src/kiln/worker/main.py): each worker sets `SIGINT` to `SIG_IGN` (Ctrl-C is the master's business, [ADR-024](../decisions.md#adr-024)), resets the master's inherited handlers, and calls `signal.set_wakeup_fd(-1)` to stop writing into the master's wakeup pipe.
- [`src/kiln/master/supervisor.py`](../../src/kiln/master/supervisor.py) uses `signal.Signals(n).name` to log `signal=SIGKILL` instead of a bare 9.

## Gotchas

- **SIGKILL and SIGSTOP can't be caught or ignored.** `signal.signal(signal.SIGKILL, ...)` raises `OSError: [Errno 22] Invalid argument`. That's why the OOM killer's SIGKILL is so brutal.
- **Handlers only run in the main thread.** `signal.signal()` called from another thread raises `ValueError`.
- **Keep handlers tiny.** Since a handler can interrupt any line, doing real work in it can find data half-updated. Kiln's handlers only append to a list.
- **Handlers are inherited across `fork()`.** A forked child starts with the parent's handlers *and* its wakeup fd, which is why Kiln workers reset both first thing.

## Try it

```text
>>> import os, signal
>>> signal.SIGTERM
<Signals.SIGTERM: 15>
>>> int(signal.SIGTERM)
15
>>> signal.Signals(15)
<Signals.SIGTERM: 15>
>>> signal.Signals(9).name
'SIGKILL'
>>> received = []
>>> def on_term(signum, frame):
...     received.append(signal.Signals(signum).name)
... 
>>> old = signal.signal(signal.SIGTERM, on_term)
>>> os.kill(os.getpid(), signal.SIGTERM)
>>> received
['SIGTERM']
>>> signal.signal(signal.SIGINT, signal.SIG_IGN)
<built-in function default_int_handler>
>>> signal.getsignal(signal.SIGINT)
<Handlers.SIG_IGN: 1>
>>> signal.signal(signal.SIGKILL, on_term)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
OSError: [Errno 22] Invalid argument
>>> signal.signal(signal.SIGTERM, old)
<function on_term at 0x77945af1efb0>
```

(Generated from [`snippets/signal-module.repl`](snippets/signal-module.repl) on Python 3.15.1.)

The process sent SIGTERM *to itself* (`os.kill(os.getpid(), ...)`), and instead of dying, it ran `on_term`. Setting SIGINT to `SIG_IGN` returned the previous handler, Python's `default_int_handler`, which is what turns Ctrl-C into `KeyboardInterrupt`.

## Check yourself

1. What does `signal.signal(signal.SIGHUP, signal.SIG_IGN)` do?
   <details><summary>Answer</summary>

   From then on, SIGHUP sent to this process is ignored. Nothing happens when it arrives.

   </details>
2. Why do Kiln's master handlers only append to a list?
   <details><summary>Answer</summary>

   A handler runs between two bytecodes of whatever the main thread was doing, possibly in the middle of updating the job table. Doing the real work later, at a known safe point in the loop, avoids seeing data half-changed.

   </details>
3. A forked worker forgets to call `signal.set_wakeup_fd(-1)`. What happens when the worker gets a signal?
   <details><summary>Answer</summary>

   The worker writes the signal byte into the wakeup socket it inherited, which is connected to the *master's* loop. The master would wake up and see a signal that was never sent to it.

   </details>
