# Interpreter Shutdown: atexit, Finalization and os._exit

> **Milestone:** K3 · **Read before:** [exit-status](exit-status.md), [garbage-collector](garbage-collector.md), [fork](fork.md) · **Reading time:** ~11 min
> **Terms introduced:** **interpreter finalization**, **atexit handler**, **LIFO order**, **final collection**, **module teardown**

## One-sentence summary

When a Python program ends normally, the interpreter runs its **atexit handlers** newest-first, then **finalizes**: it runs a final garbage collection and tears down every module, calling finalizers as objects die; `os._exit()` skips all of it and ends the process at once.

## Why you should care

- **Instagram's shutdown trick.** Interpreter shutdown runs a collection over every object, which in a forked worker copies every page it touches, just before the process dies anyway. Instagram skipped it with `atexit.register(os._exit, 0)` registered *first* (research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)). K3's `worker.exit_via_os_exit` setting reproduces it.
- **Kiln's forked workers already end with `os._exit`** ([ADR-028](../decisions.md#adr-028), errata E-16), so they skip finalization by design. This primer explains why that's right for a forked child and what it costs.

## The mental model

**Analogy:** closing a shop. A normal close runs the checklist (atexit), cleans every shelf (the final collection) and returns every key (module teardown, object finalizers). `os._exit` is the fire alarm: everyone leaves *now*, nothing gets cleaned, and the landlord (the kernel) reclaims the building anyway.

**Where the analogy fails:** for a process, the "cleaning" is usually pointless: the kernel frees all memory and closes all fds at exit no matter what. The cleaning matters only for things outside the process: buffered output not yet written, temp files, flushed logs.

```
 normal end (end of script, sys.exit(), unhandled exception)
   ├─ atexit handlers, LAST registered runs FIRST  ── Instagram registers os._exit FIRST ──┐
   ├─ flush stdout/stderr                                                    so it runs LAST
   ├─ final garbage collection     (scans everything: copies pages in a forked worker)     │
   ├─ module teardown              (objects freed → __del__ / weakref.finalize)            │
   └─ process exits with the code                                                         │
                                                                                           ▼
 os._exit(code) ─────────────────────────────▶ process exits immediately, none of the above
```

## See it for yourself

[`snippets/finalization-and-atexit_1.py`](snippets/finalization-and-atexit_1.py) runs one tiny program in a fresh interpreter three ways. The program prints in `main`, registers two atexit handlers, and keeps a module-level object whose `__del__` prints:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/finalization-and-atexit_1.py
```

```text
normal end:
  main: done
  atexit: handler registered SECOND runs FIRST
  atexit: handler registered FIRST runs LAST
  finalizer: Resource.__del__ ran during shutdown
  exit code 0
os._exit(0) at the end:
  main: done
  exit code 0
Instagram's trick:
  main: done
  atexit: handler registered SECOND runs FIRST
  atexit: handler registered FIRST runs LAST
  exit code 0
