# The `atexit` module

> **First used in:** see the [ledger](README.md) (milestone K3) · **Needs:** [functions](functions.md) · **Reading time:** ~6 min
> **Terms introduced:** **`atexit.register`**, **exit handler**

## What it looks like

```python
import atexit

def goodbye() -> None:
    print("cleaning up")

atexit.register(goodbye)                    # call goodbye() when the interpreter shuts down
atexit.register(print, "registered second") # extra arguments are passed to the function
```

## What it means

- `atexit.register(func, *args, **kwargs)` adds an **exit handler**: Python calls `func(*args, **kwargs)` during normal interpreter shutdown. It returns `func`, so it also works as a decorator (`@atexit.register`).
- Handlers run in **reverse order of registration** (last in, first out), so code that registered later, and probably depends on earlier setup, is cleaned up first.
- They run when the program ends normally: the end of the main script, `sys.exit()`, or an unhandled exception.
- They do **not** run after `os._exit()`, when the process is killed by a signal it doesn't handle (SIGKILL, default SIGTERM), or after a crash.
- `atexit.unregister(func)` removes every registration of `func`.

## Why people use it

To make sure cleanup happens however the program ends normally: flushing logs, deleting temp files or a pidfile, closing connections. Instagram also used it backwards: registering `os._exit` *first* so that it runs *last* and skips interpreter finalization ([finalization-and-atexit](../concepts/finalization-and-atexit.md)).

## How Kiln uses it

- `src/kiln/worker/__main__.py` (K3): with `worker.exit_via_os_exit = true`, a *spawned* worker registers an `os._exit` handler as its first action, so the app's own handlers still run but the final collection and module teardown are skipped.
- Forked workers never run atexit handlers at all: they end with `os._exit` ([ADR-028](../decisions.md#adr-028)), because the handlers they'd inherit belong to the master. K1's gate G1.7 tested exactly that.

## Gotchas

- **Order surprises:** the *first* handler you register runs *last*.
- **`os._exit` and SIGKILL skip every handler.** Never rely on atexit for anything that must survive a crash.
- **Forked children inherit the parent's handlers.** If a forked child shuts down normally, it runs the parent's cleanup (for example deleting the parent's pidfile). Use `os._exit` in forked children.
- **Output from a handler may be lost** if a later handler calls `os._exit` before stdout is flushed. Use `print(..., flush=True)` or flush first.

## Try it

[`snippets/atexit_1.py`](snippets/atexit_1.py) runs a program with two handlers, ending four ways:

```bash
(wsl) $ ./scripts/dev.sh run docs/python/snippets/atexit_1.py
```

```text
end of script  exit=  0  printed: handler B (registered second) | handler A (registered first)
sys.exit       exit=  3  printed: handler B (registered second) | handler A (registered first)
os._exit       exit=  3  printed: (nothing)
SIGKILL        exit= -9  printed: (nothing)
```

(Python 3.15.1.) B runs before A; `sys.exit(3)` still runs both; `os._exit(3)` and SIGKILL run neither. (`-9` is how `subprocess` reports "killed by signal 9", see [exit-status](../concepts/exit-status.md).)

## Check yourself

1. Predict the output: `atexit.register(print, 1); atexit.register(print, 2); atexit.register(print, 3)`, then normal exit.
   <details><summary>Answer</summary>

   `3`, `2`, `1`, one per line: last registered, first run.

   </details>
2. A worker is killed by the kernel's OOM killer. Does its atexit cleanup run?
   <details><summary>Answer</summary>

   No. The OOM killer sends SIGKILL, which can't be caught; the process ends without running any Python code.

   </details>
3. Why does Instagram's trick register `os._exit` *first*?
   <details><summary>Answer</summary>

   Handlers run in reverse order, so the first one registered runs last: every other handler gets to run, and only then does `os._exit` skip the final collection and teardown.

   </details>