```

(Python 3.15.1; 3.13.17 and 3.15.1t print exactly the same.) The program prints with `flush=True` everywhere, because `os._exit` throws away anything still sitting in an output buffer.

- **Normal end:** the handlers run in **LIFO order** (last in, first out), then the module-level object is finalized during teardown.
- **`os._exit(0)`:** neither the handlers nor the finalizer run. The process just stops.
- **Instagram's trick:** `atexit.register(os._exit, 0)` was registered *before* the other two handlers, so it runs *after* them: both handlers still print, and then `os._exit` cuts off interpreter finalization, so the finalizer (and, in a real app, the final garbage collection) never runs.

## How it really works

- **atexit** ([python/atexit](../python/atexit.md)) keeps a list of callables. At normal shutdown (`Py_FinalizeEx` in `Python/pylifecycle.c`) they run in reverse order of registration. They run after the main program ends, after `sys.exit()`, and after an unhandled exception. They don't run after `os._exit()`, after a signal that kills the process (SIGKILL; also SIGTERM unless a handler turns it into an exception), or after a crash.
- **Then finalization:** stdout and stderr are flushed, threads are joined, a final garbage collection runs, and modules are torn down; objects freed along the way run `__del__` and `weakref.finalize` callbacks. In a forked worker this is when the "final GC copies everything" cost of Instagram's story occurs.
- **`os._exit(code)`** calls the C `_exit(2)` system call: the kernel frees memory, closes fds and reports `code` to the parent. No Python code runs, and no buffers are flushed.
- **`sys.exit(code)`** is not an immediate exit: it raises `SystemExit`, which unwinds the stack (running `finally:` blocks) and then goes through normal shutdown.
- **Why a forked child must use `os._exit`** ([ADR-028](../decisions.md#adr-028)). A forked worker is a copy of the master, including the master's atexit handlers, open files and buffered output. If the child ran a normal shutdown, it would run the *master's* atexit handlers (for example deleting the master's pidfile) and flush the master's unflushed buffers a second time. Kiln's `finalize_and_exit` flushes the child's own streams, then calls `os._exit` (K1 gate G1.7 verified the handlers don't run).
- **Why the trick runs "last".** Registering `os._exit` first means every handler registered later (by the app and its libraries) still runs, and only then does `os._exit` cut off the final collection and teardown.

## How Kiln uses it

- [`src/kiln/worker/main.py`](../../src/kiln/worker/main.py) → `finalize_and_exit`: every *forked* worker ends with `os._exit(code)` after flushing (ADR-028). For them `worker.exit_via_os_exit` changes nothing, and the master logs that once.
- `src/kiln/worker/__main__.py`: a *spawned* worker is a fresh interpreter that ends with `sys.exit`, so finalization runs. With `worker.exit_via_os_exit = true`, it registers an `os._exit` handler as its very first action, so the app's own atexit handlers still run but the final collection and teardown are skipped (K3 spec §5.6; experiment (d) measures the shutdown time saved).
- Code tag: `CONCEPT(finalization-and-atexit)`.

## Common misconceptions

- ❌ **Myth:** "`sys.exit()` exits immediately."
  ✅ **Reality:** it raises `SystemExit`; `finally` blocks, atexit handlers and finalization all still run.
- ❌ **Myth:** "atexit handlers always run when the process ends."
  ✅ **Reality:** not after `os._exit`, SIGKILL, a crash, or an unhandled fatal signal.
- ❌ **Myth:** "skipping finalization leaks memory."
  ✅ **Reality:** the kernel reclaims all of a process's memory and fds when it exits. What you can lose is *unflushed output* and cleanup that touches the outside world.

## Check yourself

1. Predict: `atexit.register(print, "A"); atexit.register(print, "B")`, then the script ends normally. What's printed, in what order?
   <details><summary>Answer</summary>

   `B` then `A`. Handlers run last-in-first-out.

   </details>
2. In the snippet's program, what would the `os._exit(0)` run print if `say` were plain `print` (no `flush=True`) and the output went to a pipe?
   <details><summary>Answer</summary>

   Nothing at all, not even `main: done`. When stdout is a pipe, `print` writes into a buffer that's only flushed at shutdown, and `os._exit` ends the process without flushing it. That's why Kiln's `finalize_and_exit` flushes stdout and stderr before calling `os._exit`.

   </details>
3. Why would a forked Kiln worker that called `sys.exit()` be dangerous?
   <details><summary>Answer</summary>

   Normal shutdown would run the atexit handlers it inherited from the master, flush the master's copied buffers again, and run a final collection over the master's copied objects. ADR-028 makes every forked worker end with `os._exit`.

   </details>
4. Instagram registered `os._exit` *first*. What would go wrong if it were registered last?
   <details><summary>Answer</summary>

   It would run first, before every other handler, so the app's and libraries' handlers (flushing logs, closing connections) would never run.

   </details>

## Go deeper

- [Python docs: `atexit`](https://docs.python.org/3.15/library/atexit.html), [`os._exit`](https://docs.python.org/3.15/library/os.html#os._exit), [`sys.exit`](https://docs.python.org/3.15/library/sys.html#sys.exit).
- CPython source: `Python/pylifecycle.c` (`Py_FinalizeEx`: the order of shutdown steps).
- `man 2 _exit`.
- [ADR-028](../decisions.md#adr-028) and [errata E-16](../research/errata.md#e-16-forked-workers-skip-interpreter-finalization-anyway).
